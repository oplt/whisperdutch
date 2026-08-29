# Performance baseline

Measured on 2026-08-29 before the optimization tasks were implemented. The
committed `HEAD` backend was used for startup behavior; inference measurements
used the same ASR and translation code because the pre-existing worktree edits
only touched WebSocket ordering, startup scheduling, and AudioWorklet
resampling.

## Environment

- Python 3.12, CTranslate2 4.7.1, faster-whisper 1.2.1
- ASR: Whisper `small`, CPU, int8, four threads
- Translation: OPUS-MT nl-en, CTranslate2, CPU, int8, beam size 1
- Hardware acceleration: unavailable (`cuda_device_count=0`)
- Translation corpus: 737 persisted real subtitle requests, 728 unique after
  whitespace normalization
- Long-session sample: `ws-1787943624097486833`, 4,235 audio chunks, 69 ASR
  samples, and 62 completed subtitle latency samples

## Results

| Metric | Baseline | Source / method |
| --- | ---: | --- |
| Backend process import/startup | 1,950 ms | `/usr/bin/time` around `import app.main` |
| `/health/live` availability | 2,849 ms | Isolated committed backend; endpoint could not serve until blocking lifespan warmup finished |
| `/health/ready` availability | 2,849 ms | Isolated committed backend log timestamps |
| Partial ASR latency p50 / p95 | 798.540 / 828.644 ms | 12 warmed 1.8 s partial calls |
| Final ASR latency p50 / p95 | 853.018 / 868.198 ms | 12 warmed 3.0 s final calls |
| Translation latency p50 / p95 | 26.557 / 38.167 ms | First 40 unique persisted subtitle lines |
| WebSocket queue delay p50 / p95 | 0 / 0 ms | Long-session delivered rows; total latency equaled ASR plus translation latency |
| Total subtitle latency p50 / p95 | 1,338 / 1,526.9 ms | Latest long real session |
| Realtime factor p50 / p95 | 0.329 / 0.860 | Latest long real session |
| Translation cache hit ratio | 1.22% (9 / 737) | Replay of persisted real subtitle corpus |
| Translation L1 hit latency p50 / p95 | 0.005 / 0.006 ms | 100 warmed in-memory hits |
| Maximum resident memory | 1,209,860 KiB | `ru_maxrss` after ASR and translation replay |
| Audio chunks dropped by frontend WebSocket backpressure | 0 observed | No `websocket_backpressure` drop event in the measured session logs |
| Maximum ASR queue depth | 3 | Long-session metrics |
| Maximum translation queue depth | 1 observed | Translation completed before the next final ASR result throughout the measured session |
| Rendered transcript rows after a long session | 500 | Existing hard retention limit |
| Frontend element nodes after a long session | 1,727 | 227 static elements plus 500 three-element subtitle rows |
| Estimated total DOM nodes after a long session | 2,831 | Element nodes plus non-empty text nodes |

The ASR microbenchmark uses zero-valued PCM to isolate inference overhead; the
real-session figures cover end-to-end behavior on captured speech. Queue delay
was not yet persisted as a standalone field in the historical session record,
so the zero value is derived from each delivered row's exact
`total = ASR + translation` relationship. Translation queue depth likewise had
no dedicated counter and was reconstructed from event ordering. Both counters
must be recorded directly in the post-change benchmark.

## Repository audit findings

- `subtitle.js` is a 61.7 kB controller with capture, WebSocket, backend,
  settings, transcript, glossary, export, and rendering responsibilities.
- Partial ASR is interval-based and repeatedly transcribes overlapping audio.
- `SpeechSegmenter` repeatedly concatenates chunk lists and removes pre-roll
  chunks from the front of a list.
- ASR reads invariant environment configuration on every inference call.
- Durable translation reads and writes run synchronously while the L1 cache
  lock is held; SQLite opens a connection for every operation, updates every
  hit, and prunes every write.
- Backend startup blocks serving until both models load and warm.
- Frontend transcript rendering retains 500 rows and has full-list rerender
  paths.
- Frontend state is represented by interacting booleans and resource-null
  checks, leaving stale callback races possible.
- Runtime and development Python dependencies are mixed.
- The repository check script selects the backend virtual environment, but the
  existing environment does not contain pytest, so the initial combined check
  stops before running tests.

These numbers are the fixed “before” values for Task 38. Later optimization
claims must use the same model/device profile and corpus, and must distinguish
microbenchmark results from real-session results.
