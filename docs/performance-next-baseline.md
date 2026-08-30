# Performance next baseline (Phase 1)

Reproducible pre-optimization baseline for the local live subtitle pipeline.

**Generated:** 2026-08-30T10:17:22Z  
**Git commit:** `60dc89d94237a01bbbdd1b6455f0f3baf2d381bd` (`main`, subject: `dev`)  
**Raw artifact:** [`benchmark-artifacts/phase1-baseline-latest.json`](benchmark-artifacts/phase1-baseline-latest.json)

Prior docs referenced in `task.txt` (`docs/performance-baseline.md`, `docs/performance-after.md`, etc.) are not present in this repository snapshot; this file is the new canonical baseline for the performance work tracked in `task.txt`.

---

## Environment

| Item | Value |
| --- | --- |
| Host | Linux 6.8.0-138-generic x86_64 |
| Logical CPUs | 16 |
| Python | 3.12.3 |
| faster-whisper | 1.2.1 |
| CTranslate2 | 4.7.1 |
| transformers | 4.57.6 |
| CUDA | **Not available** (0 devices) |

### Model configuration (from `backend/.env`)

| Setting | Value |
| --- | --- |
| ASR model | `small` |
| ASR device / compute | `cpu` / `int8` |
| Translation family | `nllb` |
| Translation model | `models/nllb-200-distilled-600m-ct2` |
| Translation tokenizer | `facebook/nllb-200-distilled-600M` |
| Translation device / compute | `cpu` / `int8` |

---

## How to reproduce

From the repository root:

```bash
cd backend
. .venv/bin/activate
set -a && [ -f .env ] && . ./.env && set +a
python scripts/benchmark_phase1.py --max-segments 4
```

Individual harnesses:

```bash
python scripts/benchmark_startup.py --output /tmp/startup.json
python scripts/benchmark_pipeline.py /path/to/16k.wav --mode fast --json --output /tmp/pipeline.json
python scripts/benchmark_concurrency.py --engine fake --sessions 1 2 4 --output /tmp/concurrency.json
python scripts/benchmark_concurrency.py --engine real --wav /path/to/16k.wav --sessions 1 2 4 --output /tmp/concurrency-real.json
```

Unit tests for the harness helpers:

```bash
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 pytest backend/tests/test_phase1_benchmarks.py
```

---

## Startup (cold backend process)

Measured by spawning a fresh backend via `backend/run_gpu.sh` on an isolated localhost port.

| Metric | ms |
| --- | ---: |
| Process launch | 0.5 |
| `/health/live` | 4057 |
| `/health/ready` (models warmed) | 8532 |
| Peak RSS during startup | 1834724 KiB (~1.8 GB) |
| Startup CPU (one core) | 104% |

Native-host `start_backend` command latency: **0.9 ms** when a backend was already running on port 8000 (status `already_running`).

---

## Single-session pipeline (`fast` mode, synthetic 16 kHz WAV)

Audio: 30 s synthetic tone burst (no real speech). **ASR ran on all segments; sentence assembly produced 0 cues** because the synthetic input is not transcribed as speech. ASR latency is still valid; translation used a fixed Dutch probe sentence.

Warmup excluded from inference samples. Four 3-second segments measured.

| Metric | p50 | p95 | max |
| --- | ---: | ---: | ---: |
| Partial ASR (1.8 s window, n=10) | 963 ms | 1121 ms | 1164 ms |
| Final ASR (3 s segment, n=4) | 941 ms | 1053 ms | 1068 ms |
| Translation probe (n=10, cached after 1st) | 0 ms | 161 ms | 292 ms |
| Final end-to-end (ASR only, n=4) | 941 ms | 1053 ms | 1068 ms |
| Realtime factor (ASR wall / audio) | 0.314 | 0.351 | 0.356 |

### Resources (pipeline run)

| Metric | Value |
| --- | ---: |
| Peak RSS | 2156020 KiB (~2.1 GB) |
| CPU vs system capacity | 27.1% |
| Model init (ASR + MT load) | 4.0 s |
| Explicit warmup | 0.2 s |

### Translation cache (during pipeline workload)

| Metric | Value |
| --- | ---: |
| Hit ratio | 81.8% |
| Cache-hit lookup p50 | 0.001 ms |
| Cache-miss translation p50 | 235 ms |
| Cache-miss translation p95 | 287 ms |

---

## Concurrent sessions (deterministic fake engine)

Scheduler/concurrency shape only — **not real model contention**. Fake delays: ASR 30 ms, translation 5 ms, 8 final jobs per session, 1 partial per final.

| Sessions | Final E2E p50 | Final E2E p95 | ASR queue wait p95 | MT queue wait p95 | Partials/min | Peak RSS KiB |
| ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| 1 | 35.5 ms | 35.7 ms | 0.17 ms | 0.08 ms | 1146 | 563536 |
| 2 | 35.6 ms | 35.8 ms | 0.16 ms | 0.19 ms | 2290 | 563536 |
| 4 | 35.5 ms | 36.0 ms | 0.17 ms | 0.12 ms | 4579 | 563664 |

Real-model concurrency (`--include-real-models`) was **not run** in this baseline because CUDA is unavailable and CPU real-model 4-session runs would add significant wall time; run locally when needed.

---

## Not measured in this harness

| Metric | Reason |
| --- | --- |
| Capture → backend audio delay | PCM WebSocket frames carry no capture timestamp |
| Dropped audio chunks | Requires live browser capture telemetry |
| WebSocket `bufferedAmount` high-water mark | Frontend does not persist this yet |
| History / DB write backlog | Excluded from offline inference harness |
| Live ASR/translation queue wait under real WebSocket load | Use production metrics endpoint + future browser instrumentation |

---

## Notes for Phase 2+ comparisons

1. **ASR dominates** final latency on CPU (`small`, ~940 ms for 3 s audio).
2. **Translation is comparatively fast** on CPU/int8 NLLB once warmed (cache hits sub-millisecond; cold miss ~235 ms).
3. **Startup to ready ~8.5 s** includes ASR + translation model load and warmup on this machine.
4. Replace synthetic WAV with a real 16 kHz speech sample for sentence/cue/translation-path measurements in future baselines.
5. Re-run with `ASR_DEVICE=cuda` and `TRANSLATION_DEVICE=cuda` on GPU hardware before claiming CUDA improvements.

---

## Related tests

- `backend/tests/test_phase1_benchmarks.py` — summary math, fake 1/2/4-session concurrency, queue/service timing helper, startup port finder
