from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.schemas import ClientConfigMessage
from app.ws_session import ConfigIgnored, _parse_config


def test_client_config_validates_and_normalizes_context() -> None:
    config = ClientConfigMessage.model_validate(
        {
            "type": "config",
            "sample_rate": 16000,
            "source_lang": "nl",
            "target_lang": "en",
            "mode": "fast",
            "context_prompt": "  Feyenoord   Rotterdam  ",
        }
    ).to_client_config()
    assert config.context_prompt == "Feyenoord Rotterdam"


def test_client_config_rejects_bad_mode() -> None:
    with pytest.raises(ValidationError):
        ClientConfigMessage.model_validate({"type": "config", "sample_rate": 16000, "mode": "turbo"})


def test_parse_config_ignores_non_config_json() -> None:
    with pytest.raises(ConfigIgnored):
        _parse_config('{"type":"flush"}')
