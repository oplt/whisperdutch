from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator


@dataclass(frozen=True)
class ClientConfig:
    sample_rate: int = 16000
    source_lang: str = "nl"
    target_lang: str = "en"
    mode: str = "fast"  # fast | balanced | quality
    context_prompt: str = ""
    reconnect_count: int = 0


class ClientConfigMessage(BaseModel):
    type: Literal["config"]
    sample_rate: int = Field(default=16000)
    source_lang: Literal["nl"] = "nl"
    target_lang: Literal["en"] = "en"
    mode: Literal["fast", "balanced", "quality"] = "fast"
    context_prompt: str = Field(default="", max_length=600)
    reconnect_count: int = Field(default=0, ge=0)

    @field_validator("sample_rate")
    @classmethod
    def validate_sample_rate(cls, value: int) -> int:
        if value != 16000:
            raise ValueError("sample_rate must be 16000")
        return value

    def to_client_config(self) -> ClientConfig:
        return ClientConfig(
            sample_rate=self.sample_rate,
            source_lang=self.source_lang,
            target_lang=self.target_lang,
            mode=self.mode,
            context_prompt=" ".join(self.context_prompt.split()),
            reconnect_count=self.reconnect_count,
        )


class ClientLog(BaseModel):
    level: str = Field(default="info", pattern="^(debug|info|warn|warning|error)$")
    source: str = "frontend"
    message: str
    context: dict[str, Any] = Field(default_factory=dict)


class GlossaryRule(BaseModel):
    pattern: str = Field(min_length=1, max_length=160)
    replacement: str = Field(max_length=160)


class GlossaryUpdate(BaseModel):
    rules: list[GlossaryRule] = Field(default_factory=list, max_length=200)


class PrivacyUpdate(BaseModel):
    log_transcript_text: bool = False
