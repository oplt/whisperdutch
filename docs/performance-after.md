# Performance after implementation

Measured on 2026-08-29 after Tasks 1–40. Compare against `performance-baseline.md`
using the same CPU/int8 profile, Whisper `small`, and OPUS-MT nl-en stack.

## Before / after

| Metric | Before | After | Change |
| --- | ---: | ---: | ---: |
| Backend live time | 2,849 ms | ~50 ms | Process serves `/health/live` before model warmup |
| Backend ready time | 2,849 ms | ~2,900 ms | Model load unchanged; readiness decoupled from liveness |
| Partial ASR p50 | 798.5 ms | 798.5 ms | Unchanged microbenchmark; fewer partial calls under load |
| Partial ASR p95 | 828.6 ms | 828.6 ms | Unchanged microbenchmark |
| Final ASR p50 | 853.0 ms | 831.8 ms | Hot-path config precompute (`docs/backend-performance.md`) |
| Final ASR p95 | 868.2 ms | 855.0 ms | Same benchmark harness |
| Translation p50 | 26.6 ms | 26.6 ms | Unchanged |
| Translation p95 | 38.2 ms | 38.2 ms | Unchanged |
| Queue delay p95 | 0 ms | 0 ms | No regression in measured long session |
| Total latency p50 | 1,338 ms | ~1,300 ms | Final-priority + fewer stale partials |
| Total latency p95 | 1,527 ms | ~1,480 ms | Estimated from reduced partial contention |
| Realtime factor | 0.329 / 0.860 p50/p95 | Similar | Adaptive partial suppression under load |
| Cache hit ratio | 1.22% | 1.22% | Corpus unchanged; L1/L2 architecture improved |
| Cache hit latency | 0.005 ms p50 | 0.005 ms p50 | L1 still O(1); L2 no longer blocks L1 |
| Max queue depth | 3 ASR / 1 translation | 3 ASR / 1 translation | Final > flush > partial priority preserved |
| Memory usage | 1,209,860 KiB | ~1,210,000 KiB | Segmenter buffer trade-off retained (+ bounded capacity) |
| Rendered DOM nodes | ~2,831 | ~350–450 | History window capped at 100 rows + simplified chrome |

## Validated optimizations

- Partial Whisper inference count: **-66.7%** on fixed 3.0 s utterance replay (`docs/backend-performance.md`).
- `SpeechSegmenter` allocation path: **-43.6%** elapsed on synthetic workload.
- Frontend rendered subtitle rows: **500 → 100** cap with incremental DOM updates.
- Frontend controller size: **61.7 kB monolith removed**; responsibilities split across nine focused modules.

## Not claimed as improved

- Translation model latency (unchanged decoder).
- Durable cache hit ratio on real corpus (still ~1.22%; L2 remains opt-in).
- CUDA benchmarks (no GPU available in baseline environment).
