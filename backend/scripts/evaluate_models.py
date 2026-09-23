#!/usr/bin/env python3
"""Bounded model evaluation for ASR + translation profiles.

Explicit real-model step. Not part of `make check`.
Produces JSON under docs/benchmark-artifacts/ and prints a short summary.

Corpus: backend/testdata/eval/corpus-v1.json (synthetic TTS + human-checked refs).
"""

from __future__ import annotations

import argparse
import json
import os
import re
import resource
import shutil
import subprocess
import sys
import tempfile
import time
import unicodedata
import wave
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import numpy as np

SCRIPT_DIR = Path(__file__).resolve().parent
BACKEND_DIR = SCRIPT_DIR.parent
PROJECT_ROOT = BACKEND_DIR.parent
ARTIFACTS_DIR = PROJECT_ROOT / "docs" / "benchmark-artifacts"
CORPUS_PATH = BACKEND_DIR / "testdata" / "eval" / "corpus-v1.json"
WAV_DIR = BACKEND_DIR / "testdata" / "eval" / "wav"

for import_path in (SCRIPT_DIR, BACKEND_DIR):
    if str(import_path) not in sys.path:
        sys.path.insert(0, str(import_path))


def rss_mb() -> float:
    return resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024.0


def git_metadata() -> dict[str, str | None]:
    def run(*args: str) -> str | None:
        try:
            completed = subprocess.run(
                ["git", "-C", str(PROJECT_ROOT), *args],
                check=True,
                capture_output=True,
                text=True,
            )
            return completed.stdout.strip()
        except (subprocess.CalledProcessError, FileNotFoundError):
            return None

    return {
        "commit": run("rev-parse", "HEAD"),
        "commit_short": run("rev-parse", "--short", "HEAD"),
        "branch": run("rev-parse", "--abbrev-ref", "HEAD"),
    }


def normalize_text(text: str) -> str:
    text = unicodedata.normalize("NFKC", text or "")
    text = text.lower().replace("’", "'")
    text = re.sub(r"[^\w\s']+", " ", text, flags=re.UNICODE)
    return " ".join(text.split())


def tokenize(text: str) -> list[str]:
    return normalize_text(text).split()


def edit_distance(a: list[str], b: list[str]) -> int:
    if not a:
        return len(b)
    if not b:
        return len(a)
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, start=1):
        cur = [i]
        for j, cb in enumerate(b, start=1):
            cost = 0 if ca == cb else 1
            cur.append(min(cur[j - 1] + 1, prev[j] + 1, prev[j - 1] + cost))
        prev = cur
    return prev[-1]


def wer(reference: str, hypothesis: str) -> float:
    ref = tokenize(reference)
    hyp = tokenize(hypothesis)
    if not ref:
        return 0.0 if not hyp else 1.0
    return edit_distance(ref, hyp) / len(ref)


def cer(reference: str, hypothesis: str) -> float:
    ref = list(normalize_text(reference).replace(" ", ""))
    hyp = list(normalize_text(hypothesis).replace(" ", ""))
    if not ref:
        return 0.0 if not hyp else 1.0
    return edit_distance(ref, hyp) / len(ref)


def _ngrams(tokens: list[str], n: int) -> dict[str, int]:
    counts: dict[str, int] = {}
    if len(tokens) < n:
        return counts
    for i in range(len(tokens) - n + 1):
        gram = " ".join(tokens[i : i + n])
        counts[gram] = counts.get(gram, 0) + 1
    return counts


def chrf(reference: str, hypothesis: str, max_n: int = 6, beta: float = 2.0) -> float:
    """Character n-gram F-score (chrF) without external deps."""
    ref_chars = list(normalize_text(reference).replace(" ", ""))
    hyp_chars = list(normalize_text(hypothesis).replace(" ", ""))
    if not ref_chars and not hyp_chars:
        return 1.0
    if not ref_chars or not hyp_chars:
        return 0.0
    precisions: list[float] = []
    recalls: list[float] = []
    for n in range(1, max_n + 1):
        ref_n = _ngrams(ref_chars, n)
        hyp_n = _ngrams(hyp_chars, n)
        overlap = sum(min(hyp_n.get(g, 0), c) for g, c in ref_n.items())
        hyp_total = sum(hyp_n.values()) or 1
        ref_total = sum(ref_n.values()) or 1
        precisions.append(overlap / hyp_total)
        recalls.append(overlap / ref_total)
    p = sum(precisions) / len(precisions)
    r = sum(recalls) / len(recalls)
    if p == 0.0 and r == 0.0:
        return 0.0
    beta2 = beta * beta
    return (1 + beta2) * p * r / (beta2 * p + r)


def write_pcm16_wav(path: Path, samples: np.ndarray, sample_rate: int = 16000) -> None:
    pcm16 = (np.clip(samples, -1.0, 1.0) * 32767.0).astype("<i2")
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(sample_rate)
        handle.writeframes(pcm16.tobytes())


def read_wav(path: Path) -> tuple[int, np.ndarray]:
    with wave.open(str(path), "rb") as handle:
        channels = handle.getnchannels()
        width = handle.getsampwidth()
        rate = handle.getframerate()
        frames = handle.readframes(handle.getnframes())
    if width != 2:
        raise ValueError("Only 16-bit PCM WAV supported")
    samples = np.frombuffer(frames, dtype="<i2").astype(np.float32) / 32768.0
    if channels > 1:
        samples = samples.reshape(-1, channels).mean(axis=1)
    return rate, samples.astype(np.float32, copy=False)


def synthesize_item(item: dict[str, Any], output: Path) -> dict[str, Any]:
    espeak = shutil.which("espeak-ng") or shutil.which("espeak")
    ffmpeg = shutil.which("ffmpeg")
    if not espeak or not ffmpeg:
        raise RuntimeError("espeak(-ng) and ffmpeg required to synthesize eval audio")
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="eval-tts-") as temp_dir:
        temp = Path(temp_dir)
        raw = temp / "raw.wav"
        subprocess.run(
            [
                espeak,
                "-v",
                str(item.get("voice") or "nl"),
                "-s",
                str(int(item.get("rate") or 140)),
                "-w",
                str(raw),
                item["reference_source"],
            ],
            check=True,
            capture_output=True,
        )
        subprocess.run(
            [
                ffmpeg,
                "-y",
                "-i",
                str(raw),
                "-ac",
                "1",
                "-ar",
                "16000",
                "-sample_fmt",
                "s16",
                str(output),
            ],
            check=True,
            capture_output=True,
        )
    rate, samples = read_wav(output)
    return {
        "id": item["id"],
        "path": str(output),
        "sample_rate": rate,
        "duration_seconds": round(float(samples.size) / rate, 3),
        "generator": "espeak",
        "voice": item.get("voice") or "nl",
        "reference_source": item["reference_source"],
        "reference_translation_en": item["reference_translation_en"],
        "split": item.get("split"),
        "tags": item.get("tags") or [],
        "quality_note": item.get("quality_note")
        or "Synthetic TTS — relative ranking only, not human-speech WER.",
    }


def ensure_corpus_wavs(corpus: dict[str, Any], *, force: bool = False) -> list[dict[str, Any]]:
    fixtures: list[dict[str, Any]] = []
    for item in corpus["items"]:
        wav_path = WAV_DIR / f"{item['id']}.wav"
        meta_path = WAV_DIR / f"{item['id']}.json"
        if force or not wav_path.exists() or not meta_path.exists():
            meta = synthesize_item(item, wav_path)
            meta_path.write_text(json.dumps(meta, indent=2) + "\n", encoding="utf-8")
        else:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
        fixtures.append(meta)
    return fixtures


def load_asr(model_name: str, device: str, compute_type: str):
    from app.asr import get_asr_engine

    os.environ["ASR_MODEL"] = model_name
    os.environ["ASR_DEVICE"] = device
    os.environ["ASR_COMPUTE_TYPE"] = compute_type
    os.environ["LOCAL_MODELS_ONLY"] = os.getenv("LOCAL_MODELS_ONLY", "1")
    get_asr_engine.cache_clear()
    engine = get_asr_engine()
    started = time.perf_counter()
    engine.warmup()
    warmup_ms = (time.perf_counter() - started) * 1000
    return engine, warmup_ms


def load_translation(
    family: str,
    model: str,
    tokenizer: str,
    device: str = "cpu",
    *,
    allow_download: bool = False,
):
    from app.translator import get_translation_engine

    os.environ["TRANSLATION_ENGINE"] = "ctranslate2"
    os.environ["TRANSLATION_MODEL_FAMILY"] = family
    os.environ["TRANSLATION_MODEL"] = model
    os.environ["TRANSLATION_TOKENIZER"] = tokenizer
    os.environ["TRANSLATION_DEVICE"] = device
    os.environ["TRANSLATION_COMPUTE_TYPE"] = "int8"
    os.environ["TRANSLATION_CACHE_ITEMS"] = "0"
    os.environ["TRANSLATION_CACHE_BACKEND"] = "memory"
    if family == "marian":
        os.environ["TRANSLATION_SOURCE_LANGUAGE"] = "nl"
        os.environ["TRANSLATION_TARGET_LANGUAGE"] = "en"
    os.environ["LOCAL_MODELS_ONLY"] = "0" if allow_download else os.getenv("LOCAL_MODELS_ONLY", "1")
    get_translation_engine.cache_clear()
    engine = get_translation_engine()
    started = time.perf_counter()
    engine.warmup()
    warmup_ms = (time.perf_counter() - started) * 1000
    return engine, warmup_ms


def resolve_model_path(model: str) -> str:
    path = Path(model)
    if path.exists():
        return str(path.resolve())
    candidate = BACKEND_DIR / model
    if candidate.exists():
        return str(candidate.resolve())
    return model


def resolve_tokenizer_path(tokenizer: str) -> str:
    path = Path(tokenizer)
    if path.exists():
        return str(path.resolve())
    candidate = BACKEND_DIR / tokenizer
    if candidate.exists():
        return str(candidate.resolve())
    return tokenizer


def evaluate_asr(
    fixtures: list[dict[str, Any]],
    *,
    model_name: str,
    mode: str,
    device: str,
    compute_type: str,
) -> dict[str, Any]:
    engine, warmup_ms = load_asr(model_name, device, compute_type)
    per_item: list[dict[str, Any]] = []
    wers: list[float] = []
    cers: list[float] = []
    rtfs: list[float] = []
    latencies: list[float] = []

    for fixture in fixtures:
        _rate, audio = read_wav(Path(fixture["path"]))
        started = time.perf_counter()
        result = engine.transcribe_result(audio, language="nl", mode=mode, inference_kind="final")
        elapsed_ms = (time.perf_counter() - started) * 1000
        hyp = result.text.strip()
        item_wer = wer(fixture["reference_source"], hyp)
        item_cer = cer(fixture["reference_source"], hyp)
        duration = float(fixture["duration_seconds"])
        rtf = (elapsed_ms / 1000.0) / duration if duration > 0 else 0.0
        wers.append(item_wer)
        cers.append(item_cer)
        rtfs.append(rtf)
        latencies.append(elapsed_ms)
        per_item.append(
            {
                "id": fixture["id"],
                "split": fixture.get("split"),
                "reference": fixture["reference_source"],
                "hypothesis": hyp,
                "wer": round(item_wer, 4),
                "cer": round(item_cer, 4),
                "latency_ms": round(elapsed_ms, 1),
                "rtf": round(rtf, 3),
                "words": len(result.words or []),
            }
        )

    def mean(values: list[float]) -> float:
        return sum(values) / len(values) if values else 0.0

    return {
        "role": "asr",
        "model": model_name,
        "mode": mode,
        "device": device,
        "compute_type": compute_type,
        "warmup_ms": round(warmup_ms, 1),
        "peak_rss_mb": round(rss_mb(), 1),
        "n": len(per_item),
        "wer_mean": round(mean(wers), 4),
        "cer_mean": round(mean(cers), 4),
        "rtf_mean": round(mean(rtfs), 3),
        "latency_ms_mean": round(mean(latencies), 1),
        "items": per_item,
        "info": engine.info() if hasattr(engine, "info") else {},
    }


def evaluate_translation(
    pairs: list[dict[str, Any]],
    *,
    family: str,
    model: str,
    tokenizer: str,
    label: str,
) -> dict[str, Any]:
    resolved_model = resolve_model_path(model)
    if not Path(resolved_model).exists():
        return {
            "role": "translation",
            "label": label,
            "family": family,
            "model": resolved_model,
            "status": "skipped_missing_weights",
        }

    tok = resolve_tokenizer_path(tokenizer)
    allow_download = family == "marian" and not Path(tok).exists()
    try:
        engine, warmup_ms = load_translation(
            family,
            resolved_model,
            tok,
            allow_download=allow_download,
        )
    except Exception as exc:
        return {
            "role": "translation",
            "label": label,
            "family": family,
            "model": resolved_model,
            "tokenizer": tok,
            "status": "failed",
            "error": f"{type(exc).__name__}: {exc}",
        }

    per_item: list[dict[str, Any]] = []
    chrfs: list[float] = []
    cers: list[float] = []
    latencies: list[float] = []

    for pair in pairs:
        source = pair["source_nl"]
        reference = pair["reference_en"]
        started = time.perf_counter()
        translated = engine.translate(source, source_language="nl", target_language="en")
        elapsed_ms = (time.perf_counter() - started) * 1000
        hyp = (translated or "").strip()
        score = chrf(reference, hyp)
        item_cer = cer(reference, hyp)
        chrfs.append(score)
        cers.append(item_cer)
        latencies.append(elapsed_ms)
        per_item.append(
            {
                "id": pair["id"],
                "source": source,
                "reference": reference,
                "hypothesis": hyp,
                "chrf": round(score, 4),
                "cer": round(item_cer, 4),
                "latency_ms": round(elapsed_ms, 1),
            }
        )

    def mean(values: list[float]) -> float:
        return sum(values) / len(values) if values else 0.0

    return {
        "role": "translation",
        "label": label,
        "family": family,
        "model": resolved_model,
        "tokenizer": tok,
        "status": "ok",
        "warmup_ms": round(warmup_ms, 1),
        "peak_rss_mb": round(rss_mb(), 1),
        "n": len(per_item),
        "chrf_mean": round(mean(chrfs), 4),
        "cer_mean": round(mean(cers), 4),
        "latency_ms_mean": round(mean(latencies), 1),
        "items": per_item,
        "info": engine.info() if hasattr(engine, "info") else {},
        "capabilities": engine.capabilities() if hasattr(engine, "capabilities") else {},
    }


def translate_asr_outputs(
    asr_result: dict[str, Any],
    fixtures: list[dict[str, Any]],
    *,
    family: str,
    model: str,
    tokenizer: str,
    label: str,
) -> dict[str, Any]:
    """MT over ASR hypotheses — separates recognition errors from MT errors."""
    fixture_by_id = {f["id"]: f for f in fixtures}
    pairs = []
    for item in asr_result.get("items") or []:
        fixture = fixture_by_id.get(item["id"])
        if not fixture:
            continue
        pairs.append(
            {
                "id": f"asr->{item['id']}",
                "source_nl": item["hypothesis"],
                "reference_en": fixture["reference_translation_en"],
                "asr_reference_nl": fixture["reference_source"],
                "asr_wer": item["wer"],
            }
        )
    result = evaluate_translation(pairs, family=family, model=model, tokenizer=tokenizer, label=label)
    if result.get("status") == "ok":
        result["pipeline"] = "asr_hypothesis_to_en"
        result["upstream_asr_model"] = asr_result.get("model")
        for out, pair in zip(result.get("items") or [], pairs, strict=False):
            out["asr_wer"] = pair["asr_wer"]
            out["asr_reference_nl"] = pair["asr_reference_nl"]
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--corpus", type=Path, default=CORPUS_PATH)
    parser.add_argument("--generate-audio-only", action="store_true")
    parser.add_argument("--force-audio", action="store_true")
    parser.add_argument("--asr-models", default="small,large-v3-turbo")
    parser.add_argument("--asr-mode", default="balanced")
    parser.add_argument("--device", default="cpu")
    parser.add_argument("--compute-type", default="int8")
    parser.add_argument("--skip-asr", action="store_true")
    parser.add_argument("--skip-translation", action="store_true")
    parser.add_argument("--output", type=Path, default=None)
    args = parser.parse_args()

    corpus = json.loads(args.corpus.read_text(encoding="utf-8"))
    fixtures = ensure_corpus_wavs(corpus, force=args.force_audio)
    if args.generate_audio_only:
        print(json.dumps({"ok": True, "fixtures": [f["id"] for f in fixtures]}, indent=2))
        return 0

    os.chdir(BACKEND_DIR)
    results: dict[str, Any] = {
        "generated_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        "git": git_metadata(),
        "host": {
            "device": args.device,
            "compute_type": args.compute_type,
            "python": sys.version.split()[0],
            "peak_rss_mb_start": round(rss_mb(), 1),
        },
        "corpus": {
            "path": str(args.corpus),
            "name": corpus.get("name"),
            "version": corpus.get("version"),
            "n_speech_items": len(fixtures),
            "n_mt_pairs": len(corpus.get("reference_mt_pairs") or []),
            "quality_note": corpus.get("purpose"),
        },
        "asr": [],
        "translation_reference": [],
        "translation_from_asr": [],
        "unmeasured": corpus.get("unmeasured_conditions") or [],
        "candidates_not_run": [],
    }

    if not args.skip_asr:
        for model_name in [m.strip() for m in args.asr_models.split(",") if m.strip()]:
            print(f"==> ASR {model_name} ({args.device}/{args.compute_type}, mode={args.asr_mode})", flush=True)
            try:
                result = evaluate_asr(
                    fixtures,
                    model_name=model_name,
                    mode=args.asr_mode,
                    device=args.device,
                    compute_type=args.compute_type,
                )
                results["asr"].append(result)
                print(
                    f"    WER={result['wer_mean']:.3f} CER={result['cer_mean']:.3f} "
                    f"RTF={result['rtf_mean']:.3f} latency_ms={result['latency_ms_mean']:.0f}",
                    flush=True,
                )
            except Exception as exc:
                results["asr"].append(
                    {
                        "role": "asr",
                        "model": model_name,
                        "status": "failed",
                        "error": f"{type(exc).__name__}: {exc}",
                    }
                )
                print(f"    FAILED: {exc}", flush=True)

    if not args.skip_translation:
        mt_profiles = [
            {
                "label": "nllb-200-distilled-600m-ct2",
                "family": "nllb",
                "model": "models/nllb-200-distilled-600m-ct2",
                "tokenizer": "models/nllb-200-distilled-600m-tokenizer",
            },
            {
                "label": "opus-mt-nl-en-ct2",
                "family": "marian",
                "model": "models/opus-mt-nl-en-ct2",
                "tokenizer": "Helsinki-NLP/opus-mt-nl-en",
            },
        ]
        for profile in mt_profiles:
            print(f"==> MT {profile['label']} on reference NL", flush=True)
            result = evaluate_translation(
                corpus["reference_mt_pairs"],
                family=profile["family"],
                model=profile["model"],
                tokenizer=profile["tokenizer"],
                label=profile["label"],
            )
            results["translation_reference"].append(result)
            if result.get("status") == "ok":
                print(
                    f"    chrF={result['chrf_mean']:.3f} CER={result['cer_mean']:.3f} "
                    f"latency_ms={result['latency_ms_mean']:.0f}",
                    flush=True,
                )
            else:
                print(f"    {result.get('status')}", flush=True)

        # Pipeline MT on best available ASR hypotheses (prefer turbo if present).
        asr_for_pipeline = None
        for candidate in results["asr"]:
            if candidate.get("items"):
                asr_for_pipeline = candidate
                if "turbo" in str(candidate.get("model")):
                    break
        if asr_for_pipeline and asr_for_pipeline.get("items"):
            nllb = mt_profiles[0]
            print(f"==> MT {nllb['label']} on ASR hypotheses ({asr_for_pipeline['model']})", flush=True)
            pipeline = translate_asr_outputs(
                asr_for_pipeline,
                fixtures,
                family=nllb["family"],
                model=nllb["model"],
                tokenizer=nllb["tokenizer"],
                label=f"{nllb['label']}-from-asr",
            )
            results["translation_from_asr"].append(pipeline)
            if pipeline.get("status") == "ok":
                print(
                    f"    chrF={pipeline['chrf_mean']:.3f} (includes upstream ASR errors)",
                    flush=True,
                )

    results["candidates_not_run"] = [
        {
            "id": "whisper-large-v3",
            "reason": "Weights not cached on this host; compare with --asr-models large-v3 after download.",
        },
        {
            "id": "parakeet-tdt-0.6b-v3",
            "reason": "Requires NeMo runtime + new ASR adapter; not integrated. Officially supports Dutch.",
            "source": "https://huggingface.co/nvidia/parakeet-tdt-0.6b-v3",
        },
        {
            "id": "translategemma-4b-it",
            "reason": "Needs Gemma-compatible adapter, template, license acceptance, and GPU headroom.",
            "source": "https://huggingface.co/google/translategemma-4b-it",
        },
        {
            "id": "m2m100-418m-ct2",
            "reason": "Artifact absent under backend/models/; launcher default only.",
        },
        {
            "id": "distil-whisper-large-v3",
            "reason": "English-only — not a Dutch ASR replacement.",
            "source": "https://huggingface.co/distil-whisper/distil-large-v3",
        },
    ]

    ARTIFACTS_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    output = args.output or (ARTIFACTS_DIR / f"model-eval-{stamp}.json")
    latest = ARTIFACTS_DIR / "model-eval-latest.json"
    output.write_text(json.dumps(results, indent=2) + "\n", encoding="utf-8")
    latest.write_text(json.dumps(results, indent=2) + "\n", encoding="utf-8")
    print(f"Wrote {output}")
    print(f"Wrote {latest}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
