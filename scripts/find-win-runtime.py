#!/usr/bin/env python3
"""Print a reusable Windows build run ID, or nothing. Run from a full main checkout."""

from contextlib import closing
from datetime import datetime, timezone
import http.client
import json
import os
import re
import subprocess
import sys

ARTIFACT = "argon-win64-build-input"
WORKFLOW = ".github/workflows/build-windows.yml"
# Exact Git paths only. stage-runtime uses argon.py, settings, the Flatpak
# policy fallback and the native DLL, NOT win_package.py or launcher assets.
# The package job reruns the packaging tests against the current checkout.
PACKAGING_ONLY = {
    b"scripts/win_package.py",
    b"tests/windows/test_windows_packaging.py",
    b"packaging/windows/argon-installer.nsi",
    b"packaging/windows/portable/ArgonPortable.cs",
    b"packaging/windows/portable/README-PORTABLE.txt",
    b"packaging/windows/portable/argon-portable.c",
    b"packaging/windows/portable/argon-portable.manifest",
    b"packaging/windows/portable/argon-portable.rc",
}


def get_metadata(repo, endpoint):
    # Fixed host, no redirects, no gh credentials and no artifact downloads.
    with closing(http.client.HTTPSConnection("api.github.com", timeout=15)) as conn:
        conn.request("GET", f"/repos/{repo}/actions/{endpoint}", headers={
            "Authorization": "Bearer " + os.environ["GH_TOKEN"],
            "Accept": "application/vnd.github+json", "User-Agent": "argon-runtime-lookup",
            "X-GitHub-Api-Version": "2022-11-28",
        })
        response = conn.getresponse()
        if response.status != 200:
            raise OSError(f"GitHub returned HTTP {response.status}")
        return json.load(response)


def compatible_tree(sha):
    if not isinstance(sha, str) or not re.fullmatch(r"[0-9a-f]{40}", sha) or sha == "0" * 40:
        return False
    git = ["git", "--no-replace-objects"]
    ancestry = subprocess.run([*git, "merge-base", "--is-ancestor", sha, "HEAD"], capture_output=True)
    if ancestry.returncode == 1:
        return False
    ancestry.check_returncode()
    # Compare endpoints, not just the latest commit. No rename folding, quoting,
    # relative paths or ignored submodules that could hide a non-packaging change.
    paths = subprocess.check_output([*git, "diff-tree", "-r", "-z", "--name-only", "--no-renames",
                                     "--no-relative", "--ignore-submodules=none", sha, "HEAD", "--"])
    return all(path in PACKAGING_ONLY for path in paths.split(b"\0") if path)


def api_id(value):
    if type(value) is not int or value <= 0:
        raise ValueError("invalid GitHub ID")
    return value


def find_run(repo, current):
    # Search one page of *named artifacts*, not one request per historical build.
    artifacts = get_metadata(repo, f"artifacts?name={ARTIFACT}&per_page=100")["artifacts"]
    for artifact in artifacts:
        if artifact["name"] != ARTIFACT or artifact["expired"] is not False:
            continue
        if datetime.fromisoformat(artifact["expires_at"].replace("Z", "+00:00")) <= datetime.now(timezone.utc):
            continue
        source = artifact["workflow_run"]
        run_id, sha, repo_id = api_id(source["id"]), source["head_sha"], api_id(source["repository_id"])
        if not (run_id < current and source["head_branch"] == "main"
                and api_id(source["head_repository_id"]) == repo_id
                and compatible_tree(sha)):
            continue
        # A matching SHA or artifact name alone does not identify its producer.
        # Accept a failed packaging conclusion: the upload gates are upstream.
        run = get_metadata(repo, f"runs/{run_id}")
        if (api_id(run["id"]) == run_id and run["head_sha"] == sha and run["event"] == "push"
                and run["head_branch"] == "main" and run["path"] == WORKFLOW
                and all(api_id(run[k]["id"]) == repo_id and run[k]["full_name"].lower() == repo.lower()
                        for k in ("repository", "head_repository"))):
            return run_id
    return None


def main():
    try:
        repo, current = os.environ["GITHUB_REPOSITORY"], os.environ["GITHUB_RUN_ID"]
        if not (os.environ.get("GH_TOKEN") and re.fullmatch(r"[A-Za-z0-9-]+/[A-Za-z0-9_.-]+", repo)
                and repo.split("/")[1] not in (".", "..") and re.fullmatch(r"[1-9][0-9]*", current)):
            raise ValueError("invalid workflow environment")
        run_id = find_run(repo, int(current))
    except (OSError, http.client.HTTPException, ValueError, KeyError, TypeError, AttributeError,
            subprocess.SubprocessError) as exc:
        # Never log response bodies, headers or token values.
        print(f"Runtime lookup failed ({type(exc).__name__}); compiling.", file=sys.stderr)
        return
    if run_id is None:
        print("No compatible retained runtime; compiling.", file=sys.stderr)
    else:
        print(run_id)


if __name__ == "__main__":
    main()
