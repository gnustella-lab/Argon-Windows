"""Windows runtime, patch classification, native adblock, and runner contracts."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import re
import unittest
from unittest import mock
from xml.etree import ElementTree

REPO = Path(__file__).resolve().parents[2]


def load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class WindowsRuntimeContractTests(unittest.TestCase):
    def test_patch_classification_isolates_linux_only_patches_without_dropping_privacy(self):
        lw = load_module("librewolf_patches", REPO / "scripts" / "librewolf-patches.py")
        lines = (REPO / "assets" / "patches.txt").read_text(encoding="utf-8").splitlines()

        with mock.patch.object(lw.platform_compat, "is_windows", return_value=True), \
             mock.patch.object(lw.platform_compat, "is_macos", return_value=False):
            win_patches = [p for p in (lw.parse_patch_entry(line) for line in lines) if p]

        with mock.patch.object(lw.platform_compat, "is_windows", return_value=False), \
             mock.patch.object(lw.platform_compat, "is_macos", return_value=False):
            linux_patches = [p for p in (lw.parse_patch_entry(line) for line in lines) if p]

        self.assertIn("patches/dbus-name.patch", linux_patches)
        self.assertIn("patches/xdg-dir.patch", linux_patches)
        self.assertNotIn("patches/dbus-name.patch", win_patches)
        self.assertNotIn("patches/xdg-dir.patch", win_patches)

        # Core privacy, telemetry removal, profile isolation, and Windows UA patches stay enabled.
        for required in (
            "patches/autoconfig-setEnv.patch",
            "patches/disable-data-reporting-at-compile-time.patch",
            "patches/firefox-in-UA.patch",
            "patches/lw-dir.patch",
            "patches/mozilla-dirs.patch",
            "patches/profile-directory.patch",
            "patches/remove-pingsender.patch",
        ):
            self.assertIn(required, win_patches, f"Windows must retain {required}")
            self.assertTrue((REPO / required).is_file(), f"missing patch file {required}")

    def test_glean_upload_disable_is_unconditional_and_profile_patch_handles_winnt(self):
        glean_patch = (REPO / "patches" / "disable-data-reporting-at-compile-time.patch").read_text(encoding="utf-8")
        self.assertIn('+gkrust_features += ["glean_disable_upload"]', glean_patch)
        self.assertNotIn('+if CONFIG["MOZILLA_OFFICIAL"]:', glean_patch)

        profile_patch = (REPO / "patches" / "profile-directory.patch").read_text(encoding="utf-8")
        self.assertIn('Services.appinfo?.OS', profile_patch)
        self.assertIn('if (os === "WINNT")', profile_patch)
        self.assertIn('"UAppData"', profile_patch)
        self.assertIn('"AppData"', profile_patch)
        self.assertIn("Services.io.newFileURI", profile_patch)

    def test_settings_cfg_guards_setenv_and_includes_windows_hardening(self):
        cfg = (REPO / "settings" / "librewolf.cfg").read_text(encoding="utf-8")
        self.assertIn('if (typeof setEnv === "function")', cfg)
        self.assertIn("[CATEGORY] WINDOWS", cfg)
        self.assertIn('lockPref("default-browser-agent.enabled", false);', cfg)
        self.assertIn('lockPref("app.update.service.enabled", false);', cfg)
        self.assertIn('defaultPref("browser.shell.checkDefaultBrowser", false);', cfg)
        self.assertIn('defaultPref("toolkit.winRegisterApplicationRestart", false);', cfg)

    def test_windows_exe_manifest_and_mozconfig_windows_contracts(self):
        manifest_path = REPO / "browser" / "app" / "argon.exe.manifest"
        self.assertTrue(manifest_path.is_file(), "missing browser/app/argon.exe.manifest")
        manifest_text = manifest_path.read_text(encoding="utf-8")
        root = ElementTree.fromstring(manifest_text)
        self.assertEqual(root.find("{urn:schemas-microsoft-com:asm.v1}description").text, "Argon")
        self.assertIn('name="Argon"', manifest_text)
        self.assertIn('name="mozglue"', manifest_text)
        self.assertIn('level="asInvoker"', manifest_text)
        self.assertIn("{8e0f7a12-bfb3-4fe8-b9a5-48fd50a15a9a}", manifest_text)

        overlay = json.loads((REPO / "config" / "overlay.json").read_text(encoding="utf-8"))
        self.assertIn(
            {"from": "browser/app/argon.exe.manifest", "to": "browser/app/argon.exe.manifest"},
            overlay["files"],
        )

        moz_win = (REPO / "config" / "mozconfig.windows").read_text(encoding="utf-8")
        active = "\n".join(l for l in moz_win.splitlines() if not l.lstrip().startswith("#"))
        for required in (
            "--enable-application=browser",
            "--enable-bootstrap",
            "--with-app-name=argon",
            "--with-app-basename=Argon",
            "--with-branding=browser/branding/argon",
            "--with-distribution-id=io.github.argon_browser.Argon",
            "MOZ_APP_REMOTINGNAME=argon",
            "--disable-updater",
            "--disable-crashreporter",
            "--disable-default-browser-agent",
            "--disable-notification-server",
            "--disable-push-notification-helper",
            "--disable-backgroundtasks",
        ):
            self.assertIn(required, active)
        self.assertNotIn("--with-redist", active, "bare --with-redist breaks builds without Redist/MSVC")
        for forbidden in ("MOZ_REQUIRE_SIGNING", "--with-unsigned-addon-scopes", "--allow-addon-sideload", "MOZILLA_OFFICIAL"):
            self.assertNotIn(forbidden, active)

        argon_mod = load_module("argon_build_check", REPO / "scripts" / "argon.py")
        import shlex
        import tempfile

        spaceful_redist = Path(r"C:\Program Files\Microsoft Visual Studio\18\Enterprise\VC\Redist\MSVC\14.51.36231\x64\Microsoft.VC145.CRT")
        with tempfile.TemporaryDirectory(prefix="argon-mozcfg-") as tmp:
            ws_dir = Path(tmp).resolve() / "ws"
            ws = argon_mod.Workspace(REPO, ws_dir)
            lock = argon_mod.load_json(REPO / "config" / "firefox.lock.json", "lock")
            with mock.patch.object(argon_mod.platform_compat, "is_windows", return_value=True), \
                 mock.patch.object(argon_mod.platform_compat, "find_windows_redist_dir", return_value=spaceful_redist):
                generated = argon_mod.mozconfig_lines(ws, lock, artifact=False, mozconfig_path=REPO / "config" / "mozconfig.windows")
            redist_lines = [
                ln for ln in generated
                if ln.startswith("ac_add_options") and "--with-redist" in ln
            ]
            self.assertEqual(len(redist_lines), 1)
            tokens = shlex.split(redist_lines[0])
            self.assertEqual(tokens, ["ac_add_options", f"--with-redist={spaceful_redist.as_posix()}"])

    def test_native_adblock_supports_optional_windows_dll_without_requiring_cargo(self):
        native_js = (REPO / "argon" / "adblock" / "native.sys.mjs").read_text(encoding="utf-8")
        self.assertIn("WINNT:", native_js)
        self.assertIn("argon_adblock.dll", native_js)
        self.assertIn("deleteWhileOpen: false", native_js)
        self.assertIn("GreBinD", native_js)
        self.assertIn("keep uBlock fallback", native_js)

        builder = load_module("build_native_adblock", REPO / "scripts" / "build-native-adblock.py")
        with mock.patch.object(builder, "find_cargo", return_value=None):
            with self.assertRaises(SystemExit) as ctx:
                builder.build_native_adblock()
            self.assertIn("optional", str(ctx.exception).lower())
            self.assertIn("ublock", str(ctx.exception).lower())

    def test_windows_titlebar_ui_and_cross_platform_browser_runner_contracts(self):
        controller = (REPO / "browser" / "base" / "content" / "argon-controller.mjs").read_text(encoding="utf-8")
        self.assertIn("CustomTitlebar", controller)
        self.assertIn("argon-bottom", controller)

        css = (REPO / "browser" / "themes" / "shared" / "argon.css").read_text(encoding="utf-8")
        self.assertIn('data-argon-toolbar-placement="top-reversed"', css)
        self.assertIn(":root[data-argon-color-scheme][data-argon-color-scheme] body", css)
        self.assertIn("forced-colors: active", css)

        palette = (REPO / "argon" / "commands" / "command-palette.mjs").read_text(encoding="utf-8")
        self.assertNotIn("Linux desktop color scheme", palette)

        runner = (REPO / "scripts" / "run-browser-tests.mjs").read_text(encoding="utf-8")
        self.assertIn("argon.exe", runner)
        self.assertIn('path.join(".cache", "browser-venv", "Scripts", "python.exe")', runner)

        browser_support = load_module("browser_support", REPO / "tests" / "browser" / "_support.py")
        with mock.patch.object(browser_support.os, "name", "nt"):
            with mock.patch.dict(browser_support.os.environ, {}, clear=True):
                self.assertTrue(browser_support.default_firefox_binary().endswith("firefox.exe"))
        with mock.patch.object(browser_support.os, "name", "posix"):
            with mock.patch.dict(browser_support.os.environ, {}, clear=True):
                self.assertEqual(browser_support.argon_test_binary(fallback_to_firefox=True), "/usr/lib/firefox/firefox")


if __name__ == "__main__":
    unittest.main(verbosity=2)
