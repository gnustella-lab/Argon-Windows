#!/usr/bin/env python3
"""Build the pinned Rust adblock engine (Linux .so / Windows .dll) and portable WASM ABI.

The native C-ABI shared library is optional at runtime: Argon ships the vendored
uBlock Origin JavaScript engine as its built-in default so neither browser builds
nor runtime operation require Cargo or native libraries.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import platform
import shutil
import subprocess
import sys

_SCRIPTS_DIR = str(Path(__file__).resolve().parent)
if _SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, _SCRIPTS_DIR)

import platform_compat

ROOT = Path(__file__).resolve().parents[1]


def find_cargo():
    cargo_name = platform_compat.binary_name("cargo")
    found = shutil.which(cargo_name) or shutil.which("cargo")
    if found:
        return found
    cargo_home = Path(os.environ.get("CARGO_HOME") or Path.home() / ".cargo")
    candidate = cargo_home / "bin" / cargo_name
    if candidate.is_file():
        return str(candidate)
    return None


def find_rustc(cargo):
    rustc_name = platform_compat.binary_name("rustc")
    sibling = Path(cargo).with_name(rustc_name)
    if sibling.is_file():
        return str(sibling)
    return shutil.which(rustc_name) or shutil.which("rustc") or "rustc"


def build_native_adblock(*, native_only=False, wasm_only=False):
    system = platform.system()
    if system not in ("Linux", "Windows"):
        raise SystemExit(
            f"Native adblock packaging is supported on Linux and Windows only (got {system}); "
            "do not remove the uBlock JS fallback."
        )
    if native_only and wasm_only:
        raise SystemExit("Pass at most one of --native-only or --wasm-only")

    cargo = find_cargo()
    if not cargo:
        raise SystemExit(
            "Cargo/Rust toolchain not found. Native C-ABI adblock compilation is optional; "
            "Argon uses the built-in uBlock JS engine by default. Install Rust/Cargo (with "
            "wasm32-unknown-unknown target) only if rebuilding the native adblock library."
        )

    rustc = find_rustc(cargo)
    target = ROOT / ".cache" / "adblock-target"
    manifest = ROOT / "native" / "adblock" / "Cargo.toml"
    env = dict(os.environ, CARGO_TARGET_DIR=str(target))
    base = [cargo, "build", "--locked", "--release", "--manifest-path", str(manifest)]

    outputs = {}
    lib_name = platform_compat.shared_library_name("argon_adblock", system=system)
    if not wasm_only:
        subprocess.run(base, env=env, check=True)
        outputs[lib_name] = target / "release" / lib_name
    if not native_only:
        subprocess.run([*base, "--target", "wasm32-unknown-unknown"], env=env, check=True)
        outputs["engine.wasm"] = target / "wasm32-unknown-unknown" / "release" / "argon_adblock.wasm"

    destination = ROOT / "argon" / "adblock"
    destination.mkdir(parents=True, exist_ok=True)
    for name, source in outputs.items():
        if not source.is_file():
            raise SystemExit(f"Expected compiled artifact not found: {source}")
        shutil.copy2(source, destination / name)

    provenance = {
        "adblock": "0.13.5",
        "target": platform_compat.rust_host_target(rustc),
        "rustc": subprocess.check_output(
            [rustc, "--version"], text=True, encoding="utf-8"
        ).strip(),
        "cargoLockSha256": hashlib.sha256(
            (ROOT / "native" / "adblock" / "Cargo.lock").read_bytes()
        ).hexdigest(),
        "files": {
            name: hashlib.sha256((destination / name).read_bytes()).hexdigest()
            for name in outputs
        },
    }
    platform_compat.write_text_lf(
        destination / "build.json",
        json.dumps(provenance, indent=2) + "\n",
    )

    metadata = json.loads(
        subprocess.check_output(
            [
                cargo,
                "metadata",
                "--locked",
                "--format-version",
                "1",
                "--manifest-path",
                str(manifest),
            ],
            env=env,
            text=True,
            encoding="utf-8",
        )
    )
    notices = ["Third-party Rust dependencies; original source is available from crates.io.\n"]
    for package in sorted(metadata["packages"], key=lambda p: p["name"]):
        notices.append(
            f"\n=== {package['name']} {package['version']} ({package.get('license', '')}) ===\n"
            f"{package.get('repository') or ''}\n"
        )
        directory = Path(package["manifest_path"]).parent
        for file in sorted(directory.iterdir()):
            if file.is_file() and file.name.upper().startswith(("LICENSE", "COPYING", "NOTICE")):
                notices.append(
                    file.name + "\n" + file.read_text(encoding="utf-8", errors="replace") + "\n"
                )
    platform_compat.write_text_lf(destination / "THIRD-PARTY-NOTICES.txt", "\n".join(notices))
    return provenance


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--native-only", action="store_true", help="Build only the host shared library (.so/.dll)")
    parser.add_argument("--wasm-only", action="store_true", help="Build only engine.wasm")
    args = parser.parse_args(argv)
    provenance = build_native_adblock(native_only=args.native_only, wasm_only=args.wasm_only)
    print(json.dumps(provenance, indent=2))


if __name__ == "__main__":
    main()
