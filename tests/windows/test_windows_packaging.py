"""Tests for Argon's Windows portable launcher, NSIS installer contract, and win_package.py."""

from __future__ import annotations

import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest import mock
import zipfile

REPO = Path(__file__).resolve().parents[2]
WIN_PACKAGE_SCRIPT = REPO / "scripts" / "win_package.py"
PORTABLE_DIR = REPO / "packaging" / "windows" / "portable"
INSTALLER_NSI = REPO / "packaging" / "windows" / "argon-installer.nsi"


def load_win_package():
    spec = importlib.util.spec_from_file_location("win_package", WIN_PACKAGE_SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def make_minimal_pe_bytes() -> bytes:
    """Construct a tiny structurally valid PE32+ executable header for offline tests."""
    buf = bytearray(512)
    buf[0:2] = b"MZ"
    buf[0x3C:0x40] = (0x80).to_bytes(4, "little")
    buf[0x80:0x84] = b"PE\x00\x00"
    buf[0x84:0x86] = (0x8664).to_bytes(2, "little")  # AMD64
    buf[0x86:0x88] = (1).to_bytes(2, "little")       # 1 section
    buf[0x94:0x96] = (0xF0).to_bytes(2, "little")    # SizeOfOptionalHeader
    opt_end = 0x84 + 20 + 0xF0
    buf[opt_end : opt_end + 5] = b".text"
    return bytes(buf)


def find_csc() -> str | None:
    if os.name != "nt":
        return None
    win_dir = Path(os.environ.get("WINDIR", r"C:\Windows"))
    for candidate in (
        win_dir / "Microsoft.NET" / "Framework64" / "v4.0.30319" / "csc.exe",
        win_dir / "Microsoft.NET" / "Framework" / "v4.0.30319" / "csc.exe",
    ):
        if candidate.is_file():
            return str(candidate)
    return shutil.which("csc.exe")


class FakeWorkspace:
    def __init__(self, root: Path):
        self.repo = REPO
        self.path = root / "ws"
        self.obj = self.path / "obj"
        self.obj.mkdir(parents=True, exist_ok=True)


class WindowsPackagingTests(unittest.TestCase):
    def setUp(self):
        self.wp = load_win_package()
        self._tmp = tempfile.TemporaryDirectory(prefix="argon-winpkg-")
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name).resolve()

    def _make_runtime(self, *, with_full_omni: bool = True, with_dll: bool = False, pe_bytes: bytes | None = None) -> Path:
        rt = self.root / "runtime"
        (rt / "browser").mkdir(parents=True, exist_ok=True)
        (rt / "argon.exe").write_bytes(pe_bytes or make_minimal_pe_bytes())
        (rt / "application.ini").write_text(
            "[App]\nVendor=Argon\nName=Argon\nRemotingName=argon\nProfile=argon\nVersion=0.1.3\n",
            encoding="utf-8",
        )
        entries = list(self.wp.REQUIRED_OMNI_ENTRIES)
        if not with_full_omni:
            entries = entries[:-1]
        with zipfile.ZipFile(rt / "browser" / "omni.ja", "w") as zf:
            for item in entries:
                zf.writestr(item, f"/* {item} */\n")
        if with_dll:
            (rt / "argon_adblock.dll").write_bytes(make_minimal_pe_bytes())
        return rt

    def test_portable_sources_enforce_strict_profile_isolation_and_valid_rc(self):
        c_src = (PORTABLE_DIR / "argon-portable.c").read_text(encoding="utf-8")
        cs_src = (PORTABLE_DIR / "ArgonPortable.cs").read_text(encoding="utf-8")
        rc_src = (PORTABLE_DIR / "argon-portable.rc").read_text(encoding="utf-8")
        manifest_src = (PORTABLE_DIR / "argon-portable.manifest").read_text(encoding="utf-8")
        readme_src = (PORTABLE_DIR / "README-PORTABLE.txt").read_text(encoding="utf-8")

        self.assertIn("#include <winresrc.h>", rc_src)
        self.assertNotIn("<winres.h>", rc_src, "winres.h requires MFC/ATL and fails under standard Windows SDK rc")
        self.assertIn("asInvoker", manifest_src)
        self.assertIn("PerMonitorV2", manifest_src)
        self.assertIn("argon-portable.exe", readme_src)

        for label, src in (("C", c_src), ("C#", cs_src)):
            for token in (
                "MOZ_APP_DATA",
                "MOZ_LOCAL_APP_DATA",
                "MOZ_APP_REMOTINGNAME",
                "MOZ_LEGACY_PROFILES",
                "--profile",
                "argon-portable-",
                "profilemanager",
                "migration",
                "reset-profile",
            ):
                self.assertIn(token, src, f"{label} portable launcher missing {token}")

    def test_installer_nsi_contract_preserves_profiles_and_never_hijacks_defaults(self):
        self.wp.verify_installer_script_contract()
        text = INSTALLER_NSI.read_text(encoding="utf-8")
        self.assertIn("SetRegView 64", text)
        self.assertIn("RegisteredApplications", text)
        self.assertIn(r"Software\Clients\StartMenuInternet\Argon\Capabilities", text)
        self.assertIn('FileOpen $0 "$INSTDIR\\${FileMainEXE}" a', text, "Upgrade must check running argon.exe lock")
        self.assertIn('WriteUninstaller "$INSTDIR\\uninstall\\helper.exe"', text)
        self.assertNotIn("HideIconsCommand", text)
        self.assertNotIn("ShowIconsCommand", text)

    def test_portable_zip_builds_verifies_stages_settings_and_visual_elements(self):
        runtime = self._make_runtime(with_full_omni=True, with_dll=False)
        ws = FakeWorkspace(self.root)
        launcher_stub = self.root / "stub-launcher.exe"
        launcher_stub.write_bytes(make_minimal_pe_bytes())

        report = self.wp.command_win_portable(
            ws,
            runtime=runtime,
            launcher_exe=launcher_stub,
        )
        self.assertTrue(report["succeeded"])
        self.assertEqual(report["package_type"], "portable-zip")
        self.assertFalse(report["adblock_dll_present"])
        bundle = Path(report["bundle"])
        self.assertTrue(bundle.is_file())
        self.assertTrue(Path(report["checksums_file"]).is_file())

        with zipfile.ZipFile(bundle) as zf:
            names = set(zf.namelist())
        prefix = "argon-0.1.3.en-US.win64-portable"
        for expected in (
            f"{prefix}/argon-portable.exe",
            f"{prefix}/README-PORTABLE.txt",
            f"{prefix}/LICENSE",
            f"{prefix}/app/argon.exe",
            f"{prefix}/app/application.ini",
            f"{prefix}/app/librewolf.cfg",
            f"{prefix}/app/defaults/pref/local-settings.js",
            f"{prefix}/app/distribution/policies.json",
            f"{prefix}/app/argon.VisualElementsManifest.xml",
            f"{prefix}/app/private_browsing.VisualElementsManifest.xml",
            f"{prefix}/app/browser/VisualElements/VisualElements_150.png",
            f"{prefix}/app/browser/VisualElements/VisualElements_70.png",
            f"{prefix}/app/browser/VisualElements/PrivateBrowsing_150.png",
            f"{prefix}/app/browser/VisualElements/PrivateBrowsing_70.png",
            f"{prefix}/data/profile/",
            f"{prefix}/data/app-data/",
            f"{prefix}/data/local-app-data/",
            f"{prefix}/data/home/",
        ):
            self.assertIn(expected, names, f"missing portable ZIP member {expected}")

        verify = self.wp.command_verify_windows(
            ws,
            portable=bundle,
            checksums=Path(report["checksums_file"]),
            require_full_omni=True,
        )
        self.assertTrue(verify["succeeded"])
        self.assertTrue(verify["portable_verified"])
        self.assertTrue(verify["checksums_verified"])
        self.assertTrue(verify["omni_checked"])
        self.assertFalse(verify["adblock_dll_present"])

    def test_portable_zip_reports_optional_adblock_dll_when_present(self):
        runtime = self._make_runtime(with_full_omni=True, with_dll=True)
        ws = FakeWorkspace(self.root)
        launcher_stub = self.root / "stub-launcher.exe"
        launcher_stub.write_bytes(make_minimal_pe_bytes())

        report = self.wp.command_win_portable(ws, runtime=runtime, launcher_exe=launcher_stub)
        self.assertTrue(report["adblock_dll_present"])
        verify = self.wp.command_verify_windows(ws, portable=report["bundle"], require_full_omni=True)
        self.assertTrue(verify["adblock_dll_present"])

    def test_refuses_artifact_firefox_runtime_and_incomplete_omni(self):
        artifact_rt = self.root / "artifact"
        (artifact_rt / "browser").mkdir(parents=True)
        (artifact_rt / "firefox.exe").write_bytes(make_minimal_pe_bytes())
        (artifact_rt / "application.ini").write_text("[App]\nName=Firefox\nVendor=Mozilla\n", encoding="utf-8")
        (artifact_rt / "browser" / "omni.ja").write_bytes(b"placeholder")
        ws = FakeWorkspace(self.root)
        launcher_stub = self.root / "stub-launcher.exe"
        launcher_stub.write_bytes(make_minimal_pe_bytes())

        with self.assertRaisesRegex(self.wp.WinPackageError, "firefox.exe|artifact"):
            self.wp.command_win_portable(ws, runtime=artifact_rt, launcher_exe=launcher_stub)

        partial_rt = self._make_runtime(with_full_omni=False)
        partial_zip = self.root / "partial.zip"
        self.wp.command_win_portable(ws, runtime=partial_rt, output=partial_zip, launcher_exe=launcher_stub, write_checksums=False)
        with self.assertRaisesRegex(self.wp.WinPackageError, "missing Argon entries"):
            self.wp.command_verify_windows(ws, portable=partial_zip, require_full_omni=True)

    def test_verify_rejects_checksum_tampering_and_unsafe_zip_paths(self):
        runtime = self._make_runtime(with_full_omni=True)
        ws = FakeWorkspace(self.root)
        launcher_stub = self.root / "stub-launcher.exe"
        launcher_stub.write_bytes(make_minimal_pe_bytes())
        report = self.wp.command_win_portable(ws, runtime=runtime, launcher_exe=launcher_stub)
        bundle = Path(report["bundle"])
        sums = Path(report["checksums_file"])
        sums.write_text(("0" * 64) + f"  {bundle.name}\n", encoding="utf-8")
        with self.assertRaisesRegex(self.wp.WinPackageError, "SHA-256 mismatch"):
            self.wp.command_verify_windows(ws, portable=bundle, checksums=sums)

        evil_zip = self.root / "evil.zip"
        with zipfile.ZipFile(evil_zip, "w") as zf:
            zf.writestr("../escape.txt", "nope")
        with self.assertRaisesRegex(self.wp.WinPackageError, "unsafe entry"):
            self.wp._safe_extract_zip(evil_zip, self.root / "out")

    def test_installer_command_invokes_makensis_and_verifies_output(self):
        runtime = self._make_runtime(with_full_omni=True, with_dll=True)
        ws = FakeWorkspace(self.root)
        fake_makensis = self.root / "makensis.exe"
        fake_makensis.write_bytes(make_minimal_pe_bytes())
        recorded_cmds = []

        def fake_run(cmd, **_kwargs):
            recorded_cmds.append(list(cmd))
            out_arg = next(arg for arg in cmd if arg.startswith("-DARGON_OUT_FILE="))
            out_path = Path(out_arg.split("=", 1)[1])
            install_nsh = Path(next(arg for arg in cmd if arg.startswith("-DARGON_INSTALL_FILES_NSH=")).split("=", 1)[1])
            nsh_text = install_nsh.read_text(encoding="utf-8")
            self.assertIn("librewolf.cfg", nsh_text)
            self.assertIn("policies.json", nsh_text)
            self.assertIn("argon_adblock.dll", nsh_text)
            out_path.write_bytes(make_minimal_pe_bytes() + b"NullsoftInst")
            return mock.Mock(returncode=0, stdout="OK", stderr="")

        with mock.patch("subprocess.run", side_effect=fake_run):
            report = self.wp.command_win_installer(ws, runtime=runtime, makensis=fake_makensis)

        self.assertTrue(report["succeeded"])
        self.assertEqual(report["package_type"], "nsis-installer")
        self.assertTrue(report["adblock_dll_present"])
        self.assertTrue(Path(report["bundle"]).is_file())
        self.assertTrue(any(arg == "-DARGON_ARCH=win64" for arg in recorded_cmds[0]))

    def test_portable_launcher_compiles_and_isolates_profile_on_windows(self):
        csc = find_csc()
        if not csc:
            self.skipTest("Windows .NET csc.exe is unavailable on this host")

        pkg_root = self.root / "Área portátil [Argon]"
        app_dir = pkg_root / "app"
        app_dir.mkdir(parents=True)

        # Compile a tiny real Windows probe executable as app\argon.exe to record argv + env.
        probe_cs = self.root / "ProbeArgon.cs"
        probe_cs.write_text(
            """
using System;
using System.IO;
using System.Text;

class ProbeArgon {
    static int Main(string[] args) {
        string log = Environment.GetEnvironmentVariable("ARGON_PROBE_LOG");
        if (!string.IsNullOrEmpty(log)) {
            var sb = new StringBuilder();
            sb.AppendLine("ARGC=" + args.Length);
            for (int i = 0; i < args.Length; i++) sb.AppendLine("ARG[" + i + "]=" + args[i]);
            foreach (var k in new[] {
                "MOZ_APP_DATA", "MOZ_LOCAL_APP_DATA", "MOZ_APP_REMOTINGNAME",
                "MOZ_LEGACY_PROFILES", "HOME", "ARGON_PORTABLE", "ARGON_PORTABLE_PROFILE"
            }) {
                sb.AppendLine("ENV[" + k + "]=" + (Environment.GetEnvironmentVariable(k) ?? ""));
            }
            File.WriteAllText(log, sb.ToString(), Encoding.UTF8);
        }
        return 0;
    }
}
""".strip(),
            encoding="utf-8",
        )
        probe_exe = app_dir / "argon.exe"
        comp_probe = subprocess.run(
            [csc, "/nologo", "/optimize+", f"/out:{probe_exe}", str(probe_cs)],
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(comp_probe.returncode, 0, comp_probe.stdout + comp_probe.stderr)

        launcher = pkg_root / "argon-portable.exe"
        compiled = self.wp.compile_portable_launcher(launcher, backend="dotnet", version="0.1.3")
        self.assertEqual(compiled["backend"], "dotnet-csc")
        self.assertTrue(launcher.is_file())

        log_file = self.root / "probe-normal.txt"
        env = dict(os.environ, ARGON_PROBE_LOG=str(log_file), ARGON_PORTABLE_NO_GUI="1")
        res = subprocess.run(
            [
                str(launcher),
                "--profile",
                r"C:\outside\Mozilla\Firefox\Profiles\stolen",
                "-P",
                "default-release",
                "--createprofile=evil",
                "--ProfileManager",
                "--migration",
                "--reset-profile",
                "https://example.test/path with spaces?q=1&x=\"quoted\"",
            ],
            cwd=pkg_root,
            env=env,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(res.returncode, 0, res.stdout + res.stderr)
        recorded = log_file.read_text(encoding="utf-8")
        expected_profile = str(pkg_root / "data" / "profile")
        self.assertIn(f"ARG[0]=--profile", recorded)
        self.assertIn(f"ARG[1]={expected_profile}", recorded)
        self.assertIn('ARG[2]=https://example.test/path with spaces?q=1&x="quoted"', recorded)
        self.assertIn("ARGC=3", recorded)
        self.assertNotIn("stolen", recorded)
        self.assertNotIn("default-release", recorded)
        self.assertNotIn("--migration", recorded)
        self.assertNotIn("--reset-profile", recorded)
        self.assertIn(f"ENV[MOZ_APP_DATA]={pkg_root / 'data' / 'app-data'}", recorded)
        self.assertIn(f"ENV[MOZ_LOCAL_APP_DATA]={pkg_root / 'data' / 'local-app-data'}", recorded)
        self.assertIn(f"ENV[HOME]={pkg_root / 'data' / 'home'}", recorded)
        self.assertIn("ENV[MOZ_APP_REMOTINGNAME]=argon-portable-", recorded)
        self.assertIn("ENV[MOZ_LEGACY_PROFILES]=1", recorded)

        # Info-only flags (--version) must not inject --profile.
        info_log = self.root / "probe-version.txt"
        res_ver = subprocess.run(
            [str(launcher), "--version"],
            cwd=pkg_root,
            env=dict(os.environ, ARGON_PROBE_LOG=str(info_log), ARGON_PORTABLE_NO_GUI="1"),
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(res_ver.returncode, 0, res_ver.stdout + res_ver.stderr)
        ver_recorded = info_log.read_text(encoding="utf-8")
        self.assertIn("ARGC=1", ver_recorded)
        self.assertIn("ARG[0]=--version", ver_recorded)

        # Fail closed when data\ cannot be created as a directory (blocked by a regular file).
        blocked_root = self.root / "blocked-portable"
        (blocked_root / "app").mkdir(parents=True)
        shutil.copy2(probe_exe, blocked_root / "app" / "argon.exe")
        shutil.copy2(launcher, blocked_root / "argon-portable.exe")
        (blocked_root / "data").write_text("not-a-directory", encoding="utf-8")
        fail_res = subprocess.run(
            [str(blocked_root / "argon-portable.exe")],
            cwd=blocked_root,
            env=dict(os.environ, ARGON_PORTABLE_NO_GUI="1"),
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertNotEqual(fail_res.returncode, 0)
        self.assertIn("not writable", (fail_res.stderr + fail_res.stdout).lower())


if __name__ == "__main__":
    unittest.main(verbosity=2)
