#!/usr/bin/env python3
"""Inventory host hardware, package versions, and layered runtime configuration.

Does not load ASR/translation weights by default. Pass --probe-models to call
engine.info() after load (explicit, network/disk heavy when models are missing).
"""

from __future__ import annotations

import argparse
import importlib.metadata
import json
import os
import platform
import re
import shutil
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

SCRIPT_DIR = Path(__file__).resolve().parent
BACKEND_DIR = SCRIPT_DIR.parent
PROJECT_ROOT = BACKEND_DIR.parent
ARTIFACTS_DIR = PROJECT_ROOT / "docs" / "benchmark-artifacts"

# Keys that distinguish documented defaults across layers.
CONFIG_KEYS = (
    "ASR_DEVICE",
    "ASR_MODEL",
    "FAST_ASR_MODEL",
    "BALANCED_ASR_MODEL",
    "QUALITY_ASR_MODEL",
    "ASR_COMPUTE_TYPE",
    "ASR_LANGUAGE",
    "ASR_BEAM_SIZE",
    "ASR_CPU_THREADS",
    "ASR_VAD_FILTER",
    "ASR_VAD_MIN_SILENCE_MS",
    "ASR_VAD_MIN_SPEECH_MS",
    "ASR_VAD_SPEECH_PAD_MS",
    "ASR_WORD_TIMESTAMPS",
    "PARTIAL_ASR_WORD_TIMESTAMPS",
    "FINAL_ASR_WORD_TIMESTAMPS",
    "PARTIAL_ASR_ENABLED",
    "PARTIAL_ASR_INTERVAL_MS",
    "PARTIAL_ASR_MAX_SECONDS",
    "INFERENCE_ASR_MAX_CONCURRENT",
    "INFERENCE_TRANSLATION_MAX_CONCURRENT",
    "INFERENCE_ASR_MAX_PENDING",
    "INFERENCE_TRANSLATION_MAX_PENDING",
    "TRANSLATION_ENGINE",
    "TRANSLATION_MODEL_FAMILY",
    "TRANSLATION_MODEL",
    "TRANSLATION_TOKENIZER",
    "TRANSFORMERS_TRANSLATION_MODEL",
    "TRANSLATION_DEVICE",
    "TRANSLATION_COMPUTE_TYPE",
    "TRANSLATION_BEAM_SIZE",
    "TRANSLATION_CACHE_ITEMS",
    "TRANSLATION_CACHE_BACKEND",
    "TRANSLATION_CACHE_TTL_SECONDS",
    "LOCAL_MODELS_ONLY",
    "STARTUP_WARMUP_STRATEGY",
    "EXPORT_ALIGNMENT_ENGINE",
    "WHISPERX_ASR_MODEL",
)


def git_metadata() -> dict[str, str | None]:
    def run(*args: str) -> str | None:
        try:
            completed = subprocess.run(
                ["git", "-C", str(PROJECT_ROOT), *args],
                check=True,
                capture_output=True,
                text=True,
            )
            return completed.stdout.strip()
        except (subprocess.CalledProcessError, FileNotFoundError):
            return None

    return {
        "commit": run("rev-parse", "HEAD"),
        "commit_short": run("rev-parse", "--short", "HEAD"),
        "branch": run("rev-parse", "--abbrev-ref", "HEAD"),
        "subject": run("log", "-1", "--format=%s"),
    }


def parse_env_file(path: Path) -> dict[str, str]:
    """Parse KEY=VALUE lines; ignore comments/blank. No shell expansion."""
    values: dict[str, str] = {}
    if not path.is_file():
        return values
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export ") :].strip()
        if "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {'"', "'"}:
            value = value[1:-1]
        values[key] = value
    return values


def parse_bash_exports(path: Path, keys: tuple[str, ...]) -> dict[str, str]:
    """Extract export KEY="${KEY:-default}" defaults from a launcher script."""
    text = path.read_text(encoding="utf-8") if path.is_file() else ""
    found: dict[str, str] = {}
    for key in keys:
        pattern = rf'export\s+{re.escape(key)}="\$\{{{re.escape(key)}:-([^}}]*)\}}"'
        match = re.search(pattern, text)
        if match:
            found[key] = match.group(1)
            continue
        pattern_plain = rf'export\s+{re.escape(key)}="([^"$]*)"'
        match_plain = re.search(pattern_plain, text)
        if match_plain and "${" not in match_plain.group(1):
            found[key] = match_plain.group(1)
    return found


def parse_native_host_setdefault(path: Path) -> dict[str, str]:
    text = path.read_text(encoding="utf-8") if path.is_file() else ""
    found: dict[str, str] = {}
    for match in re.finditer(r'env\.setdefault\("([A-Z0-9_]+)",\s*"([^"]*)"\)', text):
        found[match.group(1)] = match.group(2)
    return found


def package_versions() -> dict[str, str]:
    versions = {
        "python": platform.python_version(),
        "platform": platform.platform(),
        "node": _command_version(["node", "--version"]),
        "npm": _command_version(["npm", "--version"]),
    }
    for package in (
        "faster-whisper",
        "ctranslate2",
        "transformers",
        "tokenizers",
        "fastapi",
        "starlette",
        "uvicorn",
        "pydantic",
        "numpy",
        "torch",
        "ruff",
        "mypy",
        "pytest",
    ):
        try:
            versions[package] = importlib.metadata.version(package)
        except importlib.metadata.PackageNotFoundError:
            versions[package] = "not-installed"
    return versions


def _command_version(command: list[str]) -> str:
    try:
        completed = subprocess.run(command, check=True, capture_output=True, text=True)
        return (completed.stdout or completed.stderr).strip()
    except (subprocess.CalledProcessError, FileNotFoundError):
        return "not-found"


def cuda_metadata() -> dict[str, Any]:
    meta: dict[str, Any] = {
        "nvidia_smi": None,
        "nvcc": shutil.which("nvcc"),
        "cuda_version_json": None,
        "ctranslate2_cuda_device_count": 0,
        "ctranslate2_cuda_available": False,
    }
    version_json = Path("/usr/local/cuda/version.json")
    if version_json.is_file():
        try:
            meta["cuda_version_json"] = json.loads(version_json.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            meta["cuda_version_json"] = {"error": "unreadable"}
    try:
        completed = subprocess.run(
            ["nvidia-smi", "--query-gpu=name,memory.total,driver_version", "--format=csv,noheader"],
            check=True,
            capture_output=True,
            text=True,
        )
        meta["nvidia_smi"] = completed.stdout.strip()
    except (subprocess.CalledProcessError, FileNotFoundError) as exc:
        meta["nvidia_smi_error"] = str(exc)
    try:
        import ctranslate2

        count = int(ctranslate2.get_cuda_device_count())
        meta["ctranslate2_cuda_device_count"] = count
        meta["ctranslate2_cuda_available"] = count > 0
    except Exception as exc:  # noqa: BLE001 - inventory must not crash
        meta["ctranslate2_error"] = str(exc)
    return meta


def host_inventory() -> dict[str, Any]:
    meminfo: dict[str, int] = {}
    try:
        for line in Path("/proc/meminfo").read_text(encoding="utf-8").splitlines():
            if ":" not in line:
                continue
            key, raw = line.split(":", 1)
            parts = raw.strip().split()
            if parts and parts[0].isdigit():
                meminfo[key] = int(parts[0])
    except OSError:
        pass

    cpu_model = None
    try:
        for line in Path("/proc/cpuinfo").read_text(encoding="utf-8").splitlines():
            if line.startswith("model name"):
                cpu_model = line.split(":", 1)[1].strip()
                break
    except OSError:
        pass

    return {
        "uname": platform.uname()._asdict(),
        "logical_cpus": os.cpu_count(),
        "cpu_model": cpu_model,
        "mem_total_kib": meminfo.get("MemTotal"),
        "mem_available_kib": meminfo.get("MemAvailable"),
        "swap_total_kib": meminfo.get("SwapTotal"),
    }


def local_model_artifacts() -> dict[str, Any]:
    models_dir = BACKEND_DIR / "models"
    entries: list[dict[str, Any]] = []
    if models_dir.is_dir():
        for child in sorted(models_dir.iterdir()):
            if not child.is_dir():
                continue
            files = list(child.rglob("*"))
            size = sum(path.stat().st_size for path in files if path.is_file())
            entries.append(
                {
                    "path": str(child.relative_to(BACKEND_DIR)),
                    "file_count": sum(1 for path in files if path.is_file()),
                    "bytes": size,
                }
            )
    hub = Path.home() / ".cache" / "huggingface" / "hub"
    hub_models = []
    if hub.is_dir():
        for child in sorted(hub.iterdir()):
            name = child.name
            if name.startswith("models--") and ("whisper" in name.lower() or "nllb" in name.lower() or "m2m" in name.lower()):
                hub_models.append(name)
    return {"backend_models": entries, "huggingface_hub_relevant": hub_models}


# README table defaults for key knobs (manual snapshot; update when README changes).
README_DEFAULTS = {
    "ASR_MODEL": "large-v3-turbo",
    "ASR_DEVICE": "auto (README table) / cpu via run_gpu.sh",
    "TRANSLATION_MODEL_FAMILY": "nllb",
    "TRANSLATION_MODEL": "models/nllb-200-distilled-600m-ct2",
    "TRANSLATION_TOKENIZER": "facebook/nllb-200-distilled-600M",
}


SENSITIVE_ENV_KEYS = frozenset(
    {
        "DUTCH_SUBTITLE_EXTENSION_ID",
        "DUTCH_SUBTITLE_EXTENSION_PUBLIC_KEY",
        "DUTCH_SUBTITLE_FIREFOX_EXTENSION_ID",
    }
)


def _redact_mapping(values: dict[str, Any]) -> dict[str, Any]:
    redacted: dict[str, Any] = {}
    for key, value in values.items():
        if key in SENSITIVE_ENV_KEYS and value not in (None, ""):
            redacted[key] = "<redacted>"
        else:
            redacted[key] = value
    return redacted


def configuration_layers() -> dict[str, Any]:
    env_example = parse_env_file(BACKEND_DIR / ".env.example")
    env_local = parse_env_file(BACKEND_DIR / ".env")
    launcher = parse_bash_exports(BACKEND_DIR / "run_gpu.sh", CONFIG_KEYS)
    native = parse_native_host_setdefault(PROJECT_ROOT / "native-host" / "start_backend_host.py")
    process_env = {key: os.getenv(key) for key in CONFIG_KEYS if os.getenv(key) is not None}

    # Effective after run_gpu.sh semantics: process env > .env (already sourced into process
    # only if launcher ran) > launcher defaults. Inventory reports each layer separately.
    effective_if_launcher: dict[str, str] = {}
    for key in CONFIG_KEYS:
        if key in process_env and process_env[key] is not None:
            effective_if_launcher[key] = process_env[key]  # type: ignore[assignment]
        elif key in env_local:
            effective_if_launcher[key] = env_local[key]
        elif key in launcher:
            effective_if_launcher[key] = launcher[key]
        elif key in env_example:
            effective_if_launcher[key] = env_example[key]

    conflicts = []
    compare_pairs = (
        ("README", README_DEFAULTS, ".env.example", env_example),
        (".env.example", env_example, "run_gpu.sh", launcher),
        ("run_gpu.sh", launcher, "native-host setdefault", native),
        (".env.example", env_example, "backend/.env", env_local),
    )
    for left_name, left, right_name, right in compare_pairs:
        for key in sorted(set(left) & set(right)):
            if key in SENSITIVE_ENV_KEYS:
                continue
            if left[key] != right[key]:
                conflicts.append(
                    {
                        "key": key,
                        "left": left_name,
                        "left_value": left[key],
                        "right": right_name,
                        "right_value": right[key],
                    }
                )

    return {
        "readme_snapshot": README_DEFAULTS,
        "env_example": {key: env_example.get(key) for key in CONFIG_KEYS if key in env_example},
        "env_local_present": (BACKEND_DIR / ".env").is_file(),
        "env_local": _redact_mapping({key: env_local.get(key) for key in CONFIG_KEYS if key in env_local}),
        "run_gpu_sh_defaults": launcher,
        "native_host_setdefault": native,
        "process_environ_overrides": _redact_mapping(process_env),
        "resolved_preference_order": [
            "explicit process environment / ASR_DEVICE_OVERRIDE",
            "backend/.env (when sourced by run_gpu.sh)",
            "backend/run_gpu.sh export defaults",
            "native-host env.setdefault (only when key unset)",
            "code-level getenv defaults in asr.py / translator.py",
        ],
        "effective_when_run_gpu_with_current_env_file": _redact_mapping(effective_if_launcher),
        "layer_conflicts": conflicts,
        "code_level_notes": {
            "ASR_MODEL_code_default": "large-v3-turbo via asr._resolve_model_name when ASR_MODEL empty",
            "ASR_DEVICE_code_default": "auto via asr._auto_device (cuda if CT2 sees GPU else cpu)",
            "TRANSLATION_MODEL_code_default": "facebook/m2m100_418M path defaults in translator.py before env",
            "per_mode_ASR_MODEL_overrides": "FAST_/BALANCED_/QUALITY_ASR_MODEL advertised; single loaded model unless extended",
            "WhisperModel_num_workers": "not passed; faster-whisper default applies",
            "ASR_CPU_THREADS_default": "4",
        },
    }


def language_capabilities_snapshot() -> dict[str, Any]:
    sys.path.insert(0, str(BACKEND_DIR))
    from app.languages import (  # noqa: E402
        DEFAULT_SOURCE_LANGUAGE,
        DEFAULT_TARGET_LANGUAGE,
        language_catalog,
    )

    catalog = language_catalog()
    return {
        "default_source": DEFAULT_SOURCE_LANGUAGE,
        "default_target": DEFAULT_TARGET_LANGUAGE,
        "catalog_size": len(catalog),
        "codes": [entry["code"] for entry in catalog],
    }


def probe_loaded_models() -> dict[str, Any]:
    sys.path.insert(0, str(BACKEND_DIR))
    from app.asr import get_asr_engine  # noqa: E402
    from app.translator import get_translation_engine  # noqa: E402

    asr = get_asr_engine()
    translator = get_translation_engine()
    return {
        "asr": asr.info(),
        "translation": translator.info(),
    }


def build_inventory(*, probe_models: bool) -> dict[str, Any]:
    report: dict[str, Any] = {
        "inventory": "runtime-baseline",
        "generated_at_utc": datetime.now(UTC).isoformat(),
        "git": git_metadata(),
        "host": host_inventory(),
        "versions": package_versions(),
        "cuda": cuda_metadata(),
        "local_artifacts": local_model_artifacts(),
        "configuration": configuration_layers(),
        "language_capabilities": language_capabilities_snapshot(),
        "probed_models": None,
        "notes": [
            "Do not assume docs/benchmark-artifacts hardware matches this host.",
            "run_gpu.sh defaults ASR_MODEL=small and TRANSLATION to m2m100; .env.example prefers large-v3-turbo + nllb.",
            "CUDA toolkit may be installed while ctranslate2 reports 0 devices (driver/runtime mismatch).",
            "Model probe omitted unless --probe-models.",
        ],
    }
    if probe_models:
        report["probed_models"] = probe_loaded_models()
    return report


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Inventory host and layered runtime configuration.")
    parser.add_argument(
        "--probe-models",
        action="store_true",
        help="Load ASR + translation engines and record engine.info() (explicit real-model step).",
    )
    parser.add_argument("--output", type=Path, help="Write JSON inventory to this path.")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    report = build_inventory(probe_models=args.probe_models)
    ARTIFACTS_DIR.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    output_path = args.output or (ARTIFACTS_DIR / f"runtime-inventory-{timestamp}.json")
    payload = json.dumps(report, indent=2)
    output_path.write_text(payload, encoding="utf-8")
    latest = ARTIFACTS_DIR / "runtime-inventory-latest.json"
    latest.write_text(payload, encoding="utf-8")
    print(json.dumps({"artifact": str(output_path), "latest": str(latest), "probe_models": args.probe_models}, indent=2))


if __name__ == "__main__":
    main()
