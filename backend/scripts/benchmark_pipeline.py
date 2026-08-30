from __future__ import annotations

import argparse
import importlib.metadata
import json
import os
import resource
import statistics
import sys
import time
import wave
from pathlib import Path
from typing import Any

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.pipeline import transcribe_and_collect_sentences, transcribe_partial, translate_many_sentences  # noqa: E402
from app.schemas import ClientConfig  # noqa: E402
from app.sentences import SentenceAssembler  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(description="Benchmark local ASR + translation pipeline on a WAV file.")
    parser.add_argument("wav", type=Path, help="16-bit PCM WAV file")
    parser.add_argument("--mode", choices=["fast", "balanced", "quality"], default="fast")
    parser.add_argument("--segment-seconds", type=float, default=3.0)
    parser.add_argument("--max-segments", type=int, default=0, help="0 means all segments")
    parser.add_argument("--asr-model", default=os.getenv("ASR_MODEL", "large-v3-turbo"))
    parser.add_argument("--translation-family", default=os.getenv("TRANSLATION_MODEL_FAMILY", "nllb"))
    parser.add_argument("--source-language", default="nl")
    parser.add_argument("--target-language", default="en")
    parser.add_argument("--partial-runs", type=int, default=10, help="Warmed 1.8-second partial ASR calls")
    parser.add_argument("--cache-hit-runs", type=int, default=100, help="Warmed L1 lookup samples")
    parser.add_argument("--translation-probe-runs", type=int, default=10, help="Fixed-sentence translation latency samples")
    parser.add_argument("--no-warmup", action="store_true", help="Include first-use model costs in inference samples")
    parser.add_argument("--json", action="store_true", help="print JSON only")
    parser.add_argument("--output", type=Path, help="Write JSON results to this file.")
    args = parser.parse_args()

    os.environ["ASR_MODEL"] = args.asr_model
    os.environ["TRANSLATION_MODEL_FAMILY"] = args.translation_family

    sample_rate, audio = read_wav(args.wav)
    if sample_rate != 16000:
        raise ValueError("Benchmark expects 16 kHz WAV audio; resample input before running.")
    config = ClientConfig(
        sample_rate=sample_rate,
        source_lang=args.source_language,
        target_lang=args.target_language,
        mode=args.mode,
    )
    assembler = SentenceAssembler()
    segments = split_audio(audio, sample_rate, args.segment_seconds)
    if args.max_segments > 0:
        segments = segments[: args.max_segments]

    init_started = time.perf_counter()
    from app.asr import get_asr_engine
    from app.translator import get_translation_engine

    asr = get_asr_engine()
    translator = get_translation_engine()
    init_seconds = round(time.perf_counter() - init_started, 3)

    warmup_started = time.perf_counter()
    if not args.no_warmup:
        asr.warmup()
        translator.warmup()
    warmup_seconds = round(time.perf_counter() - warmup_started, 3)

    partial_audio = audio[: min(len(audio), int(sample_rate * 1.8))]
    partial_latencies: list[int] = []
    for _ in range(max(0, args.partial_runs)):
        _text, partial_meta = transcribe_partial(partial_audio, config, prompt=None)
        partial_latencies.append(int(partial_meta["latency_ms"]))

    translation_probe_latencies: list[int] = []
    probe_sentence = ["Dit is een korte testzin voor de vertaalsnelheid."]
    for _ in range(max(0, args.translation_probe_runs)):
        probe_started = time.perf_counter()
        translate_many_sentences(probe_sentence, config)
        translation_probe_latencies.append(int((time.perf_counter() - probe_started) * 1000))

    rows: list[dict[str, Any]] = []
    translated_inputs: list[str] = []
    offset = 0.0
    benchmark_wall_started = time.perf_counter()
    benchmark_cpu_started = time.process_time()
    for index, segment in enumerate(segments, start=1):
        started = time.perf_counter()
        sentences, meta = transcribe_and_collect_sentences(segment, config, assembler, force=True, time_offset_seconds=offset)
        translation_started = time.perf_counter()
        translations = translate_many_sentences(sentences, config) if sentences else []
        translated_inputs.extend(sentences)
        mt_latency_ms = int((time.perf_counter() - translation_started) * 1000)
        total_ms = int((time.perf_counter() - started) * 1000)
        audio_seconds = round(float(len(segment)) / sample_rate, 3)
        offset += audio_seconds
        rows.append(
            {
                "index": index,
                "audio_seconds": audio_seconds,
                "asr_latency_ms": int(meta.get("asr_latency_ms") or 0),
                "mt_latency_ms": mt_latency_ms,
                "total_latency_ms": total_ms,
                "realtime_factor": round((total_ms / 1000.0) / max(audio_seconds, 0.001), 3),
                "sentence_count": len(sentences),
                "cue_count": len(meta.get("cues") or []),
                "word_count": meta.get("word_count", 0),
                "translation_count": len(translations),
            }
        )

    workload_cache_info = translator.cache_info()
    cache_hit_latencies_ms: list[float] = []
    if translated_inputs and args.cache_hit_runs > 0:
        cache_text = translated_inputs[0]
        translator.translate(
            cache_text,
            source_language=config.source_lang,
            target_language=config.target_lang,
        )
        for _ in range(args.cache_hit_runs):
            lookup_started = time.perf_counter_ns()
            translator.translate(
                cache_text,
                source_language=config.source_lang,
                target_language=config.target_lang,
            )
            cache_hit_latencies_ms.append((time.perf_counter_ns() - lookup_started) / 1_000_000.0)

    benchmark_wall_seconds = time.perf_counter() - benchmark_wall_started
    benchmark_cpu_seconds = time.process_time() - benchmark_cpu_started
    logical_cpus = max(1, os.cpu_count() or 1)

    result = {
        "wav": str(args.wav),
        "mode": args.mode,
        "sample_rate": sample_rate,
        "segments": len(rows),
        "asr_model": asr.info().get("asr_model"),
        "translation_model_family": translator.info().get("translation_model_family"),
        "translation_model": translator.info().get("translation_model"),
        "initialization_seconds": init_seconds,
        "warmup_seconds": warmup_seconds,
        "versions": {
            "python": sys.version.split()[0],
            "faster_whisper": importlib.metadata.version("faster-whisper"),
            "ctranslate2": importlib.metadata.version("ctranslate2"),
        },
        "configuration": {
            "source_language": config.source_lang,
            "target_language": config.target_lang,
            "asr_device": asr.info().get("asr_device"),
            "asr_compute_type": asr.info().get("asr_compute_type"),
            "translation_device": translator.info().get("translation_device"),
            "translation_compute_type": translator.info().get("translation_compute_type"),
            "warmup_excluded": not args.no_warmup,
        },
        "resources": {
            "wall_seconds": round(benchmark_wall_seconds, 3),
            "cpu_seconds": round(benchmark_cpu_seconds, 3),
            "cpu_percent_one_core": round(100.0 * benchmark_cpu_seconds / max(benchmark_wall_seconds, 0.000001), 3),
            "cpu_percent_system_capacity": round(
                100.0 * benchmark_cpu_seconds / max(benchmark_wall_seconds * logical_cpus, 0.000001), 3
            ),
            "logical_cpus": logical_cpus,
            "max_rss_kib": int(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss),
        },
        "translation_cache": {
            "workload": workload_cache_info,
            "l1_hit_latency_ms": summary(cache_hit_latencies_ms),
        },
        "summary": {
            "partial_asr_latency_ms": summary(partial_latencies),
            "asr_latency_ms": summary([row["asr_latency_ms"] for row in rows]),
            "mt_latency_ms": summary([row["mt_latency_ms"] for row in rows if row["mt_latency_ms"] > 0] or translation_probe_latencies),
            "translation_probe_latency_ms": summary(translation_probe_latencies),
            "total_latency_ms": summary([row["total_latency_ms"] for row in rows]),
            "realtime_factor": summary([row["realtime_factor"] for row in rows]),
        },
        "rows": rows,
    }
    if args.json:
        payload = json.dumps(result, indent=2)
        if args.output:
            args.output.write_text(payload, encoding="utf-8")
        print(payload)
    else:
        print(json.dumps(result["summary"], indent=2))
        print(
            f"segments={len(rows)} mode={args.mode} asr={result['asr_model']} "
            f"translation={result['translation_model_family']} wav={args.wav} "
            f"source={config.source_lang} target={config.target_lang}"
        )


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


def split_audio(audio: np.ndarray, sample_rate: int, seconds: float) -> list[np.ndarray]:
    chunk = max(1, int(sample_rate * seconds))
    return [audio[index : index + chunk] for index in range(0, len(audio), chunk) if len(audio[index : index + chunk]) > 0]


def summary(values: list[float] | list[int]) -> dict[str, float | int | None]:
    if not values:
        return {"count": 0, "p50": None, "p95": None, "max": None}
    ordered = sorted(values)
    return {
        "count": len(values),
        "p50": round(float(statistics.median(ordered)), 3),
        "p95": round(float(percentile(ordered, 0.95)), 3),
        "max": round(float(max(ordered)), 3),
    }


def percentile(values: list[float] | list[int], p: float) -> float:
    if len(values) == 1:
        return float(values[0])
    index = (len(values) - 1) * p
    lower = int(index)
    upper = min(lower + 1, len(values) - 1)
    weight = index - lower
    return float(values[lower]) * (1 - weight) + float(values[upper]) * weight


if __name__ == "__main__":
    main()
