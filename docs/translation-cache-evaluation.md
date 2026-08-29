# Translation cache and batching evaluation

## Cache architecture

The in-memory `OrderedDict` remains the primary bounded O(1) LRU. Its key keeps
source text and language, target language, engine, model, tokenizer, beam size,
maximum decoding length, glossary content version, and schema version because
each can change the translation output. Concurrent identical misses use one
owner future and waiters receive the same result. Clearing the cache advances a
generation so an older in-flight result cannot repopulate either tier.

SQLite remains an opt-in L2. Production L2 writes use one ordered background
worker; reads occur outside the L1 lock, so a slow disk lookup does not block an
unrelated memory hit. The store reuses one connection, batches access-time and
hit-count updates, prunes after a write threshold, and removes overflow with one
SQL statement. Failures fall back to model translation and do not disable L1.

## Real transcript reuse

The local history database supplied 737 non-trivial real subtitle requests from
28 sessions.

| Metric | Result |
| --- | ---: |
| Total requests | 737 |
| Unique normalized sentences | 728 |
| L1 hits within a session | 9 |
| Potential L2 hits from an earlier session | 0 |
| Misses | 728 |
| Overall hit ratio | 1.22% |
| L1 hit latency p50 / p95 | 0.005 / 0.006 ms |
| Actual translation average (40 unique lines) | 25.975 ms |

Durable caching provides no reuse for this corpus. It therefore remains off by
default (`TRANSLATION_CACHE_BACKEND=memory`) and no periodic service or extra
database is created for normal users. The small opt-in L2 is retained for
specialized repetitive workloads and restart persistence, but is isolated from
the subtitle latency path.

## Translation micro-batching

On four realistic Dutch sentences, ten CPU/int8 rounds measured:

| Strategy | p50 total |
| --- | ---: |
| Four sequential model calls | 87.054 ms |
| Existing `translate_many` batch | 28.395 ms |
| Batch plus hypothetical 10 ms collection delay | 38.395 ms |

Batching improves raw throughput by 67.4%, and the application already captures
that benefit by batching all sentences emitted from one finalized ASR segment.
Cross-job micro-batching was rejected: final ASR is serial and takes roughly
832 ms, so a second translation job is not available within a 5–15 ms window in
the measured pipeline. Waiting would add about 10 ms to every visible subtitle
without forming useful additional batches. Immediately available sentences
continue to be batched with zero collection delay.
