"""Offline tests for Windows runtime reuse: real Git history, fake Actions metadata."""

import contextlib
import importlib.util
import io
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "find-win-runtime.py"
REPO = "example/argon"
NAME = "argon-win64-build-input"
WORKFLOW = ".github/workflows/build-windows.yml"
PACKAGER = "scripts/win_package.py"


class RuntimeReuseTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        spec = importlib.util.spec_from_file_location("find_win_runtime", SCRIPT)
        assert spec is not None and spec.loader is not None
        cls.helper = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cls.helper)

    def setUp(self):
        self.enterContext(contextlib.chdir(self.enterContext(tempfile.TemporaryDirectory())))
        self.git("init", "-q", "-b", "main")
        self.git("config", "user.name", "Test")
        self.git("config", "user.email", "test@example.invalid")
        self.source = self.commit(PACKAGER)
        self.artifact = {"name": NAME, "expired": False, "expires_at": "2099-01-01T00:00:00Z",
                         "workflow_run": {"id": 10, "head_branch": "main", "head_sha": self.source,
                                          "repository_id": 2, "head_repository_id": 2}}
        repository = {"full_name": REPO, "id": 2}
        self.producer = {"id": 10, "event": "push", "head_branch": "main", "path": WORKFLOW,
                         "head_sha": self.source, "repository": repository, "head_repository": repository,
                         "status": "completed", "conclusion": "failure"}

    def git(self, *args):
        return subprocess.check_output(["git", "-c", "commit.gpgSign=false", "-c", "core.hooksPath=.git/no-hooks",
                                        *args], stderr=subprocess.PIPE, text=True).strip()

    def commit(self, *names):
        for name in names:
            path = Path(name)
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open("ab") as handle:
                handle.write(b"change\n")
        self.git("add", "-A")
        self.git("commit", "-qm", "fixture", "--allow-empty")
        return self.git("rev-parse", "HEAD")

    def invoke(self, artifacts=None, run=None, error=None):
        def get(repo, endpoint):
            self.assertEqual(repo, REPO)
            if endpoint == f"artifacts?name={NAME}&per_page=100":
                return {"artifacts": [self.artifact] if artifacts is None else artifacts}
            self.assertEqual(endpoint, "runs/10")
            if error:
                raise error
            return self.producer if run is None else run

        env = {"GITHUB_REPOSITORY": REPO, "GITHUB_RUN_ID": "20", "GH_TOKEN": "test-only"}
        out, err = io.StringIO(), io.StringIO()
        with mock.patch.dict(os.environ, env), mock.patch.object(self.helper, "get_metadata", get), \
                contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            self.helper.main()
        if not out.getvalue():
            self.assertTrue(err.getvalue())
            self.assertNotIn(env["GH_TOKEN"], err.getvalue())
        return out.getvalue()

    def test_packaging_only_reuses_artifact_even_if_packaging_failed(self):
        self.commit(PACKAGER, "tests/windows/test_windows_packaging.py", "packaging/windows/argon-installer.nsi",
                    *(f"packaging/windows/portable/{name}" for name in
                      ("ArgonPortable.cs", "argon-portable.c", "argon-portable.rc", "argon-portable.manifest",
                       "README-PORTABLE.txt")))
        self.assertEqual(self.invoke(), "10\n")

    def test_mixed_history_including_stage_inputs_requires_compile(self):
        for path in ("packaging/flatpak/policies.json", "settings/distribution/policies.json",
                     "settings/librewolf.cfg", "settings/defaults/pref/local-settings.js",
                     "argon/adblock/argon_adblock.dll", "tests/browser/test_chrome.py", WORKFLOW,
                     "scripts/argon.py", "packaging/windows/portable/new-input"):
            with self.subTest(path=path):
                base = self.git("rev-parse", "HEAD")
                self.artifact["workflow_run"]["head_sha"] = self.producer["head_sha"] = base
                self.commit(path)
                self.commit(PACKAGER)  # Looking only at the last commit would allow this.
                self.assertEqual(self.invoke(), "")

    def test_rename_from_outside_allowlist_requires_compile(self):
        before = self.commit("runtime-input")
        dest = Path("packaging/windows/argon-installer.nsi")
        dest.parent.mkdir(parents=True)
        Path("runtime-input").rename(dest)
        self.commit()
        self.assertFalse(self.helper.compatible_tree(before))

    def test_newer_non_ancestor_with_identical_tree_is_not_reusable(self):
        other = self.commit()
        self.git("checkout", "-q", "--detach", self.source)
        self.assertFalse(self.helper.compatible_tree(other))

    def test_malformed_sha_never_reaches_git(self):
        with mock.patch.object(subprocess, "run", side_effect=AssertionError("unexpected Git invocation")):
            for sha in (None, "", "1234567", "HEAD", "--help", "0" * 40, self.source + "\n"):
                with self.subTest(sha=sha):
                    self.assertFalse(self.helper.compatible_tree(sha))

    def test_absent_expired_or_misnamed_artifact_is_not_reused(self):
        self.assertEqual(self.invoke(artifacts=[]), "")
        for change in ({"expired": True}, {"expired": None}, {"expires_at": "2000-01-01T00:00:00Z"},
                       {"name": NAME + "-old"}, {"workflow_run": None}):
            with self.subTest(change=change):
                self.assertEqual(self.invoke([self.artifact | change]), "")

    def test_artifact_must_come_from_prior_same_repo_main_run(self):
        for change in ({"id": 20}, {"id": 21}, {"id": "10"}, {"head_branch": "topic"}, {"head_repository_id": 3}):
            with self.subTest(change=change):
                source = self.artifact["workflow_run"] | change
                self.assertEqual(self.invoke([self.artifact | {"workflow_run": source}]), "")

    def test_search_can_reach_an_older_retained_artifact(self):
        newer = self.artifact | {"workflow_run": self.artifact["workflow_run"] | {"id": 11}, "expired": True}
        self.assertEqual(self.invoke([newer, self.artifact]), "10\n")

    def test_artifact_must_match_its_own_trusted_producing_run(self):
        # In particular a trusted run for the *same SHA* cannot vouch for another run's upload.
        for change in ({"id": 9}, {"id": 10.0}, {"head_sha": "a" * 40}, {"event": "pull_request"},
                       {"event": "workflow_dispatch"}, {"path": ".github/workflows/ci.yml"},
                       {"head_branch": "topic"}, {"repository": {"id": 3, "full_name": REPO}},
                       {"head_repository": {"id": 2, "full_name": "fork/argon"}}):
            with self.subTest(change=change):
                self.assertEqual(self.invoke(run=self.producer | change), "")

    def test_lookup_errors_and_missing_git_object_fall_back(self):
        for error in (OSError("test-only"), ValueError("invalid JSON")):
            with self.subTest(error=error):
                self.assertEqual(self.invoke(error=error), "")
        source = self.artifact["workflow_run"] | {"head_sha": "b" * 40}
        self.assertEqual(self.invoke([self.artifact | {"workflow_run": source}]), "")

    def test_cli_without_token_has_empty_stdout_and_exits_successfully(self):
        proc = subprocess.run([sys.executable, "-B", str(SCRIPT)], capture_output=True,
                              env=os.environ | {"GH_TOKEN": "", "GITHUB_REPOSITORY": REPO, "GITHUB_RUN_ID": "20"})
        self.assertEqual((proc.returncode, proc.stdout), (0, b""))
        self.assertTrue(proc.stderr)


if __name__ == "__main__":
    unittest.main()
