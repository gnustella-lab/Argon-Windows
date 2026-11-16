#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Cross-platform host, Python, filesystem, and process helpers for Argon tooling."""
from __future__ import annotations

import ctypes
import importlib
import os
from pathlib import Path
import platform
import re
import shutil
import subprocess
import sys
import time

try:
    import msvcrt
except ImportError:  # pragma: no cover - POSIX hosts
    msvcrt = None


WINDOWS_RESERVED_PATH_RE = re.compile(r"^[A-Za-z]:(?:[\\/][A-Za-z0-9_.-]+)+$")
POSIX_SAFE_PATH_RE = re.compile(r"^/[A-Za-z0-9_./-]+$")
_PYTHON_PROBE_CODE = (
    "import sys; print(sys.executable); "
    "raise SystemExit(0 if sys.version_info >= (3, 9) else 1)"
)


def is_windows() -> bool:
    return sys.platform == "win32" or platform.system() == "Windows"


def is_linux() -> bool:
    return platform.system() == "Linux"


def is_macos() -> bool:
    return platform.system() == "Darwin"


def binary_name(name: str, windows: bool | None = None) -> str:
    """Return the platform executable filename (appending .exe on Windows)."""
    use_windows = is_windows() if windows is None else windows
    if use_windows and not name.lower().endswith(".exe"):
        return f"{name}.exe"
    return name


def shared_library_name(stem: str, system: str | None = None) -> str:
    """Return the platform dynamic library filename for a Rust cdylib stem."""
    target = system or platform.system()
    if target == "Windows":
        return f"{stem}.dll"
    if target == "Darwin":
        return f"lib{stem}.dylib"
    return f"lib{stem}.so"


def is_symlink_or_junction(path: Path) -> bool:
    """Return True if path is a symlink or (on Windows) an NTFS junction."""
    if path.is_symlink():
        return True
    is_junction = getattr(path, "is_junction", None)
    if callable(is_junction):
        try:
            return bool(is_junction())
        except OSError:
            return False
    return False


def no_symlinks(path: str | Path, error_cls=ValueError) -> Path:
    """Reject '..' traversal, symlinks, and NTFS junctions in any component."""
    resolved = Path(path).absolute()
    if ".." in resolved.parts or any(
        is_symlink_or_junction(p) for p in (resolved, *resolved.parents)
    ):
        raise error_cls(f"Unsafe path (traversal or symlink): {resolved}")
    return resolved


def norm_path_key(path: Path) -> str:
    """Case-fold paths on Windows so equality checks stay accurate."""
    return os.path.normcase(str(path))


def is_safe_workspace_path(path: Path, repo: Path) -> bool:
    """Validate an ASCII, space-free, dedicated workspace path outside repo/home/root."""
    raw = str(path)
    if is_windows():
        normalized = raw.rstrip("\\/")
        if path == Path(path.anchor) or len(path.parts) <= 1:
            return False
        if not WINDOWS_RESERVED_PATH_RE.fullmatch(normalized):
            return False
    else:
        if path == Path("/") or not POSIX_SAFE_PATH_RE.fullmatch(raw):
            return False

    key = norm_path_key(path)
    disallowed = {
        norm_path_key(Path(path.anchor)) if path.anchor else "/",
        norm_path_key(Path.home()),
        norm_path_key(repo),
        *(norm_path_key(parent) for parent in repo.parents),
    }
    return key not in disallowed


def memory_gib() -> float | None:
    """Return physical RAM in GiB across Linux, Windows, and POSIX systems."""
    if is_windows():
        try:
            class MEMORYSTATUSEX(ctypes.Structure):
                _fields_ = [
                    ("dwLength", ctypes.c_ulong),
                    ("dwMemoryLoad", ctypes.c_ulong),
                    ("ullTotalPhys", ctypes.c_ulonglong),
                    ("ullAvailPhys", ctypes.c_ulonglong),
                    ("ullTotalPageFile", ctypes.c_ulonglong),
                    ("ullAvailPageFile", ctypes.c_ulonglong),
                    ("ullTotalVirtual", ctypes.c_ulonglong),
                    ("ullAvailVirtual", ctypes.c_ulonglong),
                    ("ullAvailExtendedVirtual", ctypes.c_ulonglong),
                ]

            stat = MEMORYSTATUSEX()
            stat.dwLength = ctypes.sizeof(stat)
            kernel32 = getattr(ctypes, "windll", None)
            if kernel32 and kernel32.kernel32.GlobalMemoryStatusEx(ctypes.byref(stat)):
                return round(stat.ullTotalPhys / (1024 ** 3), 2)
        except (AttributeError, OSError):
            pass

    try:
        with open("/proc/meminfo", encoding="utf-8") as handle:
            for line in handle:
                if line.startswith("MemTotal:"):
                    return round(int(line.split()[1]) / 1024 / 1024, 2)
    except OSError:
        pass

    if hasattr(os, "sysconf"):
        try:
            pages = os.sysconf("SC_PHYS_PAGES")
            page_size = os.sysconf("SC_PAGE_SIZE")
            if isinstance(pages, int) and isinstance(page_size, int) and pages > 0 and page_size > 0:
                return round((pages * page_size) / (1024 ** 3), 2)
        except (ValueError, OSError):
            pass
    return None


def is_windows_store_stub(path: str | Path, windows: bool | None = None) -> bool:
    """Return True if path points to the 0-byte Microsoft Store WindowsApps alias."""
    use_windows = is_windows() if windows is None else windows
    if not use_windows or not path:
        return False
    raw = str(path)
    lowered_parts = [part.lower() for part in Path(raw).parts]
    if "microsoft" in lowered_parts and "windowsapps" in lowered_parts:
        return True
    try:
        candidate = Path(raw)
        stat = candidate.lstat()
        if candidate.name.lower().startswith("python") and stat.st_size == 0:
            return True
    except OSError:
        return False
    return False


def which_all(name: str, path_env: str | None = None) -> list[Path]:
    """Return all executable matches for name in PATH order."""
    search_path = os.environ.get("PATH", "") if path_env is None else path_env
    if not search_path:
        return []
    candidates = [name]
    if is_windows() and not Path(name).suffix:
        exts = [ext for ext in os.environ.get("PATHEXT", ".EXE;.CMD;.BAT").split(";") if ext]
        candidates = [name + ext.lower() for ext in exts] + [name + ext.upper() for ext in exts] + [name]
        # Deduplicate while preserving order.
        seen_names = set()
        deduped = []
        for item in candidates:
            low = item.lower()
            if low not in seen_names:
                seen_names.add(low)
                deduped.append(item)
        candidates = deduped

    results: list[Path] = []
    seen_paths: set[str] = set()
    for directory in search_path.split(os.pathsep):
        if not directory:
            continue
        base = Path(directory.strip('"'))
        for cand_name in candidates:
            candidate = base / cand_name
            try:
                if candidate.is_file() and (is_windows() or os.access(candidate, os.X_OK)):
                    key = norm_path_key(candidate)
                    if key not in seen_paths:
                        seen_paths.add(key)
                        results.append(candidate)
            except OSError:
                continue
    return results


def _probe_python_executable(candidate: str | Path, min_version: tuple[int, int] = (3, 9)) -> str | None:
    path_str = str(candidate)
    if is_windows_store_stub(path_str):
        return None
    probe_code = (
        f"import sys; print(sys.executable); "
        f"raise SystemExit(0 if sys.version_info >= {min_version!r} else 1)"
    )
    try:
        completed = subprocess.run(
            [path_str, "-c", probe_code],
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=5,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if completed.returncode != 0:
        return None
    resolved = completed.stdout.strip().splitlines()
    exe = resolved[-1].strip() if resolved else path_str
    if not exe or is_windows_store_stub(exe) or not Path(exe).is_file():
        return None
    return exe


def find_mozilla_build() -> Path | None:
    """Locate MozillaBuild root directory on Windows if installed."""
    if not is_windows():
        return None
    candidates = []
    if os.environ.get("MOZILLABUILD"):
        candidates.append(Path(os.environ["MOZILLABUILD"]))
    system_drive = os.environ.get("SystemDrive", "C:")
    candidates.extend([
        Path(f"{system_drive}\\mozilla-build"),
        Path("C:\\mozilla-build"),
        Path("D:\\mozilla-build"),
    ])
    seen = set()
    for root in candidates:
        key = norm_path_key(root)
        if key in seen:
            continue
        seen.add(key)
        if root.is_dir() and (
            (root / "start-shell.bat").is_file()
            or (root / "msys2" / "usr" / "bin" / "bash.exe").is_file()
            or (root / "msys2" / "usr" / "bin" / "sh.exe").is_file()
        ):
            return root
    return None


def mozilla_build_version(mb_root: Path | None = None) -> str | None:
    """Return the MozillaBuild VERSION string if present."""
    root = mb_root or find_mozilla_build()
    if not root:
        return None
    version_file = root / "VERSION"
    if version_file.is_file():
        try:
            return version_file.read_text(encoding="utf-8").strip() or None
        except OSError:
            return None
    return None


def find_vswhere() -> Path | None:
    """Locate Visual Studio's vswhere.exe on Windows."""
    if not is_windows():
        return None
    on_path = shutil.which("vswhere.exe") or shutil.which("vswhere")
    if on_path:
        return Path(on_path)
    for env_key in ("ProgramFiles(x86)", "ProgramFiles"):
        base = os.environ.get(env_key)
        if base:
            candidate = Path(base) / "Microsoft Visual Studio" / "Installer" / "vswhere.exe"
            if candidate.is_file():
                return candidate
    return None


def find_windows_redist_dir(mozbuild_state: Path | None = None) -> Path | None:
    """Return a valid MSVC CRT redistributable directory containing vcruntime140.dll, or None."""
    if not is_windows():
        return None

    def _valid_crt_dir(path: Path) -> Path | None:
        if path.is_dir() and (path / "vcruntime140.dll").is_file():
            return path
        return None

    explicit = os.environ.get("WIN32_REDIST_DIR")
    if explicit:
        found = _valid_crt_dir(Path(explicit))
        if found:
            return found

    candidates: list[Path] = []
    vc_redist = os.environ.get("VCToolsRedistDir")
    if vc_redist:
        x64_dir = Path(vc_redist) / "x64"
        if x64_dir.is_dir():
            candidates.extend(sorted(x64_dir.glob("Microsoft.VC14*.CRT"), reverse=True))

    vswhere = find_vswhere()
    if vswhere:
        try:
            res = subprocess.run(
                [
                    str(vswhere),
                    "-latest",
                    "-products",
                    "*",
                    "-requires",
                    "Microsoft.VisualCpp.Redist.14.Latest",
                    "-property",
                    "installationPath",
                ],
                capture_output=True,
                text=True,
                encoding="utf-8",
                timeout=5,
                check=False,
            )
            if res.returncode == 0:
                for line in res.stdout.splitlines():
                    install = Path(line.strip())
                    msvc_redist = install / "VC" / "Redist" / "MSVC"
                    if msvc_redist.is_dir():
                        for ver_dir in sorted(msvc_redist.iterdir(), reverse=True):
                            x64_dir = ver_dir / "x64"
                            if x64_dir.is_dir():
                                candidates.extend(sorted(x64_dir.glob("Microsoft.VC14*.CRT"), reverse=True))
        except (OSError, subprocess.SubprocessError):
            pass

    state_dir = mozbuild_state or Path(os.environ.get("MOZBUILD_STATE_PATH") or Path.home() / ".mozbuild")
    vs_dir = state_dir / "vs"
    if vs_dir.is_dir():
        candidates.extend(sorted((vs_dir / "redist" / "x64").glob("microsoft.vc14*.crt"), reverse=True))
        msvc_redist = vs_dir / "VC" / "Redist" / "MSVC"
        if msvc_redist.is_dir():
            for ver_dir in sorted(msvc_redist.iterdir(), reverse=True):
                x64_dir = ver_dir / "x64"
                if x64_dir.is_dir():
                    candidates.extend(sorted(x64_dir.glob("Microsoft.VC14*.CRT"), reverse=True))

    for candidate in candidates:
        valid = _valid_crt_dir(candidate)
        if valid:
            return valid
    return None


def _windows_python_install_candidates() -> list[Path]:
    """Return standard Windows Python 3 installation paths (newest version first)."""
    roots = []
    local_app_data = os.environ.get("LOCALAPPDATA")
    if local_app_data:
        roots.append(Path(local_app_data) / "Programs" / "Python")
    for env_var in ("ProgramFiles", "ProgramW6432"):
        if os.environ.get(env_var):
            roots.append(Path(os.environ[env_var]))
    roots.append(Path(os.environ.get("SystemDrive", "C:") + "\\"))

    found: list[tuple[tuple[int, ...], Path]] = []
    for root in roots:
        if not root.is_dir():
            continue
        try:
            for entry in root.iterdir():
                if not entry.is_dir():
                    continue
                match = re.fullmatch(r"Python3(\d+)(?:-\d+)?", entry.name, re.IGNORECASE)
                if match:
                    exe = entry / "python.exe"
                    if exe.is_file():
                        found.append(((3, int(match.group(1))), exe))
        except OSError:
            continue
    found.sort(key=lambda item: item[0], reverse=True)
    return [exe for _, exe in found]


def find_python(*, min_version: tuple[int, int] = (3, 9), include_current: bool = False) -> str | None:
    """Discover a real Python >= min_version interpreter, filtering out WindowsApps Store aliases."""
    explicit = os.environ.get("ARGON_PYTHON", "").strip()
    if explicit:
        explicit_path = Path(explicit)
        if explicit_path.is_file():
            probed = _probe_python_executable(explicit_path, min_version=min_version)
            if probed:
                return probed
        for match in which_all(explicit):
            probed = _probe_python_executable(match, min_version=min_version)
            if probed:
                return probed

    candidates: list[str | Path] = []

    if is_windows():
        for env_key in ("VIRTUAL_ENV", "CONDA_PREFIX"):
            base = os.environ.get(env_key)
            if base:
                candidates.append(Path(base) / "Scripts" / "python.exe")

        probe_code = (
            f"import sys; print(sys.executable); "
            f"raise SystemExit(0 if sys.version_info >= {min_version!r} else 1)"
        )
        for py_launcher in which_all("py"):
            if is_windows_store_stub(py_launcher):
                continue
            try:
                res = subprocess.run(
                    [str(py_launcher), "-3", "-c", probe_code],
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    timeout=5,
                    check=False,
                )
                if res.returncode == 0:
                    exe = res.stdout.strip().splitlines()[-1].strip()
                    if exe and not is_windows_store_stub(exe) and Path(exe).is_file():
                        return exe
            except (OSError, subprocess.SubprocessError):
                pass

        for cmd_name in ("python3", "python"):
            candidates.extend(which_all(cmd_name))

        mb = find_mozilla_build()
        if mb:
            candidates.extend([
                mb / "python3" / "python3.exe",
                mb / "python3" / "python.exe",
            ])

        candidates.extend(_windows_python_install_candidates())
    else:
        venv = os.environ.get("VIRTUAL_ENV")
        if venv:
            candidates.extend([Path(venv) / "bin" / "python3", Path(venv) / "bin" / "python"])
        for cmd_name in ("python3", "python"):
            candidates.extend(which_all(cmd_name))

    seen: set[str] = set()
    for candidate in candidates:
        cand_path = Path(candidate)
        key = norm_path_key(cand_path)
        if key in seen:
            continue
        seen.add(key)
        if not cand_path.is_file() or is_windows_store_stub(cand_path):
            continue
        probed = _probe_python_executable(cand_path, min_version=min_version)
        if probed:
            return probed

    if include_current and sys.executable:
        return _probe_python_executable(sys.executable, min_version=min_version)
    return None


def only_windows_store_python_on_path() -> bool:
    """Return True on Windows when PATH resolves python/python3 only to Store stubs."""
    if not is_windows():
        return False
    matches = which_all("python3") + which_all("python")
    return bool(matches) and all(is_windows_store_stub(match) for match in matches)


def rust_host_target(rustc: str | Path | None = None) -> str:
    """Return the Rust host target triple using `rustc -vV` with platform fallback."""
    rustc_bin = str(rustc) if rustc else shutil.which(binary_name("rustc"))
    if rustc_bin:
        try:
            out = subprocess.check_output(
                [rustc_bin, "-vV"],
                text=True,
                encoding="utf-8",
                timeout=5,
            )
            for line in out.splitlines():
                if line.startswith("host:"):
                    triple = line.split(":", 1)[1].strip()
                    if triple:
                        return triple
        except (OSError, subprocess.SubprocessError):
            pass

    system = platform.system()
    machine = platform.machine().lower()
    arch_map = {
        "amd64": "x86_64",
        "x86_64": "x86_64",
        "x64": "x86_64",
        "arm64": "aarch64",
        "aarch64": "aarch64",
    }
    arch = arch_map.get(machine, machine or "x86_64")
    if system == "Windows":
        return f"{arch}-pc-windows-msvc"
    if system == "Darwin":
        return f"{arch}-apple-darwin"
    return f"{arch}-unknown-linux-gnu"


def acquire_file_lock(handle) -> None:
    """Acquire a non-blocking exclusive advisory/OS lock on an open binary file handle."""
    if msvcrt is not None and is_windows():
        handle.seek(0)
        msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
    else:
        locker = importlib.import_module("fcntl")
        locker.flock(handle.fileno(), locker.LOCK_EX | locker.LOCK_NB)


def release_file_lock(handle) -> None:
    """Release an acquired file lock safely before closing the handle."""
    if msvcrt is not None and is_windows():
        try:
            handle.seek(0)
            msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
        except OSError:
            pass
    else:
        try:
            locker = importlib.import_module("fcntl")
            locker.flock(handle.fileno(), locker.LOCK_UN)
        except (ImportError, OSError):
            pass


def write_text_lf(path: str | Path, text: str) -> None:
    """Write UTF-8 text preserving LF line endings across all platforms."""
    with open(path, "w", encoding="utf-8", newline="\n") as handle:
        handle.write(text)


def replace_path(src: Path, dst: Path, retries: int = 5, delay: float = 0.05) -> None:
    """Replace dst with src, retrying brief Windows sharing violations."""
    attempts = retries if is_windows() else 1
    for attempt in range(attempts):
        try:
            src.replace(dst)
            return
        except PermissionError:
            if attempt + 1 >= attempts:
                raise
            time.sleep(delay * (attempt + 1))


def rmtree_retry(path: Path, retries: int = 5, delay: float = 0.05) -> None:
    """Remove a directory tree cleanly, handling Windows read-only bits and transient locks."""
    if not path.exists() and not path.is_symlink():
        return

    def _on_error(func, target, _exc):
        try:
            os.chmod(target, 0o700)
            func(target)
        except OSError:
            raise

    attempts = retries if is_windows() else 1
    for attempt in range(attempts):
        try:
            if sys.version_info >= (3, 12):
                shutil.rmtree(path, onexc=lambda func, target, exc: _on_error(func, target, exc))
            else:
                shutil.rmtree(path, onerror=_on_error)
            return
        except PermissionError:
            if attempt + 1 >= attempts:
                raise
            time.sleep(delay * (attempt + 1))
