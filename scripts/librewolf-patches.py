#!/usr/bin/env python3
# SPDX-License-Identifier: MPL-2.0
"""Apply LibreWolf & Argon patches, l10n updates, settings, branding, and overlay."""

import datetime
import json
import os
import pathlib
import re
import shutil
import subprocess
import sys

_SCRIPTS_DIR = str(pathlib.Path(__file__).resolve().parent)
if _SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, _SCRIPTS_DIR)

import platform_compat

REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]


def mkdirp(path):
    os.makedirs(path, exist_ok=True)


def cp(src, dst):
    dst_path = pathlib.Path(dst)
    dst_path.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src, dst_path)


def cptree(src, dst):
    shutil.copytree(src, dst, dirs_exist_ok=True)


def rm(path):
    if os.path.exists(path):
        os.remove(path)


def rmdir(path):
    if os.path.exists(path):
        platform_compat.rmtree_retry(pathlib.Path(path))


def append_file(src_rel, dst_rel):
    src = pathlib.Path(src_rel)
    dst = pathlib.Path(dst_rel)
    if not src.is_file():
        print(f"fatal error: missing l10n append source '{src}'", file=sys.stderr)
        sys.exit(1)
    dst.parent.mkdir(parents=True, exist_ok=True)
    chunk = src.read_text(encoding="utf-8")
    existing = dst.read_text(encoding="utf-8") if dst.is_file() else ""
    if existing and not existing.endswith("\n"):
        existing += "\n"
    platform_compat.write_text_lf(dst, existing + chunk)


def append_lines(dst_rel, lines):
    dst = pathlib.Path(dst_rel)
    dst.parent.mkdir(parents=True, exist_ok=True)
    existing = dst.read_text(encoding="utf-8") if dst.is_file() else ""
    if existing and not existing.endswith("\n"):
        existing += "\n"
    platform_compat.write_text_lf(dst, existing + "\n".join(lines) + "\n")


def replace_text_in_file(dst_rel, old, new):
    dst = pathlib.Path(dst_rel)
    if not dst.is_file():
        print(f"fatal error: missing replacement target '{dst}'", file=sys.stderr)
        sys.exit(1)
    text = dst.read_text(encoding="utf-8")
    platform_compat.write_text_lf(dst, text.replace(old, new))


def _find_patch_tool():
    """Find GNU patch or fall back to git apply (works on Windows without MSYS patch)."""
    patch_bin = shutil.which(platform_compat.binary_name("patch")) or shutil.which("patch")
    if patch_bin:
        return ("patch", patch_bin)
    if platform_compat.is_windows():
        git_bin = shutil.which("git.exe") or shutil.which("git")
        if git_bin:
            git_patch = pathlib.Path(git_bin).resolve().parents[1] / "usr" / "bin" / "patch.exe"
            if git_patch.is_file():
                return ("patch", str(git_patch))
        mb = platform_compat.find_mozilla_build()
        if mb:
            mb_patch = mb / "msys2" / "usr" / "bin" / "patch.exe"
            if mb_patch.is_file():
                return ("patch", str(mb_patch))
    git_bin = shutil.which(platform_compat.binary_name("git")) or shutil.which("git")
    if git_bin:
        return ("git", git_bin)
    return None


def _current_platform_tags():
    if platform_compat.is_windows():
        return {"windows", "win32", "win"}
    if platform_compat.is_macos():
        return {"macos", "darwin", "osx", "unix", "posix"}
    return {"linux", "unix", "posix"}


def parse_patch_entry(raw_line):
    """Parse an assets/patches.txt line, supporting optional `[platform]` tags.

    Plain lines (`patches/foo.patch`) apply on every platform.
    Comments (`# ...`) and blank lines return None.
    Optional inline tags (`patches/foo.patch [linux]` or `[linux] patches/foo.patch`)
    apply only when the current host matches one of the comma-separated tags.
    """
    line = raw_line.strip()
    if not line or line.startswith("#"):
        return None
    line_no_comment = line.split(" #", 1)[0].strip()
    if not line_no_comment:
        return None
    tag_match = re.match(r"^\[([A-Za-z0-9_,-]+)\]\s+(.+)$", line_no_comment)
    if tag_match:
        tags = {t.strip().lower() for t in tag_match.group(1).split(",") if t.strip()}
        patch_path = tag_match.group(2).strip()
    else:
        trail_match = re.match(r"^(.+?)\s+\[([A-Za-z0-9_,-]+)\]$", line_no_comment)
        if trail_match:
            patch_path = trail_match.group(1).strip()
            tags = {t.strip().lower() for t in trail_match.group(2).split(",") if t.strip()}
        else:
            patch_path = line_no_comment
            tags = None

    if tags is not None and not (tags & _current_platform_tags()):
        return None
    return patch_path


def patch(patchfile):
    patch_path = pathlib.Path("..") / patchfile
    if not patch_path.is_file():
        patch_path = REPO_ROOT / patchfile
    tool = _find_patch_tool()
    if tool is None:
        print("fatal error: neither 'patch' nor 'git' was found on PATH", file=sys.stderr)
        sys.exit(1)
    kind, binary = tool
    if kind == "patch":
        cmd = [binary, "-p1", "-i", str(patch_path)]
    else:
        cmd = [binary, "apply", "--whitespace=nowarn", "-p1", str(patch_path)]
    print(f"\n*** -> {' '.join(cmd)}")
    completed = subprocess.run(cmd, check=False)
    if completed.returncode != 0:
        print(f"fatal error: patch '{patchfile}' failed", file=sys.stderr)
        sys.exit(1)


def apply_argon_overlay():
    """Apply the Argon branding, design system tokens, UI controller, and preferences."""
    overlay_path = REPO_ROOT / "config" / "overlay.json"
    if not overlay_path.is_file():
        return
    overlay = json.loads(overlay_path.read_text(encoding="utf-8"))

    branding = overlay.get("branding")
    if branding:
        src_branding = REPO_ROOT / branding["from"]
        dst_branding = pathlib.Path(branding["to"])
        if dst_branding.exists():
            platform_compat.rmtree_retry(dst_branding)
        dst_branding.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(src_branding, dst_branding)

    for entry in overlay.get("files", []):
        src_file = REPO_ROOT / entry["from"]
        dst_file = pathlib.Path(entry["to"])
        dst_file.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src_file, dst_file)

    hooks_inc = REPO_ROOT / "browser" / "base" / "content" / "argon-window-hooks.inc.xhtml"
    if hooks_inc.is_file():
        dst_inc = pathlib.Path("browser/base/content/argon-window-hooks.inc.xhtml")
        dst_inc.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(hooks_inc, dst_inc)

    for hook in overlay.get("hooks", []):
        target = pathlib.Path(hook["path"])
        if not target.is_file():
            continue
        text = target.read_text(encoding="utf-8")

        if "entries" in hook:
            begin, end = "# BEGIN Argon overlay", "# END Argon overlay"
            lines = [begin]
            for item in hook["entries"]:
                lines.append(f"  {item['destination']} ({item['source']})")
            lines.append(end)
            block = "\n".join(lines)
            anchor = hook.get("anchor") or hook.get("insert_before")
            if begin not in text and anchor and anchor in text:
                text = text.replace(anchor, f"{block}\n{anchor}", 1)
                platform_compat.write_text_lf(target, text)

        elif hook.get("path", "").endswith(".xhtml") and "include" in hook:
            begin, end = "<!-- BEGIN Argon overlay -->", "<!-- END Argon overlay -->"
            inc_name = pathlib.Path(hook["include"]).name
            block = f"{begin}\n#include {inc_name}\n{end}"
            anchor = hook.get("anchor") or hook.get("insert_after")
            if begin not in text and anchor and anchor in text:
                text = text.replace(anchor, f"{anchor}\n{block}", 1)
                platform_compat.write_text_lf(target, text)

        elif hook.get("path", "").endswith(".js") and "include" in hook:
            begin, end = "// BEGIN Argon overlay", "// END Argon overlay"
            prefs_file = REPO_ROOT / hook["include"]
            if not prefs_file.is_file():
                prefs_file = REPO_ROOT / "argon" / "preferences" / hook["include"]
            if begin not in text and prefs_file.is_file():
                body = prefs_file.read_text(encoding="utf-8").strip()
                text = f"{text.rstrip()}\n\n{begin}\n{body}\n{end}\n"
                platform_compat.write_text_lf(target, text)

        elif "replace_line" in hook:
            begin, end = "# BEGIN Argon overlay", "# END Argon overlay"
            needle = hook["replace_line"]
            body = "\n".join([f"# upstream: {needle}", *hook["lines"]])
            block = f"{begin}\n{body}\n{end}"
            if begin not in text and needle in text:
                text = text.replace(needle, block, 1)
                platform_compat.write_text_lf(target, text)

        elif "replacements" in hook:
            updated = text
            for item in hook["replacements"]:
                if item["old"] in updated and item["new"] not in updated:
                    updated = updated.replace(item["old"], item["new"], 1)
            if updated != text:
                platform_compat.write_text_lf(target, updated)


def librewolf_patches(srcdir, version, release, *, create_mozconfig=True):
    print(f"Entering '{srcdir}'...")
    prev_cwd = os.getcwd()
    os.chdir(srcdir)
    try:
        if create_mozconfig:
            win_moz = REPO_ROOT / "config" / "mozconfig.windows"
            if platform_compat.is_windows() and win_moz.is_file():
                cp(win_moz, "mozconfig")
            elif (REPO_ROOT / "assets" / "mozconfig.new").is_file():
                cp(REPO_ROOT / "assets" / "mozconfig.new", "mozconfig")

        with open(REPO_ROOT / "assets" / "patches.txt", encoding="utf-8") as f:
            patch_files = []
            for raw_line in f:
                parsed = parse_patch_entry(raw_line)
                if parsed:
                    patch_files.append(parsed)

        if (REPO_ROOT / "themes" / "browser").is_dir():
            cptree(REPO_ROOT / "themes" / "browser", pathlib.Path("browser"))
        if (REPO_ROOT / "browser" / "branding" / "argon").is_dir():
            cptree(REPO_ROOT / "browser" / "branding" / "argon", pathlib.Path("browser/branding/argon"))

        for patch_file in patch_files:
            patch(patch_file)

        if (REPO_ROOT / "settings").is_dir():
            cptree(REPO_ROOT / "settings", pathlib.Path("settings"))

        release_ts = REPO_ROOT / "release-timestamp"
        if release_ts.is_file():
            timestamp = int(release_ts.read_text(encoding="utf-8").strip())
            timestamp_str = datetime.datetime.fromtimestamp(
                timestamp, datetime.timezone.utc
            ).strftime("%Y%m%d%H%M%S")
            print(f"build-id: {timestamp_str}")
            platform_compat.write_text_lf("build-id", timestamp_str + "\n")

        if "rc" in version:
            clean_version = version.split("rc")[0]
            version_display = f"{clean_version}-{release}\n"
        else:
            version_display = f"{version}-{release}\n"

        print(f"version_display.txt: {version_display.strip()}")
        pathlib.Path("config").mkdir(parents=True, exist_ok=True)
        platform_compat.write_text_lf("config/version_display.txt", version_display)

        mkdirp("browser/themes/shared/preferences")
        cp(
            REPO_ROOT / "patches/pref-pane/category-librewolf.svg",
            "browser/themes/shared/preferences/category-librewolf.svg",
        )
        cp(
            REPO_ROOT / "patches/pref-pane/librewolf.css",
            "browser/themes/shared/preferences/librewolf.css",
        )
        mkdirp("browser/components/preferences")
        cp(
            REPO_ROOT / "patches/pref-pane/librewolf.inc.xhtml",
            "browser/components/preferences/librewolf.inc.xhtml",
        )
        cp(
            REPO_ROOT / "patches/pref-pane/librewolf.js",
            "browser/components/preferences/librewolf.js",
        )

        if (REPO_ROOT / "l10n").is_dir():
            for src_rel, dst_rel in (
                ("l10n/en-US/browser/browser/preferences/preferences.inc.ftl", "browser/locales/en-US/browser/preferences/preferences.ftl"),
                ("l10n/en-US/browser/browser/preferences/permissions.inc.ftl", "browser/locales/en-US/browser/preferences/permissions.ftl"),
                ("l10n/en-US/browser/browser/aboutDialog.inc.ftl", "browser/locales/en-US/browser/aboutDialog.ftl"),
                ("l10n/en-US/browser/browser/browser.inc.ftl", "browser/locales/en-US/browser/browser.ftl"),
                ("l10n/en-US/browser/browser/translations.inc.ftl", "browser/locales/en-US/browser/translations.ftl"),
                ("l10n/en-US/browser/chrome/browser/sitePermissions.inc.properties", "browser/locales/en-US/chrome/browser/sitePermissions.properties"),
                ("l10n/en-US/browser/chrome/browser/browser.inc.properties", "browser/locales/en-US/chrome/browser/browser.properties"),
                ("l10n/en-US/toolkit/toolkit/pictureinpicture/pictureinpicture.inc.ftl", "toolkit/locales/en-US/toolkit/pictureinpicture/pictureinpicture.ftl"),
                ("l10n/en-US/browser/browser/newtab/newtab.inc.ftl", "browser/locales/en-US/browser/newtab/newtab.ftl"),
                ("l10n/en-US/browser/browser/customkeys.inc.ftl", "browser/locales/en-US/browser/customkeys.ftl"),
            ):
                append_file(REPO_ROOT / src_rel, dst_rel)

            cp(
                REPO_ROOT / "l10n/en-US/toolkit/toolkit/branding/brandings.ftl",
                "toolkit/locales/en-US/toolkit/branding/brandings.ftl",
            )

        appstrings = pathlib.Path("browser/locales/en-US/chrome/browser/appstrings.properties")
        if appstrings.is_file():
            replace_text_in_file(appstrings, "Firefox", "Argon")

        append_lines(
            "browser/locales/en-US/browser/aboutDialog.ftl",
            [
                "",
                "bottomLinks-license = Licensing Information",
                "bottomLinks-rights = End-User Rights",
                "bottomLinks-privacy = Privacy Policy",
            ],
        )

        apply_argon_overlay()

        search_cfg = REPO_ROOT / "assets" / "search-config.json"
        if search_cfg.is_file():
            cp(search_cfg, "services/settings/dumps/main/search-config-v2.json")
        else:
            search_v2 = REPO_ROOT / "assets" / "search-config-v2.json"
            if search_v2.is_file():
                cp(search_v2, "services/settings/dumps/main/search-config-v2.json")

        ubo_assets = REPO_ROOT / "assets" / "uBOAssets.json"
        if ubo_assets.is_file():
            cp(ubo_assets, "settings/uBOAssets.json")
    finally:
        print(f"Leaving '{srcdir}'...")
        os.chdir(prev_cwd)


def main(argv=None):
    start_time = datetime.datetime.now()
    args = list(sys.argv[1:] if argv is None else argv)
    no_execute = False
    create_mozconfig = True
    while args:
        if args[0] == "--no-execute":
            no_execute = True
            args.pop(0)
        elif args[0] == "--no-mozconfig":
            create_mozconfig = False
            args.pop(0)
        else:
            break

    if len(args) != 2:
        sys.stderr.write("error: please specify version and release of Argon source\n")
        return 1

    version, release = args[0], args[1]
    srcdir = f"argon-{version}-{release}"
    if not os.path.exists(f"{srcdir}/configure.py") and os.path.exists(f"librewolf-{version}-{release}/configure.py"):
        srcdir = f"librewolf-{version}-{release}"
    if not os.path.exists(f"{srcdir}/configure.py"):
        print(f"fatal error: '{srcdir}' directory not found.")
        return 1

    if not no_execute:
        librewolf_patches(srcdir, version, release, create_mozconfig=create_mozconfig)
        elapsed = datetime.datetime.now() - start_time
        elapsed_seconds = elapsed.total_seconds()
        minutes = int(elapsed_seconds // 60)
        seconds = int(elapsed_seconds % 60)
        microseconds = elapsed.microseconds
        print("-> Elapsed time: {:02d}:{:02d}.{:02d}".format(minutes, seconds, microseconds))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
