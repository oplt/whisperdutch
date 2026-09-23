#!/usr/bin/env python3
"""Representative speech baseline for the live subtitle pipeline.

Unlike phase1 synthetic-tone harness:
- Uses Dutch speech audio (espeak-generated fixture or supplied WAV)
- Feeds SpeechSegmenter then InferenceRuntime (queue wait + service time)
- Records time-to-first-partial / first-final, steady-state finals, RTF,
  cold vs warm translation cache, cue counts, RSS

Explicit real-model step. Not part of `make check`.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import platform
import resource
import shutil
import subprocess
import sys
import tempfile
import time
import wave
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import numpy as np

SCRIPT_DIR = Path(__file__).resolve().parent
BACKEND_DIR = SCRIPT_DIR.parent
PROJECT_ROOT = BACKEND_DIR.parent
ARTIFACTS_DIR = PROJECT_ROOT / "docs" / "benchmark-artifacts"
FIXTURES_DIR = BACKEND_DIR / "testdata" / "speech"

for import_path in (SCRIPT_DIR, BACKEND_DIR):
    if str(import_path) not in sys.path:
        sys.path.insert(0, str(import_path))

from benchmark_pipeline import summary  # noqa: E402

# Canonical Dutch lines for TTS fixture (also reference for later WER work).
DUTCH_REFERENCE_LINES = (
    "Goedemorgen, dit is een korte Nederlandse testzin.",
    "De trein naar Amsterdam vertrekt over vijf minuten.",
    "Ik wil graag een kopje koffie met melk, alstublieft.",
)


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
        "subject": run("log", "-1", "--format=%s"),
    }


def write_pcm16_wav(path: Path, samples: np.ndarray, sample_rate: int = 16000) -> Path:
    pcm = np.clip(samples, -1.0, 1.0)
    pcm16 = (pcm * 32767.0).astype("<i2")
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(sample_rate)
        handle.writeframes(pcm16.tobytes())
    return path


def read_wav(path: Path) -> tuple[int, np.ndarray]:
    with wave.open(str(path), "rb") as handle:
        channels = handle.getnchannels()
        sample_width = handle.getsampwidth()
        sample_rate = handle.getframerate()
        frames = handle.readframes(handle.getnframes())
    if sample_width != 2:
        raise ValueError("Only 16-bit PCM WAV is supported")
    samples = np.frombuffer(frames, dtype="<i2").astype(np.float32) / 32768.0
    if channels > 1:
        samples = samples.reshape(-1, channels).mean(axis=1)
    return sample_rate, samples.astype(np.float32, copy=False)


def generate_dutch_speech_wav(output_path: Path) -> dict[str, Any]:
    """Synthesize Dutch speech via espeak (+ ffmpeg resample) with known transcript."""
    espeak = shutil.which("espeak-ng") or shutil.which("espeak")
    ffmpeg = shutil.which("ffmpeg")
    if not espeak or not ffmpeg:
        raise RuntimeError("espeak(-ng) and ffmpeg required to generate speech fixture")

    output_path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="nl-speech-") as temp_dir:
        temp = Path(temp_dir)
        raw_parts: list[Path] = []
        for index, line in enumerate(DUTCH_REFERENCE_LINES):
            part = temp / f"part-{index}.wav"
            subprocess.run(
                [espeak, "-v", "nl", "-s", "140", "-w", str(part), line],
                check=True,
                capture_output=True,
            )
            raw_parts.append(part)
            # ~0.6 s silence between sentences for segmenter endpointing
            silence = temp / f"silence-{index}.wav"
            subprocess.run(
                [
                    ffmpeg,
                    "-y",
                    "-f",
                    "lavfi",
                    "-i",
                    "anullsrc=r=22050:cl=mono",
                    "-t",
                    "0.6",
                    str(silence),
                ],
                check=True,
                capture_output=True,
            )
            raw_parts.append(silence)

        concat_list = temp / "concat.txt"
        concat_list.write_text("\n".join(f"file '{path}'" for path in raw_parts) + "\n", encoding="utf-8")
        joined = temp / "joined.wav"
        subprocess.run(
            ["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", str(concat_list), "-c", "copy", str(joined)],
            check=True,
            capture_output=True,
        )
        subprocess.run(
            [
                "ffmpeg",
                "-y",
                "-i",
                str(joined),
                "-ac",
                "1",
                "-ar",
                "16000",
                "-sample_fmt",
                "s16",
                str(output_path),
            ],
            check=True,
            capture_output=True,
        )

    sample_rate, audio = read_wav(output_path)
    return {
        "path": str(output_path),
        "generator": "espeak",
        "voice": "nl",
        "sample_rate": sample_rate,
        "duration_seconds": round(len(audio) / sample_rate, 3),
        "reference_transcript": " ".join(DUTCH_REFERENCE_LINES),
        "reference_lines": list(DUTCH_REFERENCE_LINES),
        "quality_note": (
            "Synthetic TTS — useful for cue/latency path coverage, not for claiming human-speech WER."
        ),
    }


def ensure_speech_fixture(wav: Path | None) -> tuple[Path, dict[str, Any]]:
    if wav is not None:
        sample_rate, audio = read_wav(wav)
        if sample_rate != 16000:
            raise ValueError("Benchmark expects 16 kHz WAV")
        meta = {
            "path": str(wav),
            "generator": "user-supplied",
            "sample_rate": sample_rate,
            "duration_seconds": round(len(audio) / sample_rate, 3),
            "reference_transcript": None,
            "quality_note": "User-supplied audio; provide reference separately for WER.",
        }
        return wav, meta

    fixture = FIXTURES_DIR / "dutch-espeak-baseline.wav"
    meta_path = FIXTURES_DIR / "dutch-espeak-baseline.json"
    if not fixture.is_file():
        meta = generate_dutch_speech_wav(fixture)
        meta_path.write_text(json.dumps(meta, indent=2), encoding="utf-8")
    else:
        meta = json.loads(meta_path.read_text(encoding="utf-8")) if meta_path.is_file() else {"path": str(fixture)}
    return fixture, meta


async def run_streaming_session(
    audio: np.ndarray,
    sample_rate: int,
    *,
    mode: str,
    source_lang: str,
    target_lang: str,
    chunk_ms: int,
    partial_interval_ms: int,
) -> dict[str, Any]:
    from app.audio import SpeechSegmenter
    from app.inference_runtime import AsrPriority, InferenceRuntime
    from app.pipeline import transcribe_and_collect_sentences, transcribe_partial, translate_many_sentences
    from app.schemas import ClientConfig
    from app.sentences import SentenceAssembler

    config = ClientConfig(
        sample_rate=sample_rate,
        source_lang=source_lang,
        target_lang=target_lang,
        mode=mode,
    )
    segmenter = SpeechSegmenter(sample_rate=sample_rate)
    segmenter.set_mode(mode)
    assembler = SentenceAssembler()
    runtime = InferenceRuntime()
    await runtime.start()

    chunk = max(1, int(sample_rate * (chunk_ms / 1000.0)))
    partial_every = max(1, int(partial_interval_ms / chunk_ms))

    session_started = time.perf_counter()
    audio_cursor_samples = 0
    chunk_index = 0

    partial_events: list[dict[str, Any]] = []
    final_events: list[dict[str, Any]] = []
    stage_totals_ms = {
        "segmentation_feed_ms": 0.0,
        "asr_queue_wait_ms": 0.0,
        "asr_service_ms": 0.0,
        "sentence_assembly_ms": 0.0,
        "translation_queue_wait_ms": 0.0,
        "translation_service_ms": 0.0,
    }
    recognized_sentences: list[str] = []
    translations: list[str] = []
    cue_count = 0
    dropped_empty_finals = 0

    time_to_first_partial_ms: float | None = None
    time_to_first_final_ms: float | None = None

    async def run_partial(snapshot: np.ndarray, audio_end_seconds: float) -> None:
        nonlocal time_to_first_partial_ms
        enqueued = time.perf_counter()

        def work() -> tuple[str, dict[str, Any]]:
            return transcribe_partial(snapshot, config, prompt=None)

        text, meta = await runtime.run_asr(
            AsrPriority.PARTIAL,
            work,
            session_id="baseline-1",
        )
        done = time.perf_counter()
        service_ms = float(meta.get("latency_ms") or ((done - enqueued) * 1000.0))
        wall_ms = (done - enqueued) * 1000.0
        if time_to_first_partial_ms is None and (text or "").strip():
            time_to_first_partial_ms = (done - session_started) * 1000.0
        stage_totals_ms["asr_queue_wait_ms"] += max(0.0, wall_ms - service_ms)
        stage_totals_ms["asr_service_ms"] += service_ms
        partial_events.append(
            {
                "audio_end_seconds": round(audio_end_seconds, 3),
                "wall_ms": round(wall_ms, 3),
                "service_ms": round(service_ms, 3),
                "queue_wait_ms": round(max(0.0, wall_ms - service_ms), 3),
                "text_len": len(text or ""),
                "has_text": bool((text or "").strip()),
            }
        )

    async def run_final(segment: np.ndarray, audio_end_seconds: float, reason: str | None) -> None:
        nonlocal time_to_first_final_ms, cue_count, dropped_empty_finals
        audio_seconds = len(segment) / sample_rate
        enqueued = time.perf_counter()

        def work() -> tuple[list[str], dict[str, Any]]:
            assembly_started = time.perf_counter()
            sentences, meta = transcribe_and_collect_sentences(
                segment,
                config,
                assembler,
                force=True,
                time_offset_seconds=max(0.0, audio_end_seconds - audio_seconds),
            )
            meta = dict(meta)
            meta["sentence_assembly_ms"] = (time.perf_counter() - assembly_started) * 1000.0
            return sentences, meta

        sentences, meta = await runtime.run_asr(
            AsrPriority.FINAL,
            work,
            session_id="baseline-1",
        )
        asr_done = time.perf_counter()
        asr_wall_ms = (asr_done - enqueued) * 1000.0
        asr_service_ms = float(meta.get("asr_latency_ms") or asr_wall_ms)
        stage_totals_ms["asr_queue_wait_ms"] += max(0.0, asr_wall_ms - asr_service_ms)
        stage_totals_ms["asr_service_ms"] += asr_service_ms
        stage_totals_ms["sentence_assembly_ms"] += float(meta.get("sentence_assembly_ms") or 0.0)

        mt_queue_wait_ms = 0.0
        mt_service_ms = 0.0
        translated: list[str] = []
        if sentences:
            mt_enqueued = time.perf_counter()

            def translate() -> list[str]:
                return translate_many_sentences(sentences, config)

            translated = await runtime.run_translation(translate, session_id="baseline-1")
            mt_done = time.perf_counter()
            mt_wall_ms = (mt_done - mt_enqueued) * 1000.0
            mt_service_ms = mt_wall_ms
            mt_queue_wait_ms = 0.0
            stage_totals_ms["translation_service_ms"] += mt_service_ms
            recognized_sentences.extend(sentences)
            translations.extend(translated)
            cue_count += int(len(meta.get("cues") or []) or len(sentences))
        else:
            dropped_empty_finals += 1

        total_ms = (time.perf_counter() - enqueued) * 1000.0
        if time_to_first_final_ms is None and sentences:
            time_to_first_final_ms = (time.perf_counter() - session_started) * 1000.0

        final_events.append(
            {
                "reason": reason,
                "audio_end_seconds": round(audio_end_seconds, 3),
                "audio_seconds": round(audio_seconds, 3),
                "asr_wall_ms": round(asr_wall_ms, 3),
                "asr_service_ms": round(asr_service_ms, 3),
                "asr_queue_wait_ms": round(max(0.0, asr_wall_ms - asr_service_ms), 3),
                "translation_wall_ms": round(mt_service_ms, 3),
                "translation_queue_wait_ms": round(mt_queue_wait_ms, 3),
                "end_to_end_ms": round(total_ms, 3),
                "realtime_factor": round((asr_service_ms / 1000.0) / max(audio_seconds, 0.001), 3),
                "sentence_count": len(sentences),
                "translation_count": len(translated),
                "cue_count": len(meta.get("cues") or []),
                "sentences": sentences,
                "translations": translated,
            }
        )

    try:
        while audio_cursor_samples < len(audio):
            piece = audio[audio_cursor_samples : audio_cursor_samples + chunk]
            audio_cursor_samples += len(piece)
            feed_started = time.perf_counter()
            finalized = segmenter.add(piece)
            stage_totals_ms["segmentation_feed_ms"] += (time.perf_counter() - feed_started) * 1000.0
            audio_end_seconds = audio_cursor_samples / sample_rate

            if chunk_index > 0 and chunk_index % partial_every == 0 and segmenter.in_speech:
                snapshot = segmenter.current_snapshot(max_seconds=float(os.getenv("PARTIAL_ASR_MAX_SECONDS", "1.8")))
                if snapshot is not None and len(snapshot) > int(0.35 * sample_rate):
                    await run_partial(snapshot, audio_end_seconds)

            if finalized is not None:
                await run_final(finalized, audio_end_seconds, segmenter.last_finalize_reason)

            chunk_index += 1

        feed_started = time.perf_counter()
        trailing = segmenter.flush()
        stage_totals_ms["segmentation_feed_ms"] += (time.perf_counter() - feed_started) * 1000.0
        if trailing is not None:
            await run_final(trailing, len(audio) / sample_rate, "flush")
    finally:
        runtime_metrics = runtime.metrics.snapshot(runtime)
        await runtime.stop()

    wall_seconds = time.perf_counter() - session_started
    finals_with_text = [row for row in final_events if row["sentence_count"] > 0]
    return {
        "wall_seconds": round(wall_seconds, 3),
        "audio_seconds": round(len(audio) / sample_rate, 3),
        "chunks": chunk_index,
        "chunk_ms": chunk_ms,
        "time_to_first_partial_ms": None if time_to_first_partial_ms is None else round(time_to_first_partial_ms, 3),
        "time_to_first_final_ms": None if time_to_first_final_ms is None else round(time_to_first_final_ms, 3),
        "partial_events": partial_events,
        "final_events": final_events,
        "partial_latency_ms": summary([row["wall_ms"] for row in partial_events]),
        "final_end_to_end_ms": summary([row["end_to_end_ms"] for row in finals_with_text]),
        "final_asr_service_ms": summary([row["asr_service_ms"] for row in finals_with_text]),
        "final_realtime_factor": summary([row["realtime_factor"] for row in finals_with_text]),
        "translation_wall_ms": summary([row["translation_wall_ms"] for row in finals_with_text if row["translation_wall_ms"] > 0]),
        "stage_totals_ms": {key: round(value, 3) for key, value in stage_totals_ms.items()},
        "inference_runtime_metrics": runtime_metrics,
        "recognized_sentences": recognized_sentences,
        "translations": translations,
        "cue_count_total": cue_count,
        "empty_final_segments": dropped_empty_finals,
        "max_rss_kib": int(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss),
    }


def measure_translation_cache(config_source: str, config_target: str) -> dict[str, Any]:
    from app.translator import get_translation_engine

    translator = get_translation_engine()
    probe = DUTCH_REFERENCE_LINES[0]
    miss_samples: list[float] = []
    for text in DUTCH_REFERENCE_LINES:
        started = time.perf_counter()
        translator.translate(text, source_language=config_source, target_language=config_target)
        miss_samples.append((time.perf_counter() - started) * 1000.0)

    hit_samples: list[float] = []
    for _ in range(50):
        started = time.perf_counter_ns()
        translator.translate(probe, source_language=config_source, target_language=config_target)
        hit_samples.append((time.perf_counter_ns() - started) / 1_000_000.0)

    return {
        "cache_info": translator.cache_info(),
        "coldish_miss_ms": summary(miss_samples),
        "warm_hit_ms": summary(hit_samples),
        "config": {
            "source_lang": config_source,
            "target_lang": config_target,
            "mode": "fast",
        },
    }


def path_coverage_notes() -> dict[str, str]:
    return {
        "capture_resampling": "Not measured offline — requires browser AudioWorklet + transferable PCM timestamps.",
        "websocket_transport": "Not measured offline — no capture clock on PCM frames yet.",
        "segmentation_endpointing": "Measured via SpeechSegmenter.add/flush on 20 ms chunks.",
        "per_session_queueing": "Single session in this harness; multi-session use benchmark_concurrency.py --engine real.",
        "global_inference_queueing": "Measured via InferenceRuntime.run_asr/run_translation.",
        "asr": "Measured service time from pipeline meta + wall around runtime await.",
        "sentence_cue_assembly": "Included inside final ASR worker; assembly_ms recorded.",
        "translation_queue_cache_tokenize_infer": "Cache miss/hit measured separately; batching under multi-session not in this run.",
        "rendering_persistence": "Not measured — DOM/localStorage/history writer out of process.",
        "clocks": "All stage timings use time.perf_counter / perf_counter_ns (monotonic). Audio positions use sample counts / 16 kHz.",
    }


def build_report(
    *,
    wav: Path | None,
    mode: str,
    source_lang: str,
    target_lang: str,
    chunk_ms: int,
    asr_model: str | None,
    skip_cache_probe: bool,
) -> dict[str, Any]:
    if asr_model:
        os.environ["ASR_MODEL"] = asr_model

    fixture_path, fixture_meta = ensure_speech_fixture(wav)
    sample_rate, audio = read_wav(fixture_path)

    init_started = time.perf_counter()
    from app.asr import get_asr_engine
    from app.translator import get_translation_engine

    asr = get_asr_engine()
    translator = get_translation_engine()
    init_seconds = time.perf_counter() - init_started

    warm_started = time.perf_counter()
    asr.warmup()
    translator.warmup()
    warmup_seconds = time.perf_counter() - warm_started

    streaming = asyncio.run(
        run_streaming_session(
            audio,
            sample_rate,
            mode=mode,
            source_lang=source_lang,
            target_lang=target_lang,
            chunk_ms=chunk_ms,
            partial_interval_ms=int(os.getenv("PARTIAL_ASR_INTERVAL_MS", "900")),
        )
    )

    cache_probe = None if skip_cache_probe else measure_translation_cache(source_lang, target_lang)

    return {
        "benchmark": "representative-speech-baseline",
        "generated_at_utc": datetime.now(UTC).isoformat(),
        "git": git_metadata(),
        "host": {
            "platform": platform.platform(),
            "processor": platform.processor() or platform.machine(),
            "logical_cpus": os.cpu_count(),
            "max_rss_kib_end": int(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss),
        },
        "audio_fixture": fixture_meta,
        "models": {
            "asr": asr.info(),
            "translation": translator.info(),
        },
        "initialization_seconds": round(init_seconds, 3),
        "warmup_seconds": round(warmup_seconds, 3),
        "streaming": streaming,
        "translation_cache_probe": cache_probe,
        "path_coverage": path_coverage_notes(),
        "provenance": {
            "related_synthetic_baseline": "docs/performance-next-baseline.md",
            "related_synthetic_artifact": "docs/benchmark-artifacts/phase1-baseline-latest.json",
            "difference": (
                "This baseline uses speech-like audio + SpeechSegmenter + InferenceRuntime; "
                "phase1 uses synthetic tone (0 cues) and fake concurrency."
            ),
        },
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run representative Dutch-speech baseline (real models).")
    parser.add_argument("--wav", type=Path, help="Optional 16 kHz PCM WAV; default: generate espeak fixture.")
    parser.add_argument("--mode", choices=["fast", "balanced", "quality"], default="fast")
    parser.add_argument("--source-language", default="nl")
    parser.add_argument("--target-language", default="en")
    parser.add_argument("--chunk-ms", type=int, default=20, help="Simulated capture chunk size (AudioWorklet-like).")
    parser.add_argument("--asr-model", default=None, help="Override ASR_MODEL for this run.")
    parser.add_argument("--skip-cache-probe", action="store_true")
    parser.add_argument("--generate-fixture-only", action="store_true", help="Only write espeak WAV + metadata.")
    parser.add_argument("--output", type=Path)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    ARTIFACTS_DIR.mkdir(parents=True, exist_ok=True)

    if args.generate_fixture_only:
        path, meta = ensure_speech_fixture(None)
        print(json.dumps({"fixture": str(path), "meta": meta}, indent=2))
        return

    report = build_report(
        wav=args.wav,
        mode=args.mode,
        source_lang=args.source_language,
        target_lang=args.target_language,
        chunk_ms=args.chunk_ms,
        asr_model=args.asr_model,
        skip_cache_probe=args.skip_cache_probe,
    )
    timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    output_path = args.output or (ARTIFACTS_DIR / f"representative-baseline-{timestamp}.json")
    payload = json.dumps(report, indent=2)
    output_path.write_text(payload, encoding="utf-8")
    latest = ARTIFACTS_DIR / "representative-baseline-latest.json"
    latest.write_text(payload, encoding="utf-8")
    print(
        json.dumps(
            {
                "artifact": str(output_path),
                "latest": str(latest),
                "time_to_first_partial_ms": report["streaming"]["time_to_first_partial_ms"],
                "time_to_first_final_ms": report["streaming"]["time_to_first_final_ms"],
                "cue_count_total": report["streaming"]["cue_count_total"],
                "final_end_to_end_ms": report["streaming"]["final_end_to_end_ms"],
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
