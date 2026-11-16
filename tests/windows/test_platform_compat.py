"""Tests for scripts/platform_compat.py and scripts/run-python.mjs."""

from __future__ import annotations

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

REPO = Path(__file__).resolve().parents[2]
PLATFORM_COMPAT_PATH = REPO / "scripts" / "platform_compat.py"
RUN_PYTHON_MJS = REPO / "scripts" / "run-python.mjs"


def load_platform_compat():
    spec = importlib.util.spec_from_file_location("platform_compat", PLATFORM_COMPAT_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class PlatformCompatTests(unittest.TestCase):
    def setUp(self):
        self.pc = load_platform_compat()
        self._tmp = tempfile.TemporaryDirectory(prefix="argon-compat-")
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)

    def test_binary_and_shared_library_names(self):
        self.assertEqual(self.pc.binary_name("argon", windows=True), "argon.exe")
        self.assertEqual(self.pc.binary_name("argon.exe", windows=True), "argon.exe")
        self.assertEqual(self.pc.binary_name("ARGON.EXE", windows=True), "ARGON.EXE")
        self.assertEqual(self.pc.binary_name("argon", windows=False), "argon")

        self.assertEqual(self.pc.shared_library_name("argon_adblock", system="Windows"), "argon_adblock.dll")
        self.assertEqual(self.pc.shared_library_name("argon_adblock", system="Linux"), "libargon_adblock.so")
        self.assertEqual(self.pc.shared_library_name("argon_adblock", system="Darwin"), "libargon_adblock.dylib")

    def test_windows_store_stub_detection(self):
        fake_apps = Path(r"C:\Users\example\AppData\Local\Microsoft\WindowsApps\python3.exe")
        self.assertTrue(self.pc.is_windows_store_stub(fake_apps, windows=True))
        self.assertFalse(self.pc.is_windows_store_stub(fake_apps, windows=False))

        zero_stub = self.root / "python3.exe"
        zero_stub.write_bytes(b"")
        self.assertTrue(self.pc.is_windows_store_stub(zero_stub, windows=True))
        self.assertFalse(self.pc.is_windows_store_stub(sys.executable))

    def test_find_python_ignores_zero_byte_store_stub_and_honors_argon_python(self):
        stub_dir = self.root / "Microsoft" / "WindowsApps"
        stub_dir.mkdir(parents=True)
        (stub_dir / "python3.exe").write_bytes(b"")
        (stub_dir / "python.exe").write_bytes(b"")

        with mock.patch.dict(os.environ, {"ARGON_PYTHON": sys.executable}):
            self.assertEqual(
                os.path.normcase(self.pc.find_python() or ""),
                os.path.normcase(str(Path(sys.executable).resolve())),
            )

        env_no_override = {
            k: v for k, v in os.environ.items() if k not in ("ARGON_PYTHON", "VIRTUAL_ENV", "CONDA_PREFIX")
        }
        env_no_override["PATH"] = str(stub_dir) + os.pathsep + env_no_override.get("PATH", "")
        with mock.patch.dict(os.environ, env_no_override, clear=True):
            discovered = self.pc.find_python(include_current=True)
            self.assertIsNotNone(discovered)
            assert discovered is not None
            self.assertFalse(self.pc.is_windows_store_stub(discovered))

    def test_run_python_mjs_handles_unicode_spaces_utf8_and_invalid_override(self):
        node = shutil.which("node")
        if not node:
            self.skipTest("node is unavailable")

        env = dict(os.environ, ARGON_PYTHON=sys.executable)
        sample = "Área de teste — αβγ [Argon] \"quoted\""
        res = subprocess.run(
            [
                node,
                str(RUN_PYTHON_MJS),
                "-c",
                "import json, os, sys; print(json.dumps({'arg': sys.argv[1], 'utf8': os.environ.get('PYTHONUTF8')}))",
                sample,
            ],
            env=env,
            capture_output=True,
            text=True,
            encoding="utf-8",
            check=False,
        )
        self.assertEqual(res.returncode, 0, res.stdout + res.stderr)
        payload = json.loads(res.stdout)
        self.assertEqual(payload["arg"], sample)
        self.assertEqual(payload["utf8"], "1")

        bad_env = dict(os.environ, ARGON_PYTHON=str(self.root / "nonexistent-python.exe"))
        bad_res = subprocess.run(
            [node, str(RUN_PYTHON_MJS), "-c", "print(1)"],
            env=bad_env,
            capture_output=True,
            text=True,
            encoding="utf-8",
            check=False,
        )
        self.assertNotEqual(bad_res.returncode, 0)
        self.assertIn("ARGON_PYTHON", bad_res.stderr)

    def test_memory_gib_returns_positive_value(self):
        mem = self.pc.memory_gib()
        self.assertIsInstance(mem, float)
        assert mem is not None
        self.assertGreater(mem, 0.25)

    def test_workspace_safety_and_junction_or_symlink_rejection(self):
        drive = Path.home().drive or "C:"
        if self.pc.is_windows():
            self.assertTrue(self.pc.is_safe_workspace_path(Path(f"{drive}\\argon-build"), REPO))
            self.assertFalse(self.pc.is_safe_workspace_path(Path(f"{drive}\\"), REPO))
            self.assertFalse(self.pc.is_safe_workspace_path(Path(f"{drive}\\has spaces"), REPO))
            self.assertFalse(self.pc.is_safe_workspace_path(Path(f"{drive}\\Área"), REPO))
        else:
            self.assertTrue(self.pc.is_safe_workspace_path(Path("/tmp/argon-build-test"), REPO))
            self.assertFalse(self.pc.is_safe_workspace_path(Path("/"), REPO))

        self.assertFalse(self.pc.is_safe_workspace_path(Path.home(), REPO))
        self.assertFalse(self.pc.is_safe_workspace_path(REPO, REPO))

        if self.pc.is_windows():
            long_root = self.root / "runneradmin_like_dir"
            long_root.mkdir()
            short_root = Path(r"C:\Users\RUNNER~1\AppData\Local\Temp\argon-short-test")
            expected_long = Path(r"C:\Users\runneradmin\AppData\Local\Temp\argon-short-test")
            with mock.patch.object(
                self.pc,
                "normalize_windows_long_path",
                side_effect=lambda p: (
                    expected_long / Path(p).name
                    if str(p).startswith(str(short_root)) and "~" not in Path(p).name
                    else (expected_long / Path(p).name if p == short_root else Path(p).absolute())
                ),
            ):
                self.assertEqual(self.pc.no_symlinks(short_root / "ws"), expected_long / "ws")
                self.assertTrue(self.pc.is_safe_workspace_path(short_root / "ws", REPO))
                self.assertFalse(self.pc.is_safe_workspace_path(self.root / "bad~dir", REPO))
                self.assertFalse(self.pc.is_safe_workspace_path(short_root / "bad~dir", REPO))

            import ctypes

            kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
            get_short = kernel32.GetShortPathNameW
            get_short.argtypes = [ctypes.c_wchar_p, ctypes.c_wchar_p, ctypes.c_uint32]
            get_short.restype = ctypes.c_uint32
            buf = ctypes.create_unicode_buffer(512)
            slen = get_short(str(long_root), buf, len(buf))
            if slen and "~" in buf.value:
                short_real = Path(buf.value) / "ws"
                self.assertEqual(self.pc.no_symlinks(short_real), long_root.resolve() / "ws")
                self.assertTrue(self.pc.is_safe_workspace_path(short_real, REPO))

        actual = self.root / "real"
        actual.mkdir()
        if self.pc.is_windows():
            junction = self.root / "junc"
            res = subprocess.run(
                ["cmd", "/c", "mklink", "/J", str(junction), str(actual)],
                capture_output=True,
                text=True,
                check=False,
            )
            if res.returncode == 0:
                self.assertTrue(self.pc.is_symlink_or_junction(junction))
                with self.assertRaises(ValueError):
                    self.pc.no_symlinks(junction / "child")

        link = self.root / "sym"
        try:
            link.symlink_to(actual, target_is_directory=True)
            with self.assertRaises(ValueError):
                self.pc.no_symlinks(link / "child")
        except OSError:
            with mock.patch.object(self.pc, "is_symlink_or_junction", side_effect=lambda p: p == link):
                with self.assertRaises(ValueError):
                    self.pc.no_symlinks(link / "child")

    def test_file_locking_is_mutually_exclusive_and_write_text_lf_preserves_lf(self):
        lock_file = self.root / "test.lock"
        with lock_file.open("a+b") as h1:
            self.pc.acquire_file_lock(h1)
            with lock_file.open("a+b") as h2:
                with self.assertRaises(OSError):
                    self.pc.acquire_file_lock(h2)
            self.pc.release_file_lock(h1)

        with lock_file.open("a+b") as h3:
            self.pc.acquire_file_lock(h3)
            self.pc.release_file_lock(h3)

        lf_path = self.root / "lf.txt"
        self.pc.write_text_lf(lf_path, "line1\nline2\n")
        self.assertEqual(lf_path.read_bytes(), b"line1\nline2\n")


if __name__ == "__main__":
    unittest.main(verbosity=2)
