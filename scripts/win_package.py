#!/usr/bin/env python3
# SPDX-License-Identifier: MPL-2.0
"""Windows distribution packaging and verification helpers for Argon Browser.

Produces and verifies:
  1. Self-contained Windows portable ZIP archives (`*.win64-portable.zip`) with
     a native Windows launcher (`argon-portable.exe`) that confines all profile,
     cache, and remoting state inside `<portable_root>\\data\\`.
  2. Argon NSIS installers (`*.win64.installer.exe`) built from
     `packaging/windows/argon-installer.nsi` and `browser/branding/argon/`,
     preserving user profiles across upgrades/uninstalls and registering
     Windows Capabilities without hijacking default browser settings.
  3. Standard `SHA256SUMS` manifests and cross-platform package verification.
"""

from __future__ import annotations

import argparse
import configparser
import glob
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import struct
import subprocess
import sys
import tarfile
import tempfile
import zipfile

REPO = Path(__file__).resolve().parents[1]
WINDOWS_DIR = REPO / "packaging" / "windows"
PORTABLE_DIR = WINDOWS_DIR / "portable"
BRANDING_DIR = REPO / "browser" / "branding" / "argon"
FLATPAK_DIR = REPO / "packaging" / "flatpak"

APP_NAME = "argon"
WINDOWS_EXE_NAME = "argon.exe"
PORTABLE_LAUNCHER_NAME = "argon-portable.exe"
WINDOWS_ARCH = "win64"

REQUIRED_OMNI_ENTRIES = (
    "chrome/browser/content/browser/argon/argon.css",
    "chrome/browser/content/browser/argon/argon-controller.mjs",
    "chrome/browser/content/browser/argon/adblock/service.sys.mjs",
    "chrome/browser/content/browser/argon/adblock/ublock-engine.sys.mjs",
    "chrome/browser/content/browser/argon/settings.html",
    "chrome/browser/content/browser/argon/onboarding.html",
    "chrome/browser/content/browser/argon/workspaces.mjs",
    "chrome/browser/content/browser/argon/shortcuts.mjs",
)

ADBLOCK_DLL_NAME = "argon_adblock.dll"
ADBLOCK_DLL_OMNI_PATH = f"chrome/browser/content/browser/argon/adblock/{ADBLOCK_DLL_NAME}"

NSIS_SCRIPT_NAME = "argon-installer.nsi"
REQUIRED_NSIS_BRANDING_FILES = (
    "branding.nsi",
    "firefox.ico",
    "document.ico",
    "document_pdf.ico",
    "pbmode.ico",
    "wizHeader.bmp",
    "wizHeaderRTL.bmp",
    "wizWatermark.bmp",
)

PORTABLE_DATA_SUBDIRS = ("profile", "app-data", "local-app-data", "home")


class WinPackageError(Exception):
    """Raised when Windows packaging or verification fails closed."""


def _no_symlinks(path: Path | str) -> Path:
    resolved = Path(path).absolute()
    if ".." in resolved.parts or any(p.is_symlink() for p in [resolved, *resolved.parents]):
        raise WinPackageError(f"Unsafe path (traversal or symlink): {resolved}")
    return resolved


def sha256_file(path: Path | str) -> str:
    """Return lowercase hex SHA-256 digest of a file."""
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def canonical_policies_source() -> Path:
    preferred = REPO / "settings" / "distribution" / "policies.json"
    if preferred.is_file():
        return preferred
    fallback = FLATPAK_DIR / "policies.json"
    if fallback.is_file():
        return fallback
    raise WinPackageError("Missing distribution policies.json")


def read_windows_application_ini(app_ini_path: Path | str) -> configparser.ConfigParser:
    """Validate and parse application.ini, refusing unbranded Firefox/Mozilla runtimes."""
    ini_path = Path(app_ini_path)
    if not ini_path.is_file():
        raise WinPackageError(f"Missing application.ini: {ini_path}")
    parser = configparser.ConfigParser()
    parser.read(ini_path, encoding="utf-8")
    name = parser.get("App", "Name", fallback="")
    vendor = parser.get("App", "Vendor", fallback="")
    if name != "Argon" or vendor != "Argon":
        raise WinPackageError(f"{ini_path} does not have Argon application.ini identity")
    return parser


def _copy_isolated(src: Path, dst: Path) -> None:
    if dst.exists() or dst.is_symlink():
        dst.unlink()
    shutil.copy2(src, dst)


def _link_or_copy(src: str | Path, dst: str | Path) -> str | Path:
    src_path = Path(src)
    target = src_path.resolve() if src_path.is_symlink() and src_path.exists() else src_path
    if target.is_file() and not src_path.is_symlink():
        try:
            os.link(target, dst)
            return dst
        except OSError:
            pass
    return shutil.copy2(target, dst)


def install_windows_runtime_settings(app_dir: Path | str) -> list[str]:
    """Install Argon policies, LibreWolf cfg, and AutoConfig loader into a Windows runtime."""
    root = Path(app_dir)
    installed: list[str] = []

    dist_dir = root / "distribution"
    dist_dir.mkdir(parents=True, exist_ok=True)
    _copy_isolated(canonical_policies_source(), dist_dir / "policies.json")
    installed.append("distribution/policies.json")

    lw_cfg = REPO / "settings" / "librewolf.cfg"
    if lw_cfg.is_file():
        _copy_isolated(lw_cfg, root / "librewolf.cfg")
        installed.append("librewolf.cfg")

    local_settings = REPO / "settings" / "defaults" / "pref" / "local-settings.js"
    if local_settings.is_file():
        pref_dir = root / "defaults" / "pref"
        pref_dir.mkdir(parents=True, exist_ok=True)
        _copy_isolated(local_settings, pref_dir / "local-settings.js")
        installed.append("defaults/pref/local-settings.js")

    license_file = REPO / "LICENSE"
    if license_file.is_file():
        _copy_isolated(license_file, root / "LICENSE")
        installed.append("LICENSE")

    return installed


def _detect_adblock_dll(app_dir: Path) -> bool:
    unpacked_candidates = (
        app_dir / ADBLOCK_DLL_NAME,
        app_dir / "browser" / "chrome" / "browser" / "content" / "browser" / "argon" / "adblock" / ADBLOCK_DLL_NAME,
        app_dir / "browser" / "base" / "content" / "argon" / "adblock" / ADBLOCK_DLL_NAME,
    )
    if any(p.is_file() for p in unpacked_candidates):
        return True
    omni_path = app_dir / "browser" / "omni.ja"
    if omni_path.is_file():
        try:
            with zipfile.ZipFile(omni_path) as zf:
                return ADBLOCK_DLL_OMNI_PATH in set(zf.namelist())
        except zipfile.BadZipFile:
            return False
    return False


def _stage_visual_elements(app_dir: Path) -> None:
    """Ensure Windows Start tile manifest and tile PNGs are staged in the runtime."""
    if not BRANDING_DIR.is_dir():
        return
    src_manifest = BRANDING_DIR / "firefox.VisualElementsManifest.xml"
    dst_manifest = app_dir / f"{APP_NAME}.VisualElementsManifest.xml"
    if src_manifest.is_file() and not dst_manifest.is_file():
        _copy_isolated(src_manifest, dst_manifest)
    ff_manifest = app_dir / "firefox.VisualElementsManifest.xml"
    if ff_manifest.is_file() and not dst_manifest.is_file():
        _copy_isolated(ff_manifest, dst_manifest)
    pb_src = BRANDING_DIR / "private_browsing.VisualElementsManifest.xml"
    pb_dst = app_dir / "private_browsing.VisualElementsManifest.xml"
    if pb_src.is_file() and not pb_dst.is_file():
        _copy_isolated(pb_src, pb_dst)

    ve_dst = app_dir / "browser" / "VisualElements"
    ve_names = (
        "VisualElements_150.png",
        "VisualElements_70.png",
        "PrivateBrowsing_150.png",
        "PrivateBrowsing_70.png",
    )
    for png_name in ve_names:
        src_png = BRANDING_DIR / png_name
        if not src_png.is_file():
            src_png = BRANDING_DIR / "VisualElements" / png_name
        if src_png.is_file():
            ve_dst.mkdir(parents=True, exist_ok=True)
            target = ve_dst / png_name
            if not target.is_file():
                _copy_isolated(src_png, target)


def _is_unsafe_archive_member(name: str, is_symlink: bool = False) -> bool:
    if is_symlink:
        return True
    if not name or name.startswith(("/", "\\")):
        return True
    if re.match(r"^[A-Za-z]:", name):
        return True
    parts = Path(name.replace("\\", "/")).parts
    return ".." in parts


def _safe_extract_zip(archive: Path, destination: Path) -> Path:
    destination.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(archive, "r") as zf:
        for info in zf.infolist():
            mode = (info.external_attr >> 16) & 0o170000
            if _is_unsafe_archive_member(info.filename, is_symlink=(mode == 0o120000)):
                raise WinPackageError(f"ZIP archive contains unsafe entry: {info.filename}")
        for info in zf.infolist():
            rel = Path(info.filename.replace("\\", "/"))
            if not rel.parts:
                continue
            target = destination / rel
            if info.is_dir():
                target.mkdir(parents=True, exist_ok=True)
            else:
                target.parent.mkdir(parents=True, exist_ok=True)
                with zf.open(info, "r") as src, open(target, "wb") as dst:
                    shutil.copyfileobj(src, dst)
    return destination


def _safe_extract_tar(archive: Path, destination: Path) -> Path:
    destination.mkdir(parents=True, exist_ok=True)
    with tarfile.open(archive, "r:*") as tf:
        for member in tf.getmembers():
            if _is_unsafe_archive_member(member.name, is_symlink=(member.issym() or member.islnk())):
                raise WinPackageError(f"Tar archive contains unsafe entry: {member.name}")
        tf.extractall(destination)
    return destination


def _locate_runtime_root(extracted_dir: Path) -> Path:
    if (extracted_dir / WINDOWS_EXE_NAME).is_file():
        return extracted_dir
    for candidate in sorted(extracted_dir.iterdir()):
        if candidate.is_dir() and (candidate / WINDOWS_EXE_NAME).is_file():
            return candidate
    if (extracted_dir / "firefox.exe").is_file() or any(
        c.is_dir() and (c / "firefox.exe").is_file() for c in extracted_dir.iterdir()
    ):
        raise WinPackageError("Refusing artifact/unbranded Firefox archive (missing argon.exe)")
    raise WinPackageError(f"Archive {extracted_dir} does not contain {WINDOWS_EXE_NAME}")


def _validate_runtime_dir(runtime_dir: Path) -> tuple[configparser.ConfigParser, str, bool]:
    exe_path = runtime_dir / WINDOWS_EXE_NAME
    if not exe_path.is_file():
        if (runtime_dir / "firefox.exe").is_file():
            raise WinPackageError(
                f"Refusing artifact/unbranded Firefox build at {runtime_dir} (missing {WINDOWS_EXE_NAME})"
            )
        raise WinPackageError(f"Argon runtime {runtime_dir} is missing {WINDOWS_EXE_NAME}")
    pe_info = inspect_pe_file(exe_path)
    if not pe_info["valid_pe"]:
        raise WinPackageError(f"Invalid Windows PE binary: {exe_path}")
    app_ini = runtime_dir / "application.ini"
    ini = read_windows_application_ini(app_ini)
    if not (runtime_dir / "browser" / "omni.ja").is_file():
        raise WinPackageError(f"Runtime {runtime_dir} is missing browser/omni.ja")
    version = ini.get("App", "Version", fallback="0.1.0") or "0.1.0"
    has_dll = _detect_adblock_dll(runtime_dir)
    return ini, version, has_dll


def _resolve_windows_runtime(
    workspace,
    archive: Path | str | None,
    runtime: Path | str | None,
    staging_parent: Path,
) -> tuple[Path, str, str, bool]:
    """Return (runtime_dir, source_mode, app_version, adblock_dll_present)."""
    if archive is not None and runtime is not None:
        raise WinPackageError("Pass either --archive or --runtime, not both")

    if runtime is not None:
        runtime_path = _no_symlinks(runtime)
        if not runtime_path.is_dir():
            raise WinPackageError(f"Argon runtime directory not found: {runtime_path}")
        _, version, has_dll = _validate_runtime_dir(runtime_path)
        return runtime_path, "runtime", version, has_dll

    archive_path: Path | None = Path(archive).resolve() if archive is not None else None
    source_mode = "archive"

    if archive_path is None:
        if workspace is None:
            raise WinPackageError("Pass --archive or --runtime when no workspace is provided")
        dist = Path(workspace.obj) / "dist"
        zips = sorted(
            p for p in dist.glob(f"{APP_NAME}-*.win*.zip")
            if p.is_file() and p.name.endswith((".win32.zip", ".win64.zip", ".win64-aarch64.zip"))
        ) if dist.is_dir() else []
        if zips:
            archive_path = zips[0].resolve()
        else:
            dist_bin = dist / "bin"
            if dist_bin.is_dir():
                _, version, has_dll = _validate_runtime_dir(dist_bin)
                return dist_bin, "dist-bin", version, has_dll
            raise WinPackageError(
                "No Windows Argon build or archive found; run `argon.py build` or `argon.py package` "
                "first or pass --archive or --runtime"
            )

    if not archive_path.is_file():
        raise WinPackageError(f"Packaged Argon archive not found: {archive_path}")

    extract_dir = staging_parent / "extracted"
    if archive_path.suffix.lower() == ".zip":
        _safe_extract_zip(archive_path, extract_dir)
    else:
        _safe_extract_tar(archive_path, extract_dir)

    runtime_root = _locate_runtime_root(extract_dir)
    _, version, has_dll = _validate_runtime_dir(runtime_root)
    return runtime_root, source_mode, version, has_dll


def _find_csc() -> Path | None:
    if os.name != "nt":
        return None
    windir = Path(os.environ.get("WINDIR") or r"C:\Windows")
    candidates = [
        windir / "Microsoft.NET" / "Framework64" / "v4.0.30319" / "csc.exe",
        windir / "Microsoft.NET" / "Framework" / "v4.0.30319" / "csc.exe",
    ]
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    found = shutil.which("csc.exe") or shutil.which("csc")
    return Path(found) if found else None


def _find_native_c_toolchain() -> tuple[str, Path, Path | None] | None:
    """Return (kind, compiler_path, rc_path) for compiling argon-portable.c."""
    mozbuild = Path(os.environ.get("MOZBUILD_STATE_PATH") or Path.home() / ".mozbuild")

    clang_cl_candidates: list[Path] = []
    if (mozbuild / "clang" / "bin" / "clang-cl.exe").is_file():
        clang_cl_candidates.append(mozbuild / "clang" / "bin" / "clang-cl.exe")
    for exe in ("clang-cl.exe", "clang-cl"):
        hit = shutil.which(exe)
        if hit:
            clang_cl_candidates.append(Path(hit))
    for cc in clang_cl_candidates:
        rc = cc.parent / "llvm-rc.exe"
        if not rc.is_file():
            rc_hit = shutil.which("llvm-rc.exe") or shutil.which("rc.exe")
            rc = Path(rc_hit) if rc_hit else None
        return ("clang-cl", cc, rc)

    cl_hit = shutil.which("cl.exe") or shutil.which("cl")
    if cl_hit:
        cl_path = Path(cl_hit)
        rc_hit = shutil.which("rc.exe")
        return ("cl", cl_path, Path(rc_hit) if rc_hit else None)

    for gcc_name in ("x86_64-w64-mingw32-gcc", "x86_64-w64-mingw32-gcc.exe", "gcc.exe", "gcc"):
        hit = shutil.which(gcc_name)
        if hit:
            gcc_path = Path(hit)
            windres_name = "x86_64-w64-mingw32-windres" if "mingw32" in gcc_name else "windres"
            wr = shutil.which(windres_name) or shutil.which("windres.exe")
            return ("gcc", gcc_path, Path(wr) if wr else None)

    return None


def _rc_escape(path: Path) -> str:
    return str(path).replace("\\", "\\\\")


def _compile_native_c_launcher(
    output_exe: Path,
    icon_path: Path | None,
    version: str,
    build_dir: Path,
) -> dict:
    toolchain = _find_native_c_toolchain()
    if not toolchain:
        raise WinPackageError("No native C compiler (clang-cl, cl, or mingw gcc) found")
    kind, cc, rc = toolchain
    c_src = PORTABLE_DIR / "argon-portable.c"
    rc_src = PORTABLE_DIR / "argon-portable.rc"
    manifest = PORTABLE_DIR / "argon-portable.manifest"
    if not c_src.is_file():
        raise WinPackageError(f"Missing portable C source: {c_src}")

    res_obj: Path | None = None
    if rc_src.is_file() and icon_path and icon_path.is_file():
        if not rc:
            raise WinPackageError(
                f"Resource compiler (llvm-rc / rc.exe / windres) not found alongside {cc.name}"
            )
        work_rc = build_dir / "rc_work"
        work_rc.mkdir(parents=True, exist_ok=True)
        shutil.copy2(rc_src, work_rc / "argon-portable.rc")
        if manifest.is_file():
            shutil.copy2(manifest, work_rc / "argon-portable.manifest")
        shutil.copy2(icon_path, work_rc / "argon.ico")
        vi_csv = _format_vi_version(version).replace(".", ",")
        safe_ver = version.replace('"', "")
        (work_rc / "version_defs.h").write_text(
            f'#define ARGON_VERSION_STR "{safe_ver}"\n#define ARGON_FILEVERSION_CSV {vi_csv}\n',
            encoding="utf-8",
        )
        res_out = work_rc / ("argon-portable.res" if kind in ("clang-cl", "cl") else "argon-portable-res.o")
        if kind in ("clang-cl", "cl"):
            rc_cmd = [
                str(rc),
                "/nologo",
                "/DHAVE_VERSION_DEFS",
                "/fo",
                str(res_out),
                str(work_rc / "argon-portable.rc"),
            ]
        else:
            rc_cmd = [
                str(rc),
                "-DHAVE_VERSION_DEFS",
                "-i",
                str(work_rc / "argon-portable.rc"),
                "-o",
                str(res_out),
            ]
        rc_proc = subprocess.run(rc_cmd, cwd=work_rc, capture_output=True, text=True, check=False)
        if rc_proc.returncode != 0 or not res_out.is_file():
            detail = (rc_proc.stderr or rc_proc.stdout or "").strip()
            raise WinPackageError(f"Resource compiler failed ({rc.name}): {detail}")
        res_obj = res_out

    if kind in ("clang-cl", "cl"):
        cmd = [
            str(cc),
            "/nologo",
            "/O2",
            "/W3",
            f"/Fo{build_dir / 'argon-portable.obj'}",
            f"/Fe{output_exe}",
            str(c_src),
        ]
        if res_obj is not None:
            cmd.append(str(res_obj))
        cmd.extend(["/link", "/SUBSYSTEM:WINDOWS", "user32.lib", "shell32.lib"])
    else:
        cmd = [str(cc), "-O2", "-mwindows", str(c_src)]
        if res_obj is not None:
            cmd.append(str(res_obj))
        cmd.extend(["-o", str(output_exe), "-luser32", "-lshell32"])

    completed = subprocess.run(cmd, cwd=build_dir, capture_output=True, text=True, check=False)
    if completed.returncode != 0 or not output_exe.is_file():
        detail = (completed.stderr or completed.stdout or "").strip()
        raise WinPackageError(f"Native C launcher compilation failed ({cc.name}): {detail}")
    return {"launcher": str(output_exe), "backend": f"native-c ({kind})", "compiler": str(cc)}


def _compile_dotnet_launcher(
    output_exe: Path,
    icon_path: Path | None,
    manifest_path: Path | None,
) -> dict:
    csc = _find_csc()
    if not csc:
        raise WinPackageError("Windows csc.exe compiler not found for dotnet portable launcher")
    cs_src = PORTABLE_DIR / "ArgonPortable.cs"
    if not cs_src.is_file():
        raise WinPackageError(f"Missing portable C# source: {cs_src}")

    # Pass native backslashes: csc.exe parses `/` as a CLI option switch.
    cmd = [
        os.fspath(csc),
        "-nologo",
        "-target:winexe",
        "-platform:x64",
        "-optimize+",
    ]
    if manifest_path and manifest_path.is_file():
        cmd.append(f"-win32manifest:{os.fspath(manifest_path)}")
    if icon_path and icon_path.is_file():
        cmd.append(f"-win32icon:{os.fspath(icon_path)}")
    cmd.append(f"-out:{os.fspath(output_exe)}")
    cmd.append(os.fspath(cs_src))

    completed = subprocess.run(cmd, capture_output=True, text=True, check=False)
    if completed.returncode != 0 or not output_exe.is_file():
        detail = (completed.stderr or completed.stdout or "").strip()
        raise WinPackageError(f"csc.exe launcher compilation failed: {detail}")
    return {"launcher": str(output_exe), "backend": "dotnet-csc", "compiler": str(csc)}


def compile_portable_launcher(
    output_exe: Path | str,
    icon_path: Path | str | None = None,
    manifest_path: Path | str | None = None,
    backend: str = "auto",
    version: str = "0.1.3",
) -> dict:
    """Compile the native Windows portable launcher (`argon-portable.exe`)."""
    out_path = Path(output_exe).resolve()
    out_path.parent.mkdir(parents=True, exist_ok=True)
    ico = Path(icon_path) if icon_path is not None else (BRANDING_DIR / "firefox.ico")
    if not ico.is_file():
        ico = None
    manifest = Path(manifest_path) if manifest_path is not None else (PORTABLE_DIR / "argon-portable.manifest")
    if not manifest.is_file():
        manifest = None

    if backend not in ("auto", "native-c", "dotnet"):
        raise WinPackageError(f"Unsupported launcher backend: {backend}")

    with tempfile.TemporaryDirectory(prefix="argon-launcher-build-") as tmp:
        build_dir = Path(tmp)
        native_err: str | None = None
        if backend in ("auto", "native-c"):
            try:
                result = _compile_native_c_launcher(out_path, ico, version, build_dir)
                result["sha256"] = sha256_file(out_path)
                return result
            except WinPackageError as exc:
                if backend == "native-c":
                    raise
                native_err = str(exc)

        if backend in ("auto", "dotnet"):
            try:
                result = _compile_dotnet_launcher(out_path, ico, manifest)
                result["sha256"] = sha256_file(out_path)
                return result
            except WinPackageError as exc:
                if native_err:
                    raise WinPackageError(
                        f"Failed to compile portable launcher ({native_err}; {exc})"
                    ) from None
                raise

    raise WinPackageError("Failed to compile portable launcher")


def find_makensis() -> Path | None:
    """Locate makensis from ARGON_MAKENSIS, PATH, or standard MozillaBuild/NSIS paths."""
    override = os.environ.get("ARGON_MAKENSIS")
    if override:
        candidate = Path(override)
        if candidate.is_file():
            return candidate

    for exe in ("makensis.exe", "makensis"):
        hit = shutil.which(exe)
        if hit:
            return Path(hit)

    if os.name == "nt":
        patterns = [
            r"C:\mozilla-build\nsis*\makensis.exe",
            r"C:\Program Files (x86)\NSIS\makensis.exe",
            r"C:\Program Files\NSIS\makensis.exe",
        ]
        for pattern in patterns:
            for match in sorted(glob.glob(pattern), reverse=True):
                if Path(match).is_file():
                    return Path(match)
    return None


def _format_vi_version(version: str) -> str:
    """Convert Mozilla version strings (e.g. '162.0a1', '0.1.3') into a 4-part VIProductVersion."""
    nums = [int(n) for n in re.findall(r"\d+", version)[:4]]
    while len(nums) < 4:
        nums.append(0)
    clamped = [min(max(n, 0), 65535) for n in nums]
    return ".".join(str(n) for n in clamped)


def _nsis_escape(value: str) -> str:
    return value.replace("$", "$$").replace('"', '$\\"')


def _write_nsis_file_lists(payload_dir: Path, install_nsh: Path, uninstall_nsh: Path) -> int:
    """Generate deterministic per-file NSIS install & uninstall includes; return total bytes."""
    dirs: list[str] = []
    files: list[tuple[str, Path]] = []
    total_bytes = 0

    for root, dirnames, filenames in os.walk(payload_dir):
        dirnames.sort()
        filenames.sort()
        rel_dir = Path(root).relative_to(payload_dir)
        rel_dir_win = "" if rel_dir == Path(".") else str(rel_dir).replace("/", "\\")
        if rel_dir_win:
            dirs.append(rel_dir_win)
        for fname in filenames:
            fpath = Path(root) / fname
            total_bytes += fpath.stat().st_size
            rel_file = f"{rel_dir_win}\\{fname}" if rel_dir_win else fname
            files.append((rel_file, fpath))

    install_lines = []
    by_dir: dict[str, list[Path]] = {}
    for rel_file, fpath in files:
        parent = rel_file.rsplit("\\", 1)[0] if "\\" in rel_file else ""
        by_dir.setdefault(parent, []).append(fpath)

    for rel_dir in sorted(by_dir):
        out_dir = f"$INSTDIR\\{rel_dir}" if rel_dir else "$INSTDIR"
        install_lines.append(f'SetOutPath "{_nsis_escape(out_dir)}"')
        for fpath in by_dir[rel_dir]:
            # NSIS `File` has no /REBOOTOK flag; locked payload files are handled
            # by the pre-install lock probe in argon-installer.nsi instead.
            install_lines.append(f'File "{_nsis_escape(os.fspath(fpath))}"')
    install_lines.append('SetOutPath "$INSTDIR"')
    install_nsh.write_text("\n".join(install_lines) + "\n", encoding="utf-8")

    uninstall_lines = [
        f'Delete /REBOOTOK "$INSTDIR\\{_nsis_escape(rel_file)}"' for rel_file, _ in reversed(files)
    ]
    for rel_dir in sorted(dirs, key=lambda d: (-d.count("\\"), d), reverse=False):
        uninstall_lines.append(f'RMDir "$INSTDIR\\{_nsis_escape(rel_dir)}"')
    uninstall_nsh.write_text("\n".join(uninstall_lines) + "\n", encoding="utf-8")

    return total_bytes


def write_sha256_sums(paths: list[Path | str], output_file: Path | str | None = None, merge: bool = True) -> Path:
    """Write or update a standard SHA256SUMS file (`<digest>  <filename>`)."""
    resolved = [Path(p).resolve() for p in paths]
    if not resolved:
        raise WinPackageError("No files passed to write_sha256_sums")
    for item in resolved:
        if not item.is_file():
            raise WinPackageError(f"Cannot checksum missing file: {item}")

    sums_path = Path(output_file).resolve() if output_file is not None else (resolved[0].parent / "SHA256SUMS")
    sums_path.parent.mkdir(parents=True, exist_ok=True)

    entries: dict[str, str] = {}
    if merge and sums_path.is_file():
        for raw in sums_path.read_text(encoding="utf-8", errors="replace").splitlines():
            m = re.match(r"^([0-9a-fA-F]{64})\s+[* ]?(.+)$", raw.strip())
            if m:
                fname = m.group(2).strip()
                if (sums_path.parent / fname).is_file():
                    entries[fname] = m.group(1).lower()

    for item in resolved:
        entries[item.name] = sha256_file(item)

    lines = [f"{entries[name]}  {name}" for name in sorted(entries)]
    sums_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return sums_path


def verify_sha256_sums(
    checksums_path: Path | str,
    base_dir: Path | str | None = None,
    required_names: list[str] | None = None,
) -> dict[str, str]:
    """Verify SHA256SUMS entries on disk and ensure required artifact names are covered."""
    sums_file = Path(checksums_path).resolve()
    if not sums_file.is_file():
        raise WinPackageError(f"Missing SHA256SUMS file: {sums_file}")
    root = Path(base_dir).resolve() if base_dir is not None else sums_file.parent

    verified: dict[str, str] = {}
    for line_no, raw in enumerate(sums_file.read_text(encoding="utf-8", errors="replace").splitlines(), 1):
        stripped = raw.strip()
        if not stripped:
            continue
        m = re.match(r"^([0-9a-fA-F]{64})\s+[* ]?(.+)$", stripped)
        if not m:
            raise WinPackageError(f"Malformed SHA256SUMS line {line_no} in {sums_file}")
        expected_digest = m.group(1).lower()
        name = m.group(2).strip()
        if _is_unsafe_archive_member(name):
            raise WinPackageError(f"Unsafe filename in SHA256SUMS: {name}")
        target = root / name
        if not target.is_file():
            raise WinPackageError(f"SHA256SUMS entry missing on disk: {target}")
        actual_digest = sha256_file(target)
        if actual_digest != expected_digest:
            raise WinPackageError(
                f"SHA-256 mismatch for {name}: expected {expected_digest}, got {actual_digest}"
            )
        verified[name] = actual_digest

    if not verified:
        raise WinPackageError(f"Empty SHA256SUMS file: {sums_file}")

    if required_names:
        for req in required_names:
            if req not in verified:
                raise WinPackageError(f"SHA256SUMS does not cover verified artifact: {req}")

    return verified


def _read_u16(data: bytes, offset: int) -> int:
    return struct.unpack_from("<H", data, offset)[0]


def _read_u32(data: bytes, offset: int) -> int:
    return struct.unpack_from("<I", data, offset)[0]


def _align4(offset: int) -> int:
    return (offset + 3) & ~3


def _parse_version_strings(blob: bytes) -> dict[str, str]:
    """Parse UTF-16LE StringFileInfo entries from a Win32 VS_VERSIONINFO resource."""
    result: dict[str, str] = {}

    def walk(pos: int, end: int) -> None:
        if pos + 6 > end:
            return
        length = _read_u16(blob, pos)
        value_len = _read_u16(blob, pos + 2)
        val_type = _read_u16(blob, pos + 4)
        if length < 6 or pos + length > end:
            return
        node_end = pos + length
        key_start = pos + 6
        key_end = key_start
        while key_end + 1 < node_end:
            if blob[key_end] == 0 and blob[key_end + 1] == 0:
                break
            key_end += 2
        key = blob[key_start:key_end].decode("utf-16le", errors="replace")
        val_pos = _align4(min(key_end + 2, node_end))

        if val_type == 1 and value_len > 0 and key not in ("VS_VERSION_INFO", "StringFileInfo", "VarFileInfo"):
            byte_len = min(value_len * 2, max(0, node_end - val_pos))
            val = blob[val_pos:val_pos + byte_len].decode("utf-16le", errors="replace").rstrip("\x00")
            if key and not re.fullmatch(r"[0-9a-fA-F]{8}", key):
                result[key] = val

        child_pos = val_pos
        if key == "VS_VERSION_INFO" and value_len > 0:
            child_pos = _align4(min(val_pos + value_len, node_end))
        elif val_type == 0 and value_len > 0:
            child_pos = _align4(min(val_pos + value_len, node_end))
        elif val_type == 1 and value_len > 0:
            child_pos = _align4(min(val_pos + value_len * 2, node_end))

        while child_pos + 6 <= node_end:
            clen = _read_u16(blob, child_pos)
            if clen < 6 or child_pos + clen > node_end:
                break
            walk(child_pos, child_pos + clen)
            child_pos = _align4(child_pos + clen)

    walk(0, len(blob))
    return result


def inspect_pe_file(path: Path | str) -> dict:
    """Inspect a Windows PE file's headers, VersionInfo resources, and NSIS marker."""
    data = Path(path).read_bytes()
    if len(data) < 0x40 or data[:2] != b"MZ":
        return {"valid_pe": False, "machine": None, "version_info": {}, "nsis_installer": False}

    e_lfanew = _read_u32(data, 0x3C)
    if e_lfanew + 24 > len(data) or data[e_lfanew:e_lfanew + 4] != b"PE\x00\x00":
        return {"valid_pe": False, "machine": None, "version_info": {}, "nsis_installer": False}

    coff = e_lfanew + 4
    machine, num_sections, _, _, _, opt_size, _ = struct.unpack_from("<HHIIIHH", data, coff)
    opt_off = coff + 20
    sec_off = opt_off + opt_size
    if sec_off + num_sections * 40 > len(data):
        return {"valid_pe": False, "machine": machine, "version_info": {}, "nsis_installer": False}

    sections = []
    rsrc_sec = None
    max_raw_end = 0
    for idx in range(num_sections):
        entry = sec_off + idx * 40
        sec_name = data[entry:entry + 8].rstrip(b"\x00").decode("ascii", errors="replace")
        virt_size, virt_addr, raw_size, raw_ptr = struct.unpack_from("<IIII", data, entry + 8)
        sections.append((sec_name, virt_addr, virt_size, raw_ptr, raw_size))
        max_raw_end = max(max_raw_end, raw_ptr + raw_size)
        if sec_name == ".rsrc":
            rsrc_sec = (virt_addr, raw_ptr, raw_size)

    overlay = data[max_raw_end:] if max_raw_end < len(data) else b""
    nsis_installer = b"NullsoftInst" in overlay or b"Nullsoft.NSIS" in data[:min(len(data), 512 * 1024)]

    version_info: dict[str, str] = {}
    if rsrc_sec is not None:
        rva_base, rsrc_raw, rsrc_size = rsrc_sec

        def _rva_to_raw(rva: int) -> int | None:
            for _, vaddr, vsize, rptr, rsize in sections:
                span = max(vsize, rsize)
                if vaddr <= rva < vaddr + span:
                    raw = rptr + (rva - vaddr)
                    if raw < len(data):
                        return raw
            return None

        def _dir_entries(rel_offset: int) -> list[tuple[int, int]]:
            base = rsrc_raw + rel_offset
            if base + 16 > len(data):
                return []
            named, id_cnt = struct.unpack_from("<HH", data, base + 12)
            out = []
            cursor = base + 16
            for _ in range(named + id_cnt):
                if cursor + 8 > len(data):
                    break
                name_or_id, off = struct.unpack_from("<II", data, cursor)
                out.append((name_or_id, off))
                cursor += 8
            return out

        for type_id, type_off in _dir_entries(0):
            if type_id == 16 and (type_off & 0x80000000):  # RT_VERSION
                for _, lang_off in _dir_entries(type_off & 0x7FFFFFFF):
                    if lang_off & 0x80000000:
                        for _, leaf_off in _dir_entries(lang_off & 0x7FFFFFFF):
                            if not (leaf_off & 0x80000000):
                                leaf_pos = rsrc_raw + leaf_off
                                if leaf_pos + 16 <= len(data):
                                    data_rva, data_size = struct.unpack_from("<II", data, leaf_pos)
                                    raw_pos = _rva_to_raw(data_rva)
                                    if raw_pos is not None and raw_pos + data_size <= len(data):
                                        version_info.update(
                                            _parse_version_strings(data[raw_pos:raw_pos + data_size])
                                        )

    return {
        "valid_pe": True,
        "machine": f"0x{machine:04x}",
        "version_info": version_info,
        "nsis_installer": nsis_installer,
    }


def command_win_portable(
    workspace,
    archive: Path | str | None = None,
    output: Path | str | None = None,
    runtime: Path | str | None = None,
    launcher_exe: Path | str | None = None,
    launcher_backend: str = "auto",
    write_checksums: bool = True,
) -> dict:
    """Build a self-contained portable Windows ZIP package with isolated profile launch."""
    if output is None and workspace is None:
        raise WinPackageError("Pass --output when no workspace is provided")
    output_parent = (
        Path(output).resolve().parent
        if output is not None
        else (Path(workspace.obj) / "dist").resolve()
    )
    output_parent.mkdir(parents=True, exist_ok=True)

    staging = Path(tempfile.mkdtemp(dir=output_parent, prefix=".win-portable-"))
    try:
        runtime_dir, source_mode, app_version, has_dll = _resolve_windows_runtime(
            workspace, archive, runtime, staging
        )
        root_name = f"{APP_NAME}-{app_version}.en-US.{WINDOWS_ARCH}-portable"
        default_output = output_parent / f"{root_name}.zip"
        output_path = Path(output).resolve() if output is not None else default_output

        package_root = staging / "pkg" / root_name
        app_dst = package_root / "app"
        data_dst = package_root / "data"

        shutil.copytree(runtime_dir, app_dst, symlinks=False, copy_function=_link_or_copy)
        installed_settings = install_windows_runtime_settings(app_dst)
        _stage_visual_elements(app_dst)
        has_dll = has_dll or _detect_adblock_dll(app_dst)

        for sub in PORTABLE_DATA_SUBDIRS:
            (data_dst / sub).mkdir(parents=True, exist_ok=True)

        readme_src = PORTABLE_DIR / "README-PORTABLE.txt"
        if readme_src.is_file():
            _copy_isolated(readme_src, package_root / "README-PORTABLE.txt")
        else:
            (package_root / "README-PORTABLE.txt").write_text(
                "Launch argon-portable.exe for isolated portable browsing.\n",
                encoding="utf-8",
            )
        license_src = REPO / "LICENSE"
        if license_src.is_file():
            _copy_isolated(license_src, package_root / "LICENSE")

        launcher_dst = package_root / PORTABLE_LAUNCHER_NAME
        if launcher_exe is not None:
            prebuilt = _no_symlinks(launcher_exe)
            if not prebuilt.is_file():
                raise WinPackageError(f"Prebuilt portable launcher not found: {prebuilt}")
            pe = inspect_pe_file(prebuilt)
            if not pe["valid_pe"]:
                raise WinPackageError(f"Prebuilt portable launcher is not a valid PE executable: {prebuilt}")
            _copy_isolated(prebuilt, launcher_dst)
            backend_used = "prebuilt"
        else:
            compiled = compile_portable_launcher(
                launcher_dst,
                backend=launcher_backend,
                version=app_version,
            )
            backend_used = compiled["backend"]

        handle, temp_zip = tempfile.mkstemp(dir=output_path.parent, prefix=".portable-", suffix=".zip")
        os.close(handle)
        temp_zip_path = Path(temp_zip)
        try:
            with zipfile.ZipFile(temp_zip_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
                pkg_parent = package_root.parent
                for dirpath, dirnames, filenames in os.walk(package_root):
                    dirnames.sort()
                    filenames.sort()
                    cur_dir = Path(dirpath)
                    if not dirnames and not filenames:
                        rel_dir = cur_dir.relative_to(pkg_parent).as_posix().rstrip("/") + "/"
                        zf.writestr(rel_dir, b"")
                    for fname in filenames:
                        fpath = cur_dir / fname
                        rel_file = fpath.relative_to(pkg_parent).as_posix()
                        zf.write(fpath, rel_file)
            temp_zip_path.replace(output_path)
        finally:
            temp_zip_path.unlink(missing_ok=True)
    finally:
        shutil.rmtree(staging, ignore_errors=True)

    sums_path = write_sha256_sums([output_path]) if write_checksums else None
    return {
        "package_type": "portable-zip",
        "source_mode": source_mode,
        "version": app_version,
        "arch": WINDOWS_ARCH,
        "bundle": str(output_path),
        "bundle_sha256": sha256_file(output_path),
        "bundle_size": output_path.stat().st_size,
        "launcher": PORTABLE_LAUNCHER_NAME,
        "launcher_backend": backend_used,
        "installed_settings": installed_settings,
        "adblock_dll_present": has_dll,
        "checksums_file": str(sums_path) if sums_path else None,
        "exit_code": 0,
        "succeeded": True,
    }


def command_win_installer(
    workspace,
    archive: Path | str | None = None,
    output: Path | str | None = None,
    runtime: Path | str | None = None,
    makensis: Path | str | None = None,
    write_checksums: bool = True,
) -> dict:
    """Build an Argon Windows NSIS installer using packaging/windows/argon-installer.nsi."""
    verify_installer_script_contract()
    if output is None and workspace is None:
        raise WinPackageError("Pass --output when no workspace is provided")
    nsis_script = WINDOWS_DIR / NSIS_SCRIPT_NAME
    if not nsis_script.is_file():
        raise WinPackageError(f"Missing NSIS installer script: {nsis_script}")

    missing_branding = [
        name for name in REQUIRED_NSIS_BRANDING_FILES if not (BRANDING_DIR / name).is_file()
    ]
    if missing_branding:
        raise WinPackageError(
            "Missing Windows branding assets in browser/branding/argon: " + ", ".join(missing_branding)
        )

    makensis_bin = Path(makensis).resolve() if makensis is not None else find_makensis()
    if makensis_bin is None or not makensis_bin.is_file():
        raise WinPackageError(
            "makensis is not installed; install NSIS 3.x (or MozillaBuild NSIS) or pass --makensis"
        )

    output_parent = (
        Path(output).resolve().parent
        if output is not None
        else (Path(workspace.obj) / "dist").resolve()
    )
    output_parent.mkdir(parents=True, exist_ok=True)

    staging = Path(tempfile.mkdtemp(dir=output_parent, prefix=".win-installer-"))
    try:
        runtime_dir, source_mode, app_version, has_dll = _resolve_windows_runtime(
            workspace, archive, runtime, staging
        )
        default_output = output_parent / f"{APP_NAME}-{app_version}.en-US.{WINDOWS_ARCH}.installer.exe"
        output_path = Path(output).resolve() if output is not None else default_output

        payload_dir = staging / "payload"
        shutil.copytree(runtime_dir, payload_dir, symlinks=False, copy_function=_link_or_copy)
        installed_settings = install_windows_runtime_settings(payload_dir)
        _stage_visual_elements(payload_dir)
        has_dll = has_dll or _detect_adblock_dll(payload_dir)

        nsis_work = staging / "nsis"
        nsis_work.mkdir(parents=True, exist_ok=True)
        shutil.copy2(nsis_script, nsis_work / NSIS_SCRIPT_NAME)

        branding_work = nsis_work / "branding"
        branding_work.mkdir(parents=True, exist_ok=True)
        for name in REQUIRED_NSIS_BRANDING_FILES:
            shutil.copy2(BRANDING_DIR / name, branding_work / name)

        license_dst = nsis_work / "LICENSE.txt"
        shutil.copy2(REPO / "LICENSE", license_dst)

        install_nsh = nsis_work / "install_files.nsh"
        uninstall_nsh = nsis_work / "uninstall_files.nsh"
        total_bytes = _write_nsis_file_lists(payload_dir, install_nsh, uninstall_nsh)
        estimated_kb = max(1, (total_bytes + 1023) // 1024)

        cmd = [
            os.fspath(makensis_bin),
            "-V3",
            "-INPUTCHARSET",
            "UTF8",
            f"-DARGON_APP_VERSION={app_version}",
            f"-DARGON_VI_VERSION={_format_vi_version(app_version)}",
            f"-DARGON_ARCH={WINDOWS_ARCH}",
            f"-DARGON_OUT_FILE={os.fspath(output_path)}",
            f"-DARGON_OUT_BASENAME={output_path.name}",
            f"-DARGON_BRANDING_DIR={os.fspath(branding_work)}",
            f"-DARGON_LICENSE_FILE={os.fspath(license_dst)}",
            f"-DARGON_INSTALL_FILES_NSH={os.fspath(install_nsh)}",
            f"-DARGON_UNINSTALL_FILES_NSH={os.fspath(uninstall_nsh)}",
            f"-DARGON_ESTIMATED_SIZE_KB={estimated_kb}",
            os.fspath(nsis_work / NSIS_SCRIPT_NAME),
        ]
        completed = subprocess.run(cmd, cwd=nsis_work, capture_output=True, text=True, check=False)
        if completed.returncode != 0:
            detail = (completed.stderr or completed.stdout or "").strip()
            raise WinPackageError(f"makensis failed: {detail}")
        if not output_path.is_file() or output_path.stat().st_size == 0:
            raise WinPackageError(f"NSIS installer output missing or empty: {output_path}")
    finally:
        shutil.rmtree(staging, ignore_errors=True)

    sums_path = write_sha256_sums([output_path]) if write_checksums else None
    return {
        "package_type": "nsis-installer",
        "source_mode": source_mode,
        "version": app_version,
        "arch": WINDOWS_ARCH,
        "bundle": str(output_path),
        "bundle_sha256": sha256_file(output_path),
        "bundle_size": output_path.stat().st_size,
        "makensis": str(makensis_bin),
        "installed_settings": installed_settings,
        "adblock_dll_present": has_dll,
        "checksums_file": str(sums_path) if sums_path else None,
        "exit_code": 0,
        "succeeded": True,
    }


def _verify_portable_zip(
    bundle_path: Path,
    require_full_omni: bool = False,
    run_portable_smoke: bool = False,
) -> dict:
    if not bundle_path.is_file() or bundle_path.stat().st_size == 0:
        raise WinPackageError(f"Portable ZIP is missing or empty: {bundle_path}")

    omni_checked = False
    has_dll = False
    with tempfile.TemporaryDirectory(prefix="argon-win-verify-") as tmp:
        root = Path(tmp)
        try:
            _safe_extract_zip(bundle_path, root)
        except zipfile.BadZipFile as exc:
            raise WinPackageError(f"Corrupt portable ZIP {bundle_path}: {exc}") from None

        entries = [p for p in root.iterdir() if p.is_dir()]
        if len(entries) != 1:
            raise WinPackageError(
                f"Portable ZIP must contain a single top-level directory, found {len(entries)}"
            )
        pkg_dir = entries[0]
        launcher = pkg_dir / PORTABLE_LAUNCHER_NAME
        if not launcher.is_file() or launcher.stat().st_size == 0:
            raise WinPackageError(f"Portable ZIP missing {PORTABLE_LAUNCHER_NAME}")
        launcher_pe = inspect_pe_file(launcher)
        if not launcher_pe["valid_pe"]:
            raise WinPackageError(f"Portable launcher is not a valid PE executable: {launcher.name}")

        app_dir = pkg_dir / "app"
        if not app_dir.is_dir():
            raise WinPackageError("Portable ZIP missing app/ directory")
        if (app_dir / "firefox.exe").exists() and not (app_dir / WINDOWS_EXE_NAME).is_file():
            raise WinPackageError("Portable ZIP contains unbranded firefox.exe instead of argon.exe")

        for rel in (
            WINDOWS_EXE_NAME,
            "application.ini",
            "distribution/policies.json",
            "librewolf.cfg",
            "defaults/pref/local-settings.js",
        ):
            if not (app_dir / rel).is_file():
                raise WinPackageError(f"Portable ZIP missing app/{rel}")

        if not (pkg_dir / "README-PORTABLE.txt").is_file() or not (pkg_dir / "LICENSE").is_file():
            raise WinPackageError("Portable ZIP missing README-PORTABLE.txt or LICENSE")

        for sub in PORTABLE_DATA_SUBDIRS:
            if not (pkg_dir / "data" / sub).is_dir():
                raise WinPackageError(f"Portable ZIP missing isolated directory data/{sub}/")

        exe_pe = inspect_pe_file(app_dir / WINDOWS_EXE_NAME)
        if not exe_pe["valid_pe"]:
            raise WinPackageError("Portable app/argon.exe is not a valid PE executable")

        ini = read_windows_application_ini(app_dir / "application.ini")
        if ini.get("App", "Profile", fallback="") != "argon" or ini.get("App", "RemotingName", fallback="") != "argon":
            raise WinPackageError("Portable application.ini missing Profile=argon or RemotingName=argon")

        expected_policies = json.loads(canonical_policies_source().read_text(encoding="utf-8"))
        actual_policies = json.loads((app_dir / "distribution" / "policies.json").read_text(encoding="utf-8"))
        if actual_policies != expected_policies:
            raise WinPackageError("Portable policies.json diverges from canonical Argon policies")

        omni_path = app_dir / "browser" / "omni.ja"
        if require_full_omni or omni_path.is_file():
            if not omni_path.is_file():
                raise WinPackageError("Portable ZIP is missing app/browser/omni.ja")
            with zipfile.ZipFile(omni_path) as zf:
                names = set(zf.namelist())
            missing_entries = [entry for entry in REQUIRED_OMNI_ENTRIES if entry not in names]
            if missing_entries:
                raise WinPackageError(
                    "Portable browser/omni.ja missing Argon entries: " + ", ".join(missing_entries)
                )
            omni_checked = True

        has_dll = _detect_adblock_dll(app_dir)

        if run_portable_smoke:
            if os.name != "nt":
                raise WinPackageError("--run-portable-smoke can only run on Windows")
            appdata_argon = Path(os.environ.get("APPDATA") or "") / "argon"
            local_argon = Path(os.environ.get("LOCALAPPDATA") or "") / "argon"
            before_appdata = appdata_argon.stat().st_mtime_ns if appdata_argon.exists() else None
            before_local = local_argon.stat().st_mtime_ns if local_argon.exists() else None
            res = subprocess.run(
                [os.fspath(launcher), "--version"],
                cwd=pkg_dir,
                capture_output=True,
                text=True,
                timeout=30,
                check=False,
            )
            if res.returncode != 0:
                detail = (res.stderr or res.stdout or "").strip()
                raise WinPackageError(f"Portable launcher smoke execution failed: {detail}")
            after_appdata = appdata_argon.stat().st_mtime_ns if appdata_argon.exists() else None
            after_local = local_argon.stat().st_mtime_ns if local_argon.exists() else None
            if after_appdata != before_appdata or after_local != before_local:
                raise WinPackageError("Portable smoke execution touched host AppData\\argon!")

    return {
        "type": "portable-zip",
        "path": str(bundle_path),
        "sha256": sha256_file(bundle_path),
        "size": bundle_path.stat().st_size,
        "omni_checked": omni_checked,
        "adblock_dll_present": has_dll,
        "smoke_checked": bool(run_portable_smoke),
    }


def _verify_installer_exe(installer_path: Path) -> dict:
    if not installer_path.is_file() or installer_path.stat().st_size == 0:
        raise WinPackageError(f"Windows installer is missing or empty: {installer_path}")

    pe = inspect_pe_file(installer_path)
    if not pe["valid_pe"]:
        raise WinPackageError(f"Windows installer is not a valid PE executable: {installer_path}")

    vi = pe["version_info"]
    combined = " ".join(vi.values()).lower()
    if vi and ("firefox" in combined or "mozilla corporation" in combined):
        raise WinPackageError(f"Installer VersionInfo still carries Firefox/Mozilla branding: {vi}")
    if vi and "argon" not in combined:
        raise WinPackageError(f"Installer VersionInfo is missing Argon identity: {vi}")

    return {
        "type": "nsis-installer",
        "path": str(installer_path),
        "sha256": sha256_file(installer_path),
        "size": installer_path.stat().st_size,
        "nsis_installer": pe["nsis_installer"],
        "version_info": vi,
    }


def verify_installer_script_contract() -> None:
    """Validate packaging/windows/argon-installer.nsi invariants statically."""
    nsi_path = WINDOWS_DIR / NSIS_SCRIPT_NAME
    if not nsi_path.is_file():
        raise WinPackageError(f"Missing NSIS installer script: {nsi_path}")
    active_lines = [
        line for line in nsi_path.read_text(encoding="utf-8").splitlines()
        if not line.lstrip().startswith((";", "#"))
    ]
    active = "\n".join(active_lines)
    for forbidden in (
        "UserChoice",
        "default-browser-agent",
        "MozillaMaintenanceService",
        "qsurvey.mozilla.com",
        "installation_telemetry.json",
        "RMDir /r",
        "$APPDATA\\argon",
        "$LOCALAPPDATA\\argon",
    ):
        if forbidden.lower() in active.lower():
            raise WinPackageError(f"Installer script violates policy ({forbidden!r} found)")

    # NSIS splits include/File paths on "\" only (the Windows separator), so a
    # "/" after a define is treated as part of the final filename and makensis
    # reports "could not find" even though the file is staged correctly.
    if "${ARGON_BRANDING_DIR}/" in active:
        raise WinPackageError("NSIS branding paths must use backslashes")
    # A single-quoted string containing double quotes is parsed as extra macro
    # parameters ("WriteRegStr expects 4 parameters, got 5"); embedded quotes
    # must use NSIS' $\" escape inside a double-quoted string.
    if re.search(r"'\"[^\n]*\"'", active):
        raise WinPackageError("NSIS strings must escape embedded quotes with $\"")


def command_verify_windows(
    workspace,
    portable: Path | str | None = None,
    installer: Path | str | None = None,
    checksums: Path | str | None = None,
    require_full_omni: bool = False,
    run_portable_smoke: bool = False,
) -> dict:
    """Verify Windows portable ZIP, NSIS installer, and SHA256SUMS manifest."""
    verify_installer_script_contract()

    portable_path: Path | None = Path(portable).resolve() if portable is not None else None
    installer_path: Path | None = Path(installer).resolve() if installer is not None else None

    if portable_path is None and installer_path is None and workspace is not None:
        dist = Path(workspace.obj) / "dist"
        if dist.is_dir():
            p_hits = sorted(p for p in dist.glob("*-portable.zip") if p.is_file())
            if p_hits:
                portable_path = p_hits[0].resolve()
            i_hits = sorted(p for p in dist.glob("*.installer.exe") if p.is_file())
            if not i_hits and (dist / "install" / "sea").is_dir():
                i_hits = sorted(p for p in (dist / "install" / "sea").glob("*.installer.exe") if p.is_file())
            if i_hits:
                installer_path = i_hits[0].resolve()

    if portable_path is None and installer_path is None:
        raise WinPackageError(
            "No Windows portable ZIP or installer found; pass --portable/--installer or package first"
        )

    artifacts: list[dict] = []
    omni_checked = False
    has_dll = False
    portable_verified = False
    installer_verified = False

    if portable_path is not None:
        p_report = _verify_portable_zip(
            portable_path,
            require_full_omni=require_full_omni,
            run_portable_smoke=run_portable_smoke,
        )
        artifacts.append(p_report)
        portable_verified = True
        omni_checked = omni_checked or p_report["omni_checked"]
        has_dll = has_dll or p_report["adblock_dll_present"]

    if installer_path is not None:
        i_report = _verify_installer_exe(installer_path)
        artifacts.append(i_report)
        installer_verified = True

    checksums_verified = False
    sums_path: Path | None = Path(checksums).resolve() if checksums is not None else None
    if sums_path is None:
        first_dir = Path(artifacts[0]["path"]).parent
        candidate = first_dir / "SHA256SUMS"
        if candidate.is_file():
            sums_path = candidate

    if sums_path is not None:
        if checksums is not None:
            required_names = [Path(a["path"]).name for a in artifacts]
        else:
            required_names = [
                Path(a["path"]).name for a in artifacts if Path(a["path"]).parent == sums_path.parent
            ]
        verify_sha256_sums(sums_path, base_dir=sums_path.parent, required_names=required_names)
        checksums_verified = True

    return {
        "artifacts": artifacts,
        "portable_verified": portable_verified,
        "installer_verified": installer_verified,
        "checksums_verified": checksums_verified,
        "omni_checked": omni_checked,
        "adblock_dll_present": has_dll,
        "exit_code": 0,
        "succeeded": True,
    }


def _build_cli_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    for name in ("portable", "installer"):
        p = sub.add_parser(name)
        p.add_argument("--runtime", type=Path, default=None)
        p.add_argument("--archive", type=Path, default=None)
        p.add_argument("--output", type=Path, default=None)
        p.add_argument("--no-checksums", action="store_true")
        if name == "portable":
            p.add_argument("--launcher-exe", type=Path, default=None)
            p.add_argument("--launcher-backend", choices=("auto", "native-c", "dotnet"), default="auto")
        else:
            p.add_argument("--makensis", type=Path, default=None)

    pv = sub.add_parser("verify")
    pv.add_argument("--portable", type=Path, default=None)
    pv.add_argument("--installer", type=Path, default=None)
    pv.add_argument("--checksums", type=Path, default=None)
    pv.add_argument("--require-full-omni", action="store_true")
    pv.add_argument("--run-portable-smoke", action="store_true")

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = _build_cli_parser()
    args = parser.parse_args(argv)
    try:
        if args.command == "portable":
            report = command_win_portable(
                None,
                archive=args.archive,
                output=args.output,
                runtime=args.runtime,
                launcher_exe=args.launcher_exe,
                launcher_backend=args.launcher_backend,
                write_checksums=not args.no_checksums,
            )
        elif args.command == "installer":
            report = command_win_installer(
                None,
                archive=args.archive,
                output=args.output,
                runtime=args.runtime,
                makensis=args.makensis,
                write_checksums=not args.no_checksums,
            )
        else:
            report = command_verify_windows(
                None,
                portable=args.portable,
                installer=args.installer,
                checksums=args.checksums,
                require_full_omni=args.require_full_omni,
                run_portable_smoke=args.run_portable_smoke,
            )
    except WinPackageError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(report, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
