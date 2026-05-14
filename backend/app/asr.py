from __future__ import annotations

import os
from functools import lru_cache
from typing import Any

import numpy as np
from faster_whisper import WhisperModel


def _torch_cuda_info() -> dict[str, Any]:
    try:
        import torch

        available = bool(torch.cuda.is_available())
        return {
            "torch_cuda_available": available,
            "torch_cuda_device_count": int(torch.cuda.device_count()) if available else 0,
            "torch_cuda_device_name": torch.cuda.get_device_name(0) if available else None,
            "torch_version": torch.__version__,
            "torch_cuda_version": getattr(torch.version, "cuda", None),
        }
    except Exception as exc:
        return {
            "torch_cuda_available": False,
            "torch_cuda_device_count": 0,
            "torch_cuda_device_name": None,
            "torch_error": str(exc),
        }


def _auto_device() -> str:
    requested = os.getenv("ASR_DEVICE", "auto").strip().lower()
    if requested != "auto":
        return requested
    return "cuda" if _torch_cuda_info().get("torch_cuda_available") else "cpu"


class TranscriptionEngine:
    def __init__(self) -> None:
        # For live subtitles, small gives the best perceived latency on RTX 3060.
        # Use medium or large-v3-turbo only in Quality mode if you accept higher latency.
        self.model_name = os.getenv("ASR_MODEL", "small")
        self.device = _auto_device()
        self.compute_type = os.getenv(
            "ASR_COMPUTE_TYPE",
            "float16" if self.device == "cuda" else "int8",
        )
        self.cpu_threads = int(os.getenv("ASR_CPU_THREADS", "4"))

        print(
            f"[ASR] Loading faster-whisper model={self.model_name} "
            f"device={self.device} compute_type={self.compute_type}",
            flush=True,
        )
        self.model = WhisperModel(
            self.model_name,
            device=self.device,
            compute_type=self.compute_type,
            cpu_threads=self.cpu_threads,
        )
        print(f"[ASR] Ready: {self.info()}", flush=True)

    def info(self) -> dict[str, Any]:
        return {
            "asr_model": self.model_name,
            "asr_device": self.device,
            "asr_compute_type": self.compute_type,
            "asr_cpu_threads": self.cpu_threads,
            **_torch_cuda_info(),
        }

    def warmup(self) -> None:
        audio = np.zeros(16000, dtype=np.float32)
        _ = self.transcribe_dutch(audio, mode="fast")

    def transcribe_dutch(self, audio_16k: np.ndarray, prompt: str | None = None, mode: str = "balanced") -> str:
        """Transcribe one 16 kHz float32 mono audio segment."""
        if audio_16k.size < 1600:
            return ""

        mode = (mode or "balanced").lower()
        if mode == "fast":
            beam_size = int(os.getenv("FAST_ASR_BEAM_SIZE", "1"))
        elif mode == "quality":
            beam_size = int(os.getenv("QUALITY_ASR_BEAM_SIZE", "3"))
        else:
            beam_size = int(os.getenv("BALANCED_ASR_BEAM_SIZE", os.getenv("ASR_BEAM_SIZE", "2")))

        segments, _info = self.model.transcribe(
            audio_16k,
            language=os.getenv("ASR_LANGUAGE", "nl"),
            task="transcribe",
            beam_size=beam_size,
            best_of=1,
            temperature=0.0,
            vad_filter=False,
            condition_on_previous_text=os.getenv("ASR_CONDITION_ON_PREVIOUS_TEXT", "0") == "1",
            initial_prompt=prompt,
            no_speech_threshold=float(os.getenv("ASR_NO_SPEECH_THRESHOLD", "0.6")),
            compression_ratio_threshold=float(os.getenv("ASR_COMPRESSION_RATIO_THRESHOLD", "2.4")),
            without_timestamps=True,
        )
        text = " ".join(seg.text.strip() for seg in segments).strip()
        return _clean_text(text)


def _clean_text(text: str) -> str:
    text = " ".join(text.replace("\n", " ").split())
    return text.strip()


@lru_cache(maxsize=1)
def get_asr_engine() -> TranscriptionEngine:
    return TranscriptionEngine()
