# Argon Settings (LibreWolf Base)

This directory contains the hardened default configuration inherited from LibreWolf and customized for **Argon Browser** on Linux (Flatpak) and Windows (NSIS installer & portable):

- `librewolf.cfg`: Hardened runtime defaults for privacy, fingerprinting resistance, networking, Linux Flatpak desktop portal integration, and Windows background-agent/updater hardening.
- `defaults/pref/local-settings.js`: Loads `librewolf.cfg` at startup.
- `distribution/policies.json`: Enterprise policy configuration disabling telemetry, studies, Firefox Accounts by default, Pocket, default browser agent, and background app updates.

User overrides can be placed in:
- Linux: `~/.argon/argon.overrides.cfg` or `~/.config/argon/argon/argon.overrides.cfg`
- Windows: `%APPDATA%\argon\argon.overrides.cfg`, `%APPDATA%\Argon\argon.overrides.cfg`, or `%USERPROFILE%\.argon\argon.overrides.cfg`

Argon keeps persistent cookies and site data when the browser closes by default.
Clearing data on shutdown is opt-in through Privacy settings; explicit user
preferences still take precedence over these defaults. Session cookies and
private windows retain their normal lifetimes.
