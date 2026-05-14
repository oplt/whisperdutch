from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from pathlib import Path


DEFAULT_GLOSSARY: dict[str, str] = {
    # Intentionally empty by default.
    # Domain/entity corrections should be user-controlled via config/glossary.tsv.
}

CONNECTOR_ENDINGS = {
    "en", "maar", "want", "omdat", "doordat", "terwijl", "dat", "als", "dus",
    "of", "waarbij", "waardoor", "zodat", "wanneer", "toen", "dan", "ook",
    "met", "voor", "van", "naar", "in", "op", "bij", "over", "onder", "tussen",
}


@dataclass
class DutchTextProcessor:
    glossary_enabled: bool = field(default_factory=lambda: _env_bool("GLOSSARY_ENABLED", False))
    custom_glossary_path: str = field(default_factory=lambda: os.getenv("GLOSSARY_PATH", ""))
    _rules: list[tuple[re.Pattern[str], str]] = field(default_factory=list, init=False)

    def __post_init__(self) -> None:
        rules = dict(DEFAULT_GLOSSARY)
        if self.custom_glossary_path:
            rules.update(_load_custom_glossary(Path(self.custom_glossary_path)))
        self._rules = [(re.compile(pattern, flags=re.IGNORECASE), repl) for pattern, repl in rules.items()]

    def normalize(self, text: str) -> str:
        text = " ".join((text or "").replace("\n", " ").split())
        text = re.sub(r"\s+([,.;:!?])", r"\1", text)
        text = re.sub(r"([¿¡])\s+", r"\1", text)
        text = re.sub(r"\s+", " ", text).strip()
        return text

    def correct(self, text: str) -> str:
        text = self.normalize(text)
        if not self.glossary_enabled or not text:
            return text
        for pattern, repl in self._rules:
            text = pattern.sub(repl, text)
        return self.normalize(text)

    def ends_with_connector(self, text: str) -> bool:
        words = re.findall(r"\b[\wÀ-ÿ'-]+\b", (text or "").lower(), re.UNICODE)
        return bool(words and words[-1] in CONNECTOR_ENDINGS)


def _env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _load_custom_glossary(path: Path) -> dict[str, str]:
    """Load a simple TSV glossary: regex<TAB>replacement."""
    if not path.exists():
        return {}
    rules: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "\t" not in line:
            continue
        pattern, repl = line.split("\t", 1)
        rules[pattern.strip()] = repl.strip()
    return rules


_processor: DutchTextProcessor | None = None


def get_text_processor() -> DutchTextProcessor:
    global _processor
    if _processor is None:
        _processor = DutchTextProcessor()
    return _processor
