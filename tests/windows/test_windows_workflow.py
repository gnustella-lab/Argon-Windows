"""Exercise Windows workflow shell steps with isolated packaging fixtures."""

import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import textwrap
import unittest

REPO = Path(__file__).resolve().parents[2]
WORKFLOW = REPO / ".github/workflows/build-windows.yml"
PWSH = shutil.which("pwsh")


def shell_step(name):
    """Read a literal run block, without adding a YAML dependency to offline tests."""
    step = re.search(
        rf"^      - name: {re.escape(name)}\n(?:(?!^      - |^  \S).)*",
        WORKFLOW.read_text(encoding="utf-8"), re.MULTILINE | re.DOTALL,
    )
    if step is None:
        raise AssertionError(f"Missing workflow step: {name}")
    block = re.search(r"^        run: \|\n((?:^          .*\n|^\n)+)", step[0], re.MULTILINE)
    if block is None:
        raise AssertionError(f"Missing literal shell script for: {name}")
    return textwrap.dedent(block[1])


@unittest.skipUnless(os.name == "nt" and PWSH, "Windows PowerShell required")
class WindowsWorkflowShellTests(unittest.TestCase):
    def run_step(self, name, root, env=None, prelude="", cwd=REPO):
        script = root / "step.ps1"
        script.write_text('$ErrorActionPreference = "Stop"\n' + prelude + "\n" + shell_step(name), encoding="utf-8")
        assert PWSH is not None
        return subprocess.run(
            [PWSH, "-NoProfile", "-NonInteractive", "-File", str(script)],
            cwd=cwd, env=os.environ | (env or {}), capture_output=True, text=True,
        )

    def test_packaging_uses_existing_nsis_without_chocolatey(self):
        with tempfile.TemporaryDirectory(prefix="argon-workflow-") as temp:
            root = Path(temp)
            nsis = root / "existing nsis" / "makensis.exe"
            nsis.parent.mkdir()
            # Lookup checks existence only; this fixture is never executed.
            nsis.write_bytes(b"fixture")
            github_env = root / "github.env"
            result = self.run_step(
                "Prepare the Windows packaging tools", root,
                env={"ARGON_MAKENSIS": str(nsis), "GITHUB_ENV": str(github_env)},
                prelude='function choco { throw "Unexpected Chocolatey installation" }',
            )
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertIn(f"ARGON_MAKENSIS={nsis}", github_env.read_text(encoding="utf-8-sig"))

    def test_release_upload_keeps_windows_checksums_separate_from_linux(self):
        with tempfile.TemporaryDirectory(prefix="argon-workflow-") as temp:
            root = Path(temp)
            release = root / "win64-release"
            release.mkdir()
            names = ["argon-0.1.en-US.win64-portable.zip", "argon-0.1.en-US.win64.installer.exe"]
            for name in names:
                (release / name).write_bytes(b"fixture")
            sums = "fixture checksums\n"
            (release / "SHA256SUMS").write_text(sums, encoding="utf-8")
            captured = root / "upload.json"
            # Capture GH arguments only: no release is created or uploaded by tests.
            result = self.run_step(
                "Publish GitHub Release (tagged builds)", root, cwd=root,
                env={"GITHUB_REF": "refs/tags/v0.1", "GH_CAPTURE": str(captured)},
                prelude='''function gh {
                    $global:LASTEXITCODE = 0
                    if ($args[0] -eq 'release' -and $args[1] -eq 'upload') {
                        ConvertTo-Json -InputObject @($args) -Compress | Set-Content -LiteralPath $env:GH_CAPTURE
                    }
                }''',
            )
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            args = json.loads(captured.read_text(encoding="utf-8-sig"))
            uploaded = {Path(arg).name for arg in args[4:]}
            self.assertEqual(uploaded, set(names) | {"SHA256SUMS-win64"})
            self.assertEqual((release / "SHA256SUMS-win64").read_text(encoding="utf-8"), sums)
    def test_build_input_handoff_contains_only_runtime_and_evidence(self):
        with tempfile.TemporaryDirectory(prefix="argon-workflow-") as temp:
            root = Path(temp)
            workspace = root / "workspace"
            dist = workspace / "obj" / "dist"
            (dist / "bin").mkdir(parents=True)
            (dist / "bin" / "application.ini").write_text("Name=Argon\n", encoding="utf-8")
            runtime_name = "argon-162.0a1.en-US.win64.zip"
            for name in (runtime_name, "argon-162.0a1.en-US.win64.xpt_artifacts.zip", "argon-162.0a1.en-US.win64-portable.zip"):
                (dist / name).write_bytes(name.encode("ascii"))
            evidence = root / "artifacts" / "chrome-smoke"
            evidence.mkdir(parents=True)
            (evidence / "result.txt").write_text("fixture evidence", encoding="utf-8")
            result = self.run_step(
                "Stage the tested runtime and its smoke-test evidence", root, cwd=root,
                env={"ARGON_WORKSPACE": str(workspace)},
                prelude='function node { $global:LASTEXITCODE = 0 }',  # Do not compile Gecko in this test.
            )
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            handoff = root / "win64-build-input"
            self.assertEqual({p.name for p in handoff.iterdir()}, {runtime_name, "application.ini", "chrome-smoke"})
            self.assertEqual((handoff / runtime_name).read_bytes(), runtime_name.encode("ascii"))
            self.assertEqual((handoff / "chrome-smoke" / "result.txt").read_text(), "fixture evidence")


class WindowsWorkflowContractTests(unittest.TestCase):
    def test_cache_is_saved_before_compilation_with_matching_paths(self):
        source = WORKFLOW.read_text(encoding="utf-8")
        self.assertLess(source.index("npm run bootstrap"), source.index("uses: actions/cache/save@v4"))
        self.assertLess(source.index("uses: actions/cache/save@v4"), source.index("      - name: Build Argon Browser"))
        self.assertIn("uses: actions/cache/restore@v4", source)
        self.assertIn("path: &toolchain-paths |", source)
        self.assertIn("path: *toolchain-paths", source)
        self.assertIn("key: ${{ steps.toolchains.outputs.cache-primary-key }}", source)
        self.assertIn("if: steps.toolchains.outputs.cache-hit != 'true'", source)
        self.assertNotIn("\\.argon-workspace.json", source)

    def test_packaging_downloads_tested_input_instead_of_rebuilding(self):
        source = WORKFLOW.read_text(encoding="utf-8")
        package = source.split("  package-windows:\n", 1)[1].split("  publish-windows:\n", 1)[0]
        self.assertIn("needs: [plan, build-windows-x86_64]", package)
        self.assertIn("!cancelled()", package)
        self.assertIn("needs.build-windows-x86_64.result == 'success'", package)
        self.assertIn("needs.build-windows-x86_64.result == 'skipped' && needs.plan.outputs.runtime-run != ''", package)
        self.assertIn("run-id: ${{ needs.plan.outputs.runtime-run || github.run_id }}", package)
        self.assertIn("name: argon-win64-build-input", package)
        self.assertIn("--require-full-omni --run-portable-smoke", package)
        self.assertNotIn("scripts/argon.py", package)
        self.assertIn("if: github.event_name == 'push' && github.ref == 'refs/heads/main'", source)


if __name__ == "__main__":
    unittest.main()
