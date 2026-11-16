"""Cross-platform binary and directory helpers for Marionette browser tests."""

from __future__ import annotations

import os
from pathlib import Path
import shutil

ROOT = Path(__file__).resolve().parents[2]
EXE_SUFFIX = ".exe" if os.name == "nt" else ""


def ensure_dir(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    return path


def workspace_dir() -> Path:
    return ensure_dir(ROOT / ".cache" / "browser-tests")


def artifacts_dir(sub: str | None = None) -> Path:
    target = ROOT / "artifacts" / sub if sub else ROOT / "artifacts"
    return ensure_dir(target)


def default_argon_binary() -> Path:
    return ROOT / ".cache" / "argon-dev" / "runtime" / f"argon{EXE_SUFFIX}"


def default_firefox_binary() -> str:
    if os.name == "nt":
        candidates = []
        for env_name in ("ProgramFiles", "ProgramFiles(x86)", "LOCALAPPDATA"):
            base = os.environ.get(env_name)
            if base:
                candidates.append(Path(base) / "Mozilla Firefox" / "firefox.exe")
        for candidate in candidates:
            if candidate.is_file():
                return str(candidate)
        which = shutil.which("firefox")
        if which:
            return which
        return str(Path(os.environ.get("ProgramFiles", r"C:\Program Files")) / "Mozilla Firefox" / "firefox.exe")

    linux_default = Path("/usr/lib/firefox/firefox")
    if linux_default.is_file():
        return str(linux_default)
    return shutil.which("firefox") or str(linux_default)


def argon_test_binary(fallback_to_firefox: bool = False) -> str:
    configured = os.environ.get("ARGON_TEST_BINARY")
    if configured:
        return configured
    if fallback_to_firefox:
        return default_firefox_binary()
    return str(default_argon_binary())
