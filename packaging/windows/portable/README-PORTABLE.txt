Argon Browser for Windows (Portable)
=====================================

Launch Argon Browser using:
  argon-portable.exe

Portable profile isolation:
- All profile data, preferences, bookmarks, history, extensions, and caches
  are stored inside the `data\` directory next to `argon-portable.exe`.
- `argon-portable.exe` pins `--profile .\data\profile`, sets `MOZ_APP_DATA`,
  `MOZ_LOCAL_APP_DATA`, `HOME`, and a portable-scoped `MOZ_APP_REMOTINGNAME`,
  and refuses to fall back to `%APPDATA%\argon` if `data\` is not writable.
- Normal installed Argon (`%APPDATA%\argon`), Firefox, and LibreWolf profiles
  are never read or modified by `argon-portable.exe`.
- Do not launch `app\argon.exe` directly; `app\` contains the browser runtime
  used by `argon-portable.exe`.
