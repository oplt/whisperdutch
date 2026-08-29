# Backend optimization measurements

All measurements use the CPU/int8 environment described in
`performance-baseline.md`. CUDA benchmarks were skipped because CTranslate2
reported zero CUDA devices.

## Adaptive partial ASR

A deterministic 3.0-second utterance was replayed at 100 ms callback intervals
with realtime factor 0.7 and an otherwise idle queue. The former fixed 900 ms
policy produces three overlapping partial Whisper candidates. The adaptive
policy produces one, a 66.7% reduction, by increasing the interval to 1,845 ms
and suppressing work near finalization.

The runtime also records `partial_inferences`, `partial_suppressed`, maximum ASR
queue depth, and maximum translation queue depth directly. Partial work is
suppressed while final work is queued, ASR is busy, the ASR queue is non-empty,
realtime factor reaches 0.8, recent queue backpressure is active, the
translation queue is full, or the segment is close to silence/max-duration
finalization.

## SpeechSegmenter allocation benchmark

Workload: 200 utterances, 30,000 20 ms speech callbacks, 1,000 silence
callbacks, and 2,000 partial snapshots.

| Implementation | Elapsed | Peak traced Python allocation |
| --- | ---: | ---: |
| Chunk lists plus repeated `np.concatenate` | 420.271 ms | 196,064 bytes |
| Reusable contiguous buffer plus `deque` pre-roll | 236.927 ms | 385,808 bytes |
| Change | **-43.6%** | +189,744 bytes retained capacity |

The reusable buffer is retained because the latency improvement is material and
the memory trade-off is bounded to roughly one configured utterance. Final and
partial arrays are still copied before leaving the segmenter so later writes
cannot corrupt queued inference.

## ASR modes

Each mode was warmed, then measured for five 3.0-second zero-PCM calls. The
microbenchmark isolates decoder/runtime overhead and does not claim accuracy.

| Mode | Beam | p50 | Max |
| --- | ---: | ---: | ---: |
| Fast | 1 | 831.766 ms | 854.957 ms |
| Balanced | 2 | 843.842 ms | 883.004 ms |
| Quality | 3 | 867.478 ms | 876.096 ms |

Language, thresholds, previous-text behavior, and all three beam sizes are now
validated once when the singleton engine is created. The hot path selects an
immutable mode configuration and never reloads the model.
