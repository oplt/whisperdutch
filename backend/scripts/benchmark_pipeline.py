from __future__ import annotations

import argparse
import json
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

from app.schemas import ClientConfig  # noqa: E402
from app.sentences import SentenceAssembler  # noqa: E402
from app.pipeline import transcribe_and_collect_sentences, translate_many_sentences  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(description="Benchmark local ASR + translation pipeline on a WAV file.")
    parser.add_argument("wav", type=Path, help="16-bit PCM WAV file")
    parser.add_argument("--mode", choices=["fast", "balanced", "quality"], default="fast")
    parser.add_argument("--segment-seconds", type=float, default=3.0)
    parser.add_argument("--max-segments", type=int, default=0, help="0 means all segments")
    parser.add_argument("--json", action="store_true", help="print JSON only")
    args = parser.parse_args()

    sample_rate, audio = read_wav(args.wav)
    if sample_rate != 16000:
        raise ValueError("Benchmark expects 16 kHz WAV audio; resample input before running.")
    config = ClientConfig(sample_rate=sample_rate, mode=args.mode)
    assembler = SentenceAssembler()
    segments = split_audio(audio, sample_rate, args.segment_seconds)
    if args.max_segments > 0:
        segments = segments[: args.max_segments]

    rows: list[dict[str, Any]] = []
    for index, segment in enumerate(segments, start=1):
        started = time.perf_counter()
        sentences, meta = transcribe_and_collect_sentences(segment, config, assembler, force=True)
        translation_started = time.perf_counter()
        translations = translate_many_sentences(sentences) if sentences else []
        mt_latency_ms = int((time.perf_counter() - translation_started) * 1000)
        total_ms = int((time.perf_counter() - started) * 1000)
        rows.append(
            {
                "index": index,
                "audio_seconds": round(float(len(segment)) / sample_rate, 3),
                "asr_latency_ms": int(meta.get("asr_latency_ms") or 0),
                "mt_latency_ms": mt_latency_ms,
                "total_latency_ms": total_ms,
                "realtime_factor": round((total_ms / 1000.0) / max(float(len(segment)) / sample_rate, 0.001), 3),
                "sentence_count": len(sentences),
                "translation_count": len(translations),
            }
        )

    result = {
        "wav": str(args.wav),
        "mode": args.mode,
        "sample_rate": sample_rate,
        "segments": len(rows),
        "summary": {
            "asr_latency_ms": summary([row["asr_latency_ms"] for row in rows]),
            "mt_latency_ms": summary([row["mt_latency_ms"] for row in rows]),
            "total_latency_ms": summary([row["total_latency_ms"] for row in rows]),
            "realtime_factor": summary([row["realtime_factor"] for row in rows]),
        },
        "rows": rows,
    }
    if args.json:
        print(json.dumps(result, indent=2))
    else:
        print(json.dumps(result["summary"], indent=2))
        print(f"segments={len(rows)} mode={args.mode} wav={args.wav}")


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
