from __future__ import annotations

import argparse
import asyncio
import json
import os
import resource
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, TypeVar

import numpy as np

SCRIPT_DIR = Path(__file__).resolve().parent
ROOT = SCRIPT_DIR.parent
for import_path in (SCRIPT_DIR, ROOT):
    if str(import_path) not in sys.path:
        sys.path.insert(0, str(import_path))

from app.schemas import ClientConfig  # noqa: E402
from app.sentences import SentenceAssembler  # noqa: E402

from benchmark_pipeline import read_wav, split_audio, summary  # noqa: E402

T = TypeVar("T")


@dataclass
class Samples:
    partial_latency_ms: list[float] = field(default_factory=list)
    final_asr_latency_ms: list[float] = field(default_factory=list)
    translation_latency_ms: list[float] = field(default_factory=list)
    asr_queue_wait_ms: list[float] = field(default_factory=list)
    translation_queue_wait_ms: list[float] = field(default_factory=list)
    end_to_end_latency_ms: list[float] = field(default_factory=list)
    realtime_factors: list[float] = field(default_factory=list)


def measured_call(enqueued_at: float, function: Callable[..., T], *args: Any) -> tuple[T, float, float]:
    started_at = time.perf_counter()
    result = function(*args)
    completed_at = time.perf_counter()
    return result, (started_at - enqueued_at) * 1000.0, (completed_at - started_at) * 1000.0


def fake_partial(delay_seconds: float) -> tuple[str, dict[str, Any]]:
    time.sleep(delay_seconds)
    return "partial", {"latency_ms": round(delay_seconds * 1000.0), "audio_seconds": 1.8}


def fake_final(delay_seconds: float, session_id: int, job_id: int) -> tuple[list[str], dict[str, Any]]:
    time.sleep(delay_seconds)
    text = f"session {session_id} job {job_id}"
    return [text], {
        "asr_latency_ms": round(delay_seconds * 1000.0),
        "audio_seconds": 3.0,
        "realtime_factor": round(delay_seconds / 3.0, 6),
    }


def fake_translation(delay_seconds: float, sentences: list[str]) -> list[str]:
    time.sleep(delay_seconds)
    return [f"translated:{sentence}" for sentence in sentences]


async def benchmark_fake_session(
    session_id: int,
    jobs: int,
    gate: asyncio.Event,
    samples: Samples,
    *,
    asr_delay_seconds: float,
    translation_delay_seconds: float,
    partials_per_final: int,
) -> None:
    await gate.wait()
    for job_id in range(jobs):
        for _ in range(partials_per_final):
            enqueued_at = time.perf_counter()
            (_partial, _meta), queue_wait_ms, service_ms = await asyncio.to_thread(
                measured_call,
                enqueued_at,
                fake_partial,
                asr_delay_seconds * 0.55,
            )
            samples.asr_queue_wait_ms.append(queue_wait_ms)
            samples.partial_latency_ms.append(queue_wait_ms + service_ms)

        final_enqueued_at = time.perf_counter()
        (sentences, meta), queue_wait_ms, asr_service_ms = await asyncio.to_thread(
            measured_call,
            final_enqueued_at,
            fake_final,
            asr_delay_seconds,
            session_id,
            job_id,
        )
        samples.asr_queue_wait_ms.append(queue_wait_ms)
        samples.final_asr_latency_ms.append(queue_wait_ms + asr_service_ms)

        translation_enqueued_at = time.perf_counter()
        _translations, mt_wait_ms, mt_service_ms = await asyncio.to_thread(
            measured_call,
            translation_enqueued_at,
            fake_translation,
            translation_delay_seconds,
            sentences,
        )
        samples.translation_queue_wait_ms.append(mt_wait_ms)
        samples.translation_latency_ms.append(mt_wait_ms + mt_service_ms)
        end_to_end_ms = (time.perf_counter() - final_enqueued_at) * 1000.0
        samples.end_to_end_latency_ms.append(end_to_end_ms)
        samples.realtime_factors.append(end_to_end_ms / (float(meta["audio_seconds"]) * 1000.0))


async def benchmark_real_session(
    session_id: int,
    segments: list[np.ndarray],
    config: ClientConfig,
    gate: asyncio.Event,
    samples: Samples,
    *,
    partials_per_final: int,
) -> None:
    from app.pipeline import transcribe_and_collect_sentences, transcribe_partial, translate_many_sentences

    assembler = SentenceAssembler(source_language=config.source_lang)
    await gate.wait()
    offset = 0.0
    for job_id, segment in enumerate(segments):
        partial_audio = segment[: min(len(segment), int(config.sample_rate * 1.8))]
        for _ in range(partials_per_final):
            enqueued_at = time.perf_counter()
            (_partial, _meta), queue_wait_ms, service_ms = await asyncio.to_thread(
                measured_call,
                enqueued_at,
                transcribe_partial,
                partial_audio,
                config,
                None,
            )
            samples.asr_queue_wait_ms.append(queue_wait_ms)
            samples.partial_latency_ms.append(queue_wait_ms + service_ms)

        final_enqueued_at = time.perf_counter()
        (sentences, meta), queue_wait_ms, asr_service_ms = await asyncio.to_thread(
            measured_call,
            final_enqueued_at,
            transcribe_and_collect_sentences,
            segment,
            config,
            assembler,
            True,
        )
        samples.asr_queue_wait_ms.append(queue_wait_ms)
        samples.final_asr_latency_ms.append(queue_wait_ms + asr_service_ms)

        translation_enqueued_at = time.perf_counter()
        _translations, mt_wait_ms, mt_service_ms = await asyncio.to_thread(
            measured_call,
            translation_enqueued_at,
            translate_many_sentences,
            sentences,
            config,
        )
        samples.translation_queue_wait_ms.append(mt_wait_ms)
        samples.translation_latency_ms.append(mt_wait_ms + mt_service_ms)
        end_to_end_ms = (time.perf_counter() - final_enqueued_at) * 1000.0
        samples.end_to_end_latency_ms.append(end_to_end_ms)
        audio_seconds = max(float(meta.get("audio_seconds") or 0.0), 0.001)
        samples.realtime_factors.append(end_to_end_ms / (audio_seconds * 1000.0))
        offset += audio_seconds
        _ = (session_id, job_id, offset)


async def run_concurrency(args: argparse.Namespace, real_segments: list[np.ndarray] | None) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    config = ClientConfig(
        sample_rate=16000,
        source_lang=args.source_language,
        target_lang=args.target_language,
        mode=args.mode,
    )

    for session_count in args.sessions:
        samples = Samples()
        gate = asyncio.Event()
        wall_started = time.perf_counter()
        cpu_started = time.process_time()
        if args.engine == "fake":
            tasks = [
                asyncio.create_task(
                    benchmark_fake_session(
                        session_id,
                        args.jobs,
                        gate,
                        samples,
                        asr_delay_seconds=args.fake_asr_ms / 1000.0,
                        translation_delay_seconds=args.fake_translation_ms / 1000.0,
                        partials_per_final=args.partials_per_final,
                    )
                )
                for session_id in range(session_count)
            ]
        else:
            assert real_segments is not None
            tasks = [
                asyncio.create_task(
                    benchmark_real_session(
                        session_id,
                        real_segments,
                        config,
                        gate,
                        samples,
                        partials_per_final=args.partials_per_final,
                    )
                )
                for session_id in range(session_count)
            ]
        gate.set()
        await asyncio.gather(*tasks)
        wall_seconds = time.perf_counter() - wall_started
        cpu_seconds = time.process_time() - cpu_started
        logical_cpus = max(1, os.cpu_count() or 1)
        final_count = len(samples.final_asr_latency_ms)
        partial_count = len(samples.partial_latency_ms)
        results.append(
            {
                "sessions": session_count,
                "jobs_per_session": args.jobs if args.engine == "fake" else len(real_segments or []),
                "wall_seconds": round(wall_seconds, 3),
                "throughput_final_jobs_per_second": round(final_count / max(wall_seconds, 0.000001), 3),
                "cpu_seconds": round(cpu_seconds, 3),
                "cpu_percent_one_core": round(100.0 * cpu_seconds / max(wall_seconds, 0.000001), 3),
                "cpu_percent_system_capacity": round(
                    100.0 * cpu_seconds / max(wall_seconds * logical_cpus, 0.000001), 3
                ),
                "max_rss_kib": int(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss),
                "partial_inferences_per_minute": round(partial_count * 60.0 / max(wall_seconds, 0.000001), 3),
                "partial_asr_latency_ms": summary(samples.partial_latency_ms),
                "final_asr_latency_ms": summary(samples.final_asr_latency_ms),
                "translation_latency_ms": summary(samples.translation_latency_ms),
                "asr_queue_wait_ms": summary(samples.asr_queue_wait_ms),
                "translation_queue_wait_ms": summary(samples.translation_queue_wait_ms),
                "final_end_to_end_latency_ms": summary(samples.end_to_end_latency_ms),
                "realtime_factor": summary(samples.realtime_factors),
            }
        )
    return results


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Measure 1/2/4-session inference concurrency with fake or real local models.")
    parser.add_argument("--engine", choices=["fake", "real"], default="fake")
    parser.add_argument("--sessions", type=int, nargs="+", default=[1, 2, 4])
    parser.add_argument("--jobs", type=int, default=12)
    parser.add_argument("--wav", type=Path)
    parser.add_argument("--segment-seconds", type=float, default=3.0)
    parser.add_argument("--mode", choices=["fast", "balanced", "quality"], default="fast")
    parser.add_argument("--source-language", default="nl")
    parser.add_argument("--target-language", default="en")
    parser.add_argument("--partials-per-final", type=int, default=1)
    parser.add_argument("--fake-asr-ms", type=float, default=30.0)
    parser.add_argument("--fake-translation-ms", type=float, default=5.0)
    parser.add_argument("--asr-model", default=os.getenv("ASR_MODEL", "small"))
    parser.add_argument("--translation-family", default=os.getenv("TRANSLATION_MODEL_FAMILY", "nllb"))
    parser.add_argument("--output", type=Path, help="Write JSON results to this file.")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    if any(count < 1 for count in args.sessions):
        raise ValueError("Session counts must be positive")
    if args.jobs < 1:
        raise ValueError("--jobs must be positive")
    if args.engine == "real" and args.wav is None:
        raise ValueError("--wav is required for real-model concurrency")

    os.environ["ASR_MODEL"] = args.asr_model
    os.environ["TRANSLATION_MODEL_FAMILY"] = args.translation_family
    os.environ["TRANSLATION_CACHE_ITEMS"] = "0"

    real_segments: list[np.ndarray] | None = None
    model_info: dict[str, Any] | None = None
    if args.engine == "real":
        assert args.wav is not None
        sample_rate, audio = read_wav(args.wav)
        if sample_rate != 16000:
            raise ValueError("Benchmark expects 16 kHz WAV audio")
        real_segments = split_audio(audio, sample_rate, args.segment_seconds)[: args.jobs]
        from app.asr import get_asr_engine
        from app.translator import get_translation_engine

        asr = get_asr_engine()
        translator = get_translation_engine()
        asr.warmup()
        translator.warmup()
        model_info = {"asr": asr.info(), "translation": translator.info()}

    results = asyncio.run(run_concurrency(args, real_segments))
    output = {
        "benchmark": "phase1-concurrency",
        "engine": args.engine,
        "model_info": model_info,
        "configuration": {
            "sessions": args.sessions,
            "jobs": args.jobs,
            "partials_per_final": args.partials_per_final,
            "source_language": args.source_language,
            "target_language": args.target_language,
            "mode": args.mode,
            "fake_asr_ms": args.fake_asr_ms if args.engine == "fake" else None,
            "fake_translation_ms": args.fake_translation_ms if args.engine == "fake" else None,
            "translation_cache_disabled": True,
        },
        "results": results,
        "not_measured": {
            "capture_to_backend_audio_delay": "Binary PCM frames carry no capture timestamp.",
            "dropped_audio_chunks": "Requires a browser capture session; not part of this backend harness.",
            "websocket_buffered_amount_high_water": "The frontend does not currently retain this high-water mark.",
            "history_write_backlog": "History persistence is excluded from this inference-only harness.",
        },
    }
    payload = json.dumps(output, indent=2)
    if args.output:
        args.output.write_text(payload, encoding="utf-8")
    print(payload)


if __name__ == "__main__":
    main()
