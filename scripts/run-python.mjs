#!/usr/bin/env node
// SPDX-License-Identifier: MIT
/**
 * Cross-platform Python 3 launcher for Argon npm scripts and Node tools.
 *
 * On Windows, Microsoft Store App Execution Aliases (`WindowsApps\python3.exe`)
 * appear on PATH as 0-byte reparse points that fail or open the Store. This
 * helper discovers a real Python >= 3.9 interpreter cleanly via:
 *   1. ARGON_PYTHON
 *   2. Active virtualenv / conda (VIRTUAL_ENV / CONDA_PREFIX)
 *   3. Non-Store `python3` / `python` binaries on PATH
 *   4. Windows `py -3` launcher
 *   5. MozillaBuild Python (`MOZILLABUILD` / `C:\mozilla-build\python3`)
 *   6. Standard Python 3 install directories on Windows
 */
import { spawnSync } from "node:child_process";
import fs from "node:fs";
import path from "node:path";
import process from "node:process";
import { pathToFileURL } from "node:url";

const PROBE_CODE =
  "import sys; print(sys.executable); raise SystemExit(0 if sys.version_info >= (3, 9) else 1)";

export function isWindowsStoreAlias(filePath) {
  if (process.platform !== "win32" || !filePath) {
    return false;
  }
  const normalized = path.resolve(filePath).toLowerCase();
  if (normalized.includes(`${path.sep}microsoft${path.sep}windowsapps${path.sep}`)) {
    return true;
  }
  try {
    const stat = fs.statSync(filePath);
    if (path.basename(normalized).startsWith("python") && stat.size === 0) {
      return true;
    }
  } catch (err) {
    if (err && (err.code === "EINVAL" || err.errno === -4071 || err.code === "UNKNOWN") &&
        path.basename(normalized).startsWith("python")) {
      return true;
    }
    return false;
  }
  return false;
}

function whichAll(name, env = process.env) {
  const pathEnv = env.PATH || "";
  if (!pathEnv) {
    return [];
  }
  const isWin = process.platform === "win32";
  const hasExt = path.extname(name) !== "";
  let names = [name];
  if (isWin && !hasExt) {
    const exts = (env.PATHEXT || ".EXE;.CMD;.BAT")
      .split(";")
      .map((ext) => ext.trim())
      .filter(Boolean);
    names = [];
    const seenExt = new Set();
    for (const ext of exts) {
      const lower = ext.toLowerCase();
      if (!seenExt.has(lower)) {
        seenExt.add(lower);
        names.push(name + lower);
      }
    }
    names.push(name);
  }

  const results = [];
  const seenPaths = new Set();
  for (const rawDir of pathEnv.split(path.delimiter)) {
    const dir = rawDir.replace(/^"+|"+$/g, "").trim();
    if (!dir) {
      continue;
    }
    for (const candidateName of names) {
      const candidate = path.join(dir, candidateName);
      try {
        const stat = fs.statSync(candidate);
        if (!stat.isFile()) {
          continue;
        }
        if (!isWin) {
          fs.accessSync(candidate, fs.constants.X_OK);
        }
        const key = isWin ? candidate.toLowerCase() : candidate;
        if (!seenPaths.has(key)) {
          seenPaths.add(key);
          results.push(candidate);
        }
      } catch {
        // Ignore missing/inaccessible entries or broken WindowsApps stat entries.
      }
    }
  }
  return results;
}

export function probePython(candidate) {
  if (!candidate || isWindowsStoreAlias(candidate)) {
    return null;
  }
  try {
    const res = spawnSync(candidate, ["-c", PROBE_CODE], {
      encoding: "utf8",
      timeout: 5000,
      windowsHide: true,
    });
    if (res.error || res.status !== 0) {
      return null;
    }
    const lines = (res.stdout || "").trim().split(/\r?\n/).filter(Boolean);
    const executable = lines.at(-1)?.trim();
    if (!executable || isWindowsStoreAlias(executable)) {
      return null;
    }
    if (!fs.existsSync(executable)) {
      return null;
    }
    return executable;
  } catch {
    return null;
  }
}

function windowsInstallCandidates(env = process.env) {
  const roots = [];
  if (env.LOCALAPPDATA) {
    roots.push(path.join(env.LOCALAPPDATA, "Programs", "Python"));
  }
  if (env.ProgramFiles) {
    roots.push(env.ProgramFiles);
  }
  if (env.ProgramW6432) {
    roots.push(env.ProgramW6432);
  }
  roots.push(`${env.SystemDrive || "C:"}\\`);

  const matches = [];
  for (const root of roots) {
    try {
      if (!fs.existsSync(root)) {
        continue;
      }
      for (const entry of fs.readdirSync(root, { withFileTypes: true })) {
        if (!entry.isDirectory()) {
          continue;
        }
        const m = /^Python3(\d+)(?:-\d+)?$/i.exec(entry.name);
        if (!m) {
          continue;
        }
        const exe = path.join(root, entry.name, "python.exe");
        if (fs.existsSync(exe)) {
          matches.push({ minor: Number.parseInt(m[1], 10), exe });
        }
      }
    } catch {
      // Ignore unreadable directories.
    }
  }
  matches.sort((a, b) => b.minor - a.minor);
  return matches.map((m) => m.exe);
}

function firstWorkingPython(candidates, seen, isWin) {
  for (const candidate of candidates) {
    const key = isWin ? path.resolve(candidate).toLowerCase() : path.resolve(candidate);
    if (seen.has(key)) {
      continue;
    }
    seen.add(key);
    if (!fs.existsSync(candidate) || isWindowsStoreAlias(candidate)) {
      continue;
    }
    const probed = probePython(candidate);
    if (probed) {
      return probed;
    }
  }
  return null;
}

export function findPython(env = process.env) {
  const explicit = (env.ARGON_PYTHON || "").trim();
  if (explicit) {
    if (fs.existsSync(explicit)) {
      const probed = probePython(explicit);
      if (probed) {
        return probed;
      }
    }
    for (const match of whichAll(explicit, env)) {
      const probed = probePython(match);
      if (probed) {
        return probed;
      }
    }
    throw new Error(
      `ARGON_PYTHON is set to ${JSON.stringify(explicit)}, which is not a working Python >= 3.9 interpreter.`,
    );
  }

  const isWin = process.platform === "win32";
  const candidates = [];
  const seen = new Set();

  if (isWin) {
    for (const envKey of ["VIRTUAL_ENV", "CONDA_PREFIX"]) {
      if (env[envKey]) {
        candidates.push(path.join(env[envKey], "Scripts", "python.exe"));
      }
    }

    for (const name of ["python3", "python"]) {
      candidates.push(...whichAll(name, env));
    }

    const foundPrimary = firstWorkingPython(candidates, seen, isWin);
    if (foundPrimary) {
      return foundPrimary;
    }

    for (const pyLauncher of whichAll("py", env)) {
      if (isWindowsStoreAlias(pyLauncher)) {
        continue;
      }
      const res = spawnSync(pyLauncher, ["-3", "-c", PROBE_CODE], {
        encoding: "utf8",
        timeout: 5000,
        windowsHide: true,
      });
      if (!res.error && res.status === 0) {
        const lines = (res.stdout || "").trim().split(/\r?\n/).filter(Boolean);
        const executable = lines.at(-1)?.trim();
        if (executable && !isWindowsStoreAlias(executable) && fs.existsSync(executable)) {
          return executable;
        }
      }
    }

    candidates.length = 0;
    const mozBuildRoots = [];
    if (env.MOZILLABUILD) {
      mozBuildRoots.push(env.MOZILLABUILD);
    }
    mozBuildRoots.push("C:\\mozilla-build", "D:\\mozilla-build");
    for (const root of mozBuildRoots) {
      candidates.push(
        path.join(root, "python3", "python3.exe"),
        path.join(root, "python3", "python.exe"),
      );
    }

    candidates.push(...windowsInstallCandidates(env));
  } else {
    if (env.VIRTUAL_ENV) {
      candidates.push(
        path.join(env.VIRTUAL_ENV, "bin", "python3"),
        path.join(env.VIRTUAL_ENV, "bin", "python"),
      );
    }
    for (const name of ["python3", "python"]) {
      candidates.push(...whichAll(name, env));
    }
  }

  return firstWorkingPython(candidates, seen, isWin);
}

export function runPython(args, options = {}) {
  const env = options.env || process.env;
  const python = findPython(env);
  if (!python) {
    const detail =
      process.platform === "win32"
        ? [
            "No usable Python >= 3.9 interpreter was found.",
            "Note: Microsoft Store WindowsApps python/python3 stubs are ignored.",
            "Install Python 3.12+ (e.g. from python.org or MozillaBuild) or set ARGON_PYTHON.",
          ].join(" ")
        : "No usable Python >= 3.9 interpreter was found on PATH. Set ARGON_PYTHON or install python3.";
    throw new Error(detail);
  }

  const childEnv = { ...env };
  if (!childEnv.PYTHONUTF8) {
    childEnv.PYTHONUTF8 = "1";
  }
  if (!childEnv.PYTHONIOENCODING) {
    childEnv.PYTHONIOENCODING = "utf-8";
  }

  const res = spawnSync(python, args, {
    stdio: options.stdio || "inherit",
    cwd: options.cwd,
    env: childEnv,
  });
  if (res.error) {
    throw res.error;
  }
  if (res.signal) {
    return 1;
  }
  return res.status ?? 1;
}

function main() {
  const args = process.argv.slice(2);
  if (args.length === 0) {
    console.error("Usage: node scripts/run-python.mjs <python-args...>");
    process.exit(2);
  }
  try {
    process.exit(runPython(args));
  } catch (error) {
    console.error(`run-python: ${error instanceof Error ? error.message : String(error)}`);
    process.exit(1);
  }
}

if (process.argv[1] && pathToFileURL(path.resolve(process.argv[1])).href === import.meta.url) {
  main();
}
