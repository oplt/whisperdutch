# Streaming ASR integration note

The live pipeline separates concerns so a future incremental ASR strategy (for example SimulStreaming) can replace only the inference step:

```text
Browser PCM capture
  → SpeechSegmenter (chunk boundaries)
  → TranscriptionEngine.transcribe_result(inference_kind=partial|final)
  → SubtitleSegmenter (word-aware cue construction)
  → SentenceAssembler fallback (text-only paths)
  → TranslationEngine backend
  → WebSocket final/partial events
```

`TranscriptionEngine` is the natural swap point: a future `StreamingASRStrategy` would implement `push_audio()` and emit partial/final `TranscriptionResult` objects without changing WebSocket or translation code.

WhisperX remains outside the live loop in `app/alignment.py` for optional high-quality export only.
