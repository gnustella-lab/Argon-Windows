# Argon Browser on Windows (10 / 11 x64)

Status: native Windows `x86_64` (`win64`) build tooling, Windows branding assets (`.ico`, `.bmp`, `VisualElements`, `branding.nsi`), isolated portable launcher (`argon-portable.exe`), NSIS installer (`packaging/windows/argon-installer.nsi`), runtime compatibility, and Windows contract tests are implemented and verified at the script/contract/launcher level. Full upstream Gecko C++/Rust compilation on Windows requires a clean Windows 10/11 `x64` host with Visual Studio / MozillaBuild toolchains (`~55-65 GB` free disk) and has not yet been compiled end-to-end on the constrained agent VM.

## System requirements (clean machine)

Toolchain baselines verified against Argon's pinned Firefox revision (`d9bd6be9a67efb1c2667ef91a89856f35eb7cf5e` in `config/firefox.lock.json`):

| Component | Requirement | Notes |
| --- | --- | --- |
| **OS** | Windows 10 or Windows 11 (64-bit `x64`) | Required by both the build toolchain and `packaging/windows/argon-installer.nsi` |
| **RAM** | **8 GB minimum**, **16 GB+ recommended** | Enable a Windows pagefile (>= 8 GB) for Rust LTO and `xul.dll` linking |
| **Disk space** | **55–65 GB free** on an ASCII, space-free path | Upstream source (~4 GB) + `objdir` (~30 GB) + `.mozbuild`/VS/Rust (~12–15 GB) |
| **Build time** | ~90–240 minutes cold compile (4–8 cores) | Incremental rebuilds and front-end `npm run dev` syncs are fast |
| **MozillaBuild** | **4.0 or newer** | Install to `C:\mozilla-build` (or set `MOZILLABUILD`); provides MSYS2 `sh.exe`/`bash.exe` and `patch` |
| **Visual Studio / Build Tools** | **VS 2022+** (`MSVC >= 19.30`) with **C++ ATL/MFC** + **Windows 10 SDK `>= 10.0.17134.0`** | Or let `npm run bootstrap` download `.mozbuild\vs` (`--enable-bootstrap`) |
| **LLVM / Clang** | `clang-cl >= 19.0`, `lld-link`, `llvm-dlltool` | Installed automatically into `%USERPROFILE%\.mozbuild\clang` by `npm run bootstrap` |
| **Rust** | `rustc >= 1.95.0` (`x86_64-pc-windows-msvc`) | Installed by `npm run bootstrap` or `rustup`; add `wasm32-unknown-unknown` only if rebuilding `native/adblock` |
| **Python** | **3.10 or newer** (Argon scripts work on 3.9+) | **Avoid the 0-byte Microsoft Store `WindowsApps\python3.exe` alias** — see below |
| **Node.js** | **22 or newer** | Upstream `mach` needs >= 12.22.12; Argon tooling & `npm test` require >= 22 |
| **NSIS** | **3.0b1 or newer** (32-bit `makensis.exe`) | Optional; required only for `npm run win:installer` |

### Note on `WindowsApps\python3.exe` and `ARGON_PYTHON`

Windows 10/11 ships 0-byte App Execution Aliases (`%LOCALAPPDATA%\Microsoft\WindowsApps\python.exe` and `python3.exe`) on `PATH` that fail or launch Microsoft Store when invoked.

- All `package.json` scripts invoke `node scripts/run-python.mjs ...`, which automatically skips Microsoft Store stubs and checks, in order: `ARGON_PYTHON`, active `VIRTUAL_ENV`/`CONDA_PREFIX`, `py -3`, real `python3`/`python` on `PATH`, MozillaBuild Python (`C:\mozilla-build\python3`), and standard Windows Python installations.
- If Python lives in a custom location, set `ARGON_PYTHON`:

```powershell
$env:ARGON_PYTHON = "C:\Path\To\python.exe"
```

## Clean-machine Windows setup and build commands

Run from PowerShell in the repository root (note: the Argon checkout itself may contain spaces or Unicode characters, while the **build workspace** must be a short ASCII path without spaces such as `C:\argon-build` or `D:\argon-build` to satisfy `mach` and avoid Windows `MAX_PATH` errors):

```powershell
# 1. Choose a short, ASCII, space-free build workspace on a drive with >= 60 GB free
$env:ARGON_WORKSPACE = "C:\argon-build"

# 2. Run fast unit and contract checks (no Gecko checkout needed)
npm test
node scripts/adblock-policy.mjs verify --evidence docs/parity-evidence.json
npm run test:python
npm run test:dev
node scripts/run-python.mjs -m unittest discover -s tests/windows -v

# 3. Inspect host toolchain, RAM, disk space, and Windows SDK/MSVC/MozillaBuild status
npm run doctor

# 4. Download the pinned Firefox archive, verify its SHA-256, and extract into $ARGON_WORKSPACE\source
npm run fetch

# 5. Apply Argon's overlay, branding, and browser/moz.configure identity hooks (all-or-nothing)
npm run apply

# 6. Bootstrap clang-cl, lld-link, rustc, cbindgen, node, and (if needed) VS SDK into ~/.mozbuild
npm run bootstrap

# 7. Compile Argon Browser (produces $ARGON_WORKSPACE\obj\dist\bin\argon.exe)
node scripts/run-python.mjs scripts/argon.py --jobs 4 build

# 8. Stage settings/ (librewolf.cfg, local-settings.js, policies.json) or launch with an isolated dev profile
npm run stage:runtime
npm start
```

> [!NOTE]
> `scripts/argon.py` selects `config/mozconfig.windows` automatically on Windows, translates `MOZ_OBJDIR` to forward slashes (`C:/argon-build/obj`) for MozillaBuild's `sh`, and passes `--with-redist=<dir>` dynamically only when a valid MSVC CRT redistributable directory (`vcruntime140.dll`) is detected.

## Packaging Windows distributions

Do **not** package `--artifact` builds (`firefox.exe`): both `scripts/argon.py package` and `scripts/win_package.py` fail closed if `argon.exe` or Argon's `application.ini` identity is missing.

```powershell
# 1. Run Mozilla packager (`./mach package` -> zip / dist layout)
npm run package

# 2. Build self-contained portable ZIP + SHA256SUMS
#    Default output: $ARGON_WORKSPACE\obj\dist\argon-<ver>.en-US.win64-portable.zip
npm run win:portable

# 3. Build NSIS installer + SHA256SUMS (requires makensis.exe)
#    Default output: $ARGON_WORKSPACE\obj\dist\argon-<ver>.en-US.win64.installer.exe
npm run win:installer

# 4. Verify SHA256SUMS, layout, policies, omni.ja entries, and portable profile isolation
node scripts/run-python.mjs scripts/argon.py verify-windows --require-full-omni --run-portable-smoke
```

You can also invoke `scripts/win_package.py` directly on an already-staged runtime directory:

```powershell
node scripts/run-python.mjs scripts/win_package.py portable --runtime C:\path\to\staged-runtime --output dist\argon-portable.zip
node scripts/run-python.mjs scripts/win_package.py installer --runtime C:\path\to\staged-runtime --output dist\argon-installer.exe
node scripts/run-python.mjs scripts/win_package.py verify --portable dist\argon-portable.zip --installer dist\argon-installer.exe --require-full-omni --run-portable-smoke
```

`--launcher-backend` for `win-portable` accepts `auto` (default: tries MSVC `cl`/`clang-cl`, then MinGW `x86_64-w64-mingw32-gcc`, then built-in .NET `csc.exe`), `native-c` (`packaging/windows/portable/argon-portable.c`), or `dotnet` (`packaging/windows/portable/ArgonPortable.cs`).

## Optional native adblock DLL vs built-in JS uBlock engine

By default, Argon uses the vendored JavaScript uBlock Origin core (`argon/adblock/ublock-engine.sys.mjs` + `OffMainThreadCompiler`) and `engine.wasm`. Rebuilding `argon/adblock/argon_adblock.dll` is **optional** and Cargo is **not** required for browser builds or packaging:

```powershell
# Optional: rebuild argon/adblock/argon_adblock.dll and engine.wasm (requires Rust + wasm32-unknown-unknown)
rustup target add wasm32-unknown-unknown
npm run build:adblock
```

When `argon_adblock.dll` is present in the runtime, `argon/adblock/native.sys.mjs` can load it via `ctypes` (without deleting locked DLL handles before `close()`). When absent, Argon falls back cleanly to the built-in JS uBlock engine. `win_package.py` preserves `argon_adblock.dll` if present and reports `adblock_dll_present` in JSON verification output.

## Profile paths and browser isolation

Because `browser/moz.configure` implies `MOZ_APP_VENDOR="Argon"`, `MOZ_APP_PROFILE="argon"`, and `MOZ_APP_UA_NAME="Firefox"` (while keeping `MOZ_APP_ID` compatible with AMO WebExtensions):

| Mode | Roaming profile / data | Local cache | Notes |
| --- | --- | --- | --- |
| **Normal installed Argon** | `%APPDATA%\argon\` | `%LOCALAPPDATA%\argon\` | Completely separate from `%APPDATA%\Mozilla\Firefox` and LibreWolf |
| **Portable Argon (`argon-portable.exe`)** | `<portable_dir>\data\profile\` and `data\app-data\` | `<portable_dir>\data\local-app-data\` | Pins `--profile`, `MOZ_APP_DATA`, `MOZ_LOCAL_APP_DATA`, `HOME`, and `MOZ_APP_REMOTINGNAME=argon-portable-<8hex>` |
| **Dev loop (`npm run dev`)** | `.cache\argon-dev\profile\` | Under `.cache\argon-dev\` | Uses `--no-remote --new-instance --profile`; isolated per checkout |

### AutoConfig override paths (`argon.overrides.cfg`)

`patches/profile-directory.patch` checks, in order on Windows:
1. `%APPDATA%\argon\argon.overrides.cfg` (or `<portable_dir>\data\app-data\argon.overrides.cfg` when launched via `argon-portable.exe`)
2. `%APPDATA%\Argon\argon.overrides.cfg`
3. `%USERPROFILE%\.argon\argon.overrides.cfg`

## Portable vs installer: upgrade and uninstall behavior

### Portable ZIP (`argon-<ver>.en-US.win64-portable.zip`)

- **Entrypoint**: Always launch **`argon-portable.exe`** in the extracted root (never `app\argon.exe` directly).
- **Isolation**: `argon-portable.exe` strips external `--profile`, `-p`, `--createprofile`, `--ProfileManager`, `--migration`, and `--reset-profile` arguments, forces `--profile .\data\profile` + `MOZ_APP_DATA` / `MOZ_LOCAL_APP_DATA` / `HOME`, and **fails closed** with an error if `data\` is not writable rather than falling back to `%APPDATA%\argon`.
- **Upgrade**: Extract the new portable ZIP's `app\`, `argon-portable.exe`, `README-PORTABLE.txt`, and `LICENSE` over your portable folder while keeping `data\` intact.
- **Uninstall**: Delete the extracted portable folder. Portable mode creates no Start Menu shortcuts, no `Uninstall` registry entries, and no host `%APPDATA%\argon` files.

### NSIS Installer (`argon-<ver>.en-US.win64.installer.exe`)

- **Install scope**: Defaults to per-user (`%LOCALAPPDATA%\Programs\Argon`, `HKCU`) for standard users or per-machine (`C:\Program Files\Argon`, `HKLM`) when run elevated, with `SetRegView 64`. Supports `/NOSTARTMENU`, `/NODESKTOP`, `/D=<dir>`, and `/S` (silent).
- **Default browser behavior**: Registers `Software\RegisteredApplications` and `Capabilities` (`ArgonHTML`, `ArgonPDF`, `ArgonURL`) so users can opt into Argon via Windows *Settings -> Default apps*. It **never** modifies `UserChoice` or hijacks HTTP/HTTPS/HTML associations, and omits Mozilla Maintenance Service, Default Browser Agent, telemetry, and uninstall surveys.
- **Upgrade**: Detects a running `argon.exe` via file-lock check before overwriting, preserves existing install location, and leaves `%APPDATA%\argon` and `%LOCALAPPDATA%\argon` untouched.
- **Uninstall**: Runs `uninstall\helper.exe` (via *Installed apps* / *Add or remove programs*). Removes only files packaged by Argon, installer-created shortcuts, and Argon registry keys — **user profiles in `%APPDATA%\argon` and `%LOCALAPPDATA%\argon` are preserved**.

## Unsigned dev builds vs signed releases

- **Unsigned local and CI builds**: Built with `--disable-updater` and no Authenticode signing key in the repository. Windows Defender SmartScreen may show *"Windows protected your PC"* when running an unsigned `argon.exe`, `argon-portable.exe`, or `argon-*.installer.exe`. Always verify `SHA256SUMS`:

```powershell
Get-FileHash .\argon-0.1.3.en-US.win64.installer.exe -Algorithm SHA256
```

- **Signed releases (external release infrastructure)**: When release Authenticode signing is configured, sign `argon.exe`, bundled executables/DLLs, `argon-portable.exe`, `uninstall\helper.exe`, and `argon-*.installer.exe` before publishing `SHA256SUMS`. Because `--disable-updater` is compiled in (no Argon update server exists), upgrades happen by running a newer installer or replacing the portable `app\` folder.

## Browser smoke tests and Windows CI workflows

### Running Marionette browser tests on Windows

```powershell
node scripts/run-python.mjs -m venv .cache\browser-venv
.cache\browser-venv\Scripts\python.exe -m pip install -r tests\browser\requirements.txt
$env:ARGON_TEST_BINARY = "C:\argon-build\obj\dist\argon-runtime\argon.exe"
npm run test:browser
```

### GitHub Actions split and runner constraints

- **Fast PR / push CI (`.github/workflows/ci.yml` -> `unit-windows`)**: Runs on `windows-latest` in a few minutes and executes `npm test`, `scripts/adblock-policy.mjs verify`, `npm run test:python` (`tests/build` + `tests/branding`), `npm run test:dev`, `tests/windows` (including real `csc.exe` compilation and profile-isolation smoke execution of `ArgonPortable.cs`), `tests/adblock/test_native.py`, `tests/adblock/test_snapshot.py`, and `npm run doctor`.
- **Full Windows Gecko build (`.github/workflows/build-windows.yml`)**: Separated from PR CI onto `workflow_dispatch` and `v*` tags. Standard GitHub-hosted `windows-latest` runners only expose ~14–35 GB free disk after toolcache cleanup and enforce a 360-minute timeout, which is tight for a cold Windows Gecko compilation (~55–65 GB disk and 90–240+ minutes). For reliable full Windows release builds, dispatch `build-windows.yml` targeting a larger or self-hosted Windows `x64` runner with >= 60 GB free SSD space and >= 16 GB RAM.
