/* SPDX-License-Identifier: MPL-2.0 */
/*
 * Native Windows portable launcher for Argon Browser.
 *
 * Isolates all Argon profile data, caches, and remoting registration inside the
 * portable package root (`<portable_dir>\data\`) so launching portable Argon
 * never reads or writes %APPDATA%\argon, %LOCALAPPDATA%\argon, Firefox, or
 * LibreWolf profiles.
 */

#define UNICODE
#define _UNICODE
#define WIN32_LEAN_AND_MEAN

#include <windows.h>
#include <shellapi.h>
#include <stddef.h>
#include <stdint.h>
#include <stdlib.h>
#include <wchar.h>

static int has_env_nonempty(const wchar_t* name) {
  DWORD len = GetEnvironmentVariableW(name, NULL, 0);
  return len > 1;
}

static void write_stderr_utf8(const wchar_t* msg) {
  HANDLE err = GetStdHandle(STD_ERROR_HANDLE);
  if (err == NULL || err == INVALID_HANDLE_VALUE) {
    return;
  }
  int utf8_len = WideCharToMultiByte(CP_UTF8, 0, msg, -1, NULL, 0, NULL, NULL);
  if (utf8_len <= 1) {
    return;
  }
  char* buf = (char*)HeapAlloc(GetProcessHeap(), 0, (SIZE_T)utf8_len + 2);
  if (!buf) {
    return;
  }
  WideCharToMultiByte(CP_UTF8, 0, msg, -1, buf, utf8_len, NULL, NULL);
  buf[utf8_len - 1] = '\n';
  buf[utf8_len] = '\0';
  DWORD written = 0;
  WriteFile(err, buf, (DWORD)utf8_len, &written, NULL);
  HeapFree(GetProcessHeap(), 0, buf);
}

static void fail_error(const wchar_t* msg) {
  write_stderr_utf8(msg);
  HANDLE err = GetStdHandle(STD_ERROR_HANDLE);
  int attached_io = (err != NULL && err != INVALID_HANDLE_VALUE && GetFileType(err) != FILE_TYPE_UNKNOWN);
  if (!attached_io && !has_env_nonempty(L"ARGON_PORTABLE_NO_GUI") &&
      !has_env_nonempty(L"MOZ_AUTOMATION") && !has_env_nonempty(L"CI")) {
    MessageBoxW(NULL, msg, L"Argon Browser (Portable)", MB_OK | MB_ICONERROR);
  }
}

static wchar_t* dup_module_dir(void) {
  DWORD cap = 1024;
  wchar_t* buf = NULL;
  for (;;) {
    buf = (wchar_t*)HeapAlloc(GetProcessHeap(), 0, (SIZE_T)cap * sizeof(wchar_t));
    if (!buf) {
      return NULL;
    }
    DWORD len = GetModuleFileNameW(NULL, buf, cap);
    if (len == 0) {
      HeapFree(GetProcessHeap(), 0, buf);
      return NULL;
    }
    if (len < cap - 1) {
      break;
    }
    HeapFree(GetProcessHeap(), 0, buf);
    if (cap >= 32768) {
      return NULL;
    }
    cap *= 2;
  }
  wchar_t* last_slash = wcsrchr(buf, L'\\');
  wchar_t* last_fwd = wcsrchr(buf, L'/');
  wchar_t* cut = last_slash;
  if (last_fwd && (!cut || last_fwd > cut)) {
    cut = last_fwd;
  }
  if (!cut) {
    HeapFree(GetProcessHeap(), 0, buf);
    return NULL;
  }
  *cut = L'\0';
  return buf;
}

static wchar_t* join_path(const wchar_t* base, const wchar_t* suffix) {
  size_t blen = wcslen(base);
  size_t slen = wcslen(suffix);
  int need_sep = (blen > 0 && base[blen - 1] != L'\\' && base[blen - 1] != L'/') ? 1 : 0;
  wchar_t* out = (wchar_t*)HeapAlloc(GetProcessHeap(), 0, (blen + need_sep + slen + 1) * sizeof(wchar_t));
  if (!out) {
    return NULL;
  }
  wmemcpy(out, base, blen);
  if (need_sep) {
    out[blen] = L'\\';
  }
  wmemcpy(out + blen + need_sep, suffix, slen);
  out[blen + need_sep + slen] = L'\0';
  return out;
}

static int ensure_dir_recursive(const wchar_t* path) {
  size_t len = wcslen(path);
  if (len == 0) {
    return 0;
  }
  wchar_t* tmp = (wchar_t*)HeapAlloc(GetProcessHeap(), 0, (len + 1) * sizeof(wchar_t));
  if (!tmp) {
    return 0;
  }
  wmemcpy(tmp, path, len + 1);

  for (size_t i = 0; i < len; ++i) {
    if (tmp[i] == L'/') {
      tmp[i] = L'\\';
    }
  }

  size_t start = 0;
  if (len >= 2 && tmp[1] == L':') {
    start = 3;
  } else if (len >= 2 && tmp[0] == L'\\' && tmp[1] == L'\\') {
    start = 2;
    while (start < len && tmp[start] != L'\\') start++;
    if (start < len) start++;
    while (start < len && tmp[start] != L'\\') start++;
    if (start < len) start++;
  }

  for (size_t i = start; i <= len; ++i) {
    if (tmp[i] == L'\\' || tmp[i] == L'\0') {
      wchar_t saved = tmp[i];
      tmp[i] = L'\0';
      if (wcslen(tmp) > 0) {
        DWORD attrs = GetFileAttributesW(tmp);
        if (attrs == INVALID_FILE_ATTRIBUTES) {
          if (!CreateDirectoryW(tmp, NULL)) {
            DWORD err = GetLastError();
            if (err != ERROR_ALREADY_EXISTS) {
              HeapFree(GetProcessHeap(), 0, tmp);
              return 0;
            }
          }
        } else if ((attrs & FILE_ATTRIBUTE_DIRECTORY) == 0) {
          HeapFree(GetProcessHeap(), 0, tmp);
          return 0;
        }
      }
      tmp[i] = saved;
    }
  }
  HeapFree(GetProcessHeap(), 0, tmp);
  DWORD final_attrs = GetFileAttributesW(path);
  return final_attrs != INVALID_FILE_ATTRIBUTES && (final_attrs & FILE_ATTRIBUTE_DIRECTORY) != 0;
}

static int check_dir_writable(const wchar_t* dir) {
  wchar_t probe_name[64];
  _snwprintf_s(probe_name, 64, _TRUNCATE, L".argon-portable-check-%lu.tmp", (unsigned long)GetCurrentProcessId());
  wchar_t* probe_path = join_path(dir, probe_name);
  if (!probe_path) {
    return 0;
  }
  HANDLE fh = CreateFileW(
      probe_path,
      GENERIC_WRITE,
      0,
      NULL,
      CREATE_ALWAYS,
      FILE_ATTRIBUTE_TEMPORARY | FILE_FLAG_DELETE_ON_CLOSE,
      NULL);
  if (fh == INVALID_HANDLE_VALUE) {
    HeapFree(GetProcessHeap(), 0, probe_path);
    return 0;
  }
  CloseHandle(fh);
  DeleteFileW(probe_path);
  HeapFree(GetProcessHeap(), 0, probe_path);
  return 1;
}

static uint32_t fnv1a_lower_path(const wchar_t* path) {
  uint32_t hash = 2166136261u;
  size_t len = wcslen(path);
  while (len > 0 && (path[len - 1] == L'\\' || path[len - 1] == L'/')) {
    len--;
  }
  for (size_t i = 0; i < len; ++i) {
    wchar_t c = path[i];
    if (c == L'/') {
      c = L'\\';
    }
    if (c >= L'A' && c <= L'Z') {
      c = (wchar_t)(c + (L'a' - L'A'));
    }
    hash ^= (uint16_t)c;
    hash *= 16777619u;
  }
  return hash;
}

static const wchar_t* strip_flag_prefix(const wchar_t* arg) {
  if (arg[0] == L'-' && arg[1] == L'-') {
    return arg + 2;
  }
  if (arg[0] == L'-' || arg[0] == L'/') {
    return arg + 1;
  }
  return NULL;
}

static int eq_flag_name(const wchar_t* stripped, const wchar_t* name, int allow_equals) {
  size_t nlen = wcslen(name);
  if (_wcsnicmp(stripped, name, nlen) != 0) {
    return 0;
  }
  if (stripped[nlen] == L'\0') {
    return 1;
  }
  if (allow_equals && stripped[nlen] == L'=') {
    return 2;
  }
  return 0;
}

static int is_info_only_argv(int argc, wchar_t** argv) {
  for (int i = 1; i < argc; ++i) {
    const wchar_t* s = strip_flag_prefix(argv[i]);
    if (!s) continue;
    if (eq_flag_name(s, L"v", 0) || eq_flag_name(s, L"version", 0) ||
        eq_flag_name(s, L"full-version", 0) || eq_flag_name(s, L"h", 0) ||
        eq_flag_name(s, L"help", 0) || eq_flag_name(s, L"?", 0)) {
      return 1;
    }
  }
  return 0;
}

typedef struct {
  wchar_t* data;
  size_t len;
  size_t cap;
} WStrBuf;

static int buf_append_char(WStrBuf* b, wchar_t c) {
  if (b->len + 1 >= b->cap) {
    size_t ncap = b->cap ? b->cap * 2 : 512;
    wchar_t* ndata = (wchar_t*)HeapReAlloc(GetProcessHeap(), 0, b->data, ncap * sizeof(wchar_t));
    if (!ndata) return 0;
    b->data = ndata;
    b->cap = ncap;
  }
  b->data[b->len++] = c;
  b->data[b->len] = L'\0';
  return 1;
}

static int buf_append_quoted_arg(WStrBuf* b, const wchar_t* arg) {
  if (b->len > 0 && !buf_append_char(b, L' ')) return 0;
  int need_quotes = (arg[0] == L'\0');
  for (const wchar_t* p = arg; *p; ++p) {
    if (*p == L' ' || *p == L'\t' || *p == L'"') {
      need_quotes = 1;
      break;
    }
  }
  if (!need_quotes) {
    for (const wchar_t* p = arg; *p; ++p) {
      if (!buf_append_char(b, *p)) return 0;
    }
    return 1;
  }
  if (!buf_append_char(b, L'"')) return 0;
  size_t backslashes = 0;
  for (const wchar_t* p = arg; *p; ++p) {
    if (*p == L'\\') {
      backslashes++;
    } else if (*p == L'"') {
      for (size_t k = 0; k < backslashes * 2 + 1; ++k) {
        if (!buf_append_char(b, L'\\')) return 0;
      }
      if (!buf_append_char(b, L'"')) return 0;
      backslashes = 0;
    } else {
      for (size_t k = 0; k < backslashes; ++k) {
        if (!buf_append_char(b, L'\\')) return 0;
      }
      backslashes = 0;
      if (!buf_append_char(b, *p)) return 0;
    }
  }
  for (size_t k = 0; k < backslashes * 2; ++k) {
    if (!buf_append_char(b, L'\\')) return 0;
  }
  return buf_append_char(b, L'"');
}

int WINAPI WinMain(HINSTANCE hInstance, HINSTANCE hPrevInstance, LPSTR lpCmdLine, int nCmdShow) {
  (void)hInstance;
  (void)hPrevInstance;
  (void)lpCmdLine;
  (void)nCmdShow;

  wchar_t* portable_dir = dup_module_dir();
  if (!portable_dir) {
    fail_error(L"Unable to locate the portable Argon Browser directory.");
    return 2;
  }

  wchar_t* app_exe = join_path(portable_dir, L"app\\argon.exe");
  wchar_t* data_dir = join_path(portable_dir, L"data");
  wchar_t* profile_dir = join_path(portable_dir, L"data\\profile");
  wchar_t* app_data_dir = join_path(portable_dir, L"data\\app-data");
  wchar_t* local_app_data_dir = join_path(portable_dir, L"data\\local-app-data");
  wchar_t* home_dir = join_path(portable_dir, L"data\\home");

  if (!app_exe || !data_dir || !profile_dir || !app_data_dir || !local_app_data_dir || !home_dir) {
    fail_error(L"Out of memory while preparing portable Argon Browser launch.");
    return 2;
  }

  DWORD exe_attrs = GetFileAttributesW(app_exe);
  if (exe_attrs == INVALID_FILE_ATTRIBUTES || (exe_attrs & FILE_ATTRIBUTE_DIRECTORY) != 0) {
    fail_error(L"Missing app\\argon.exe inside the portable Argon directory.");
    return 2;
  }

  if (!ensure_dir_recursive(data_dir) || !ensure_dir_recursive(profile_dir) ||
      !ensure_dir_recursive(app_data_dir) || !ensure_dir_recursive(local_app_data_dir) ||
      !ensure_dir_recursive(home_dir) || !check_dir_writable(profile_dir)) {
    fail_error(
        L"Portable data directory is not writable. Argon Portable refuses to fall back to the installed user profile.");
    return 2;
  }

  uint32_t path_hash = fnv1a_lower_path(portable_dir);
  wchar_t remoting_name[48];
  _snwprintf_s(remoting_name, 48, _TRUNCATE, L"argon-portable-%08x", (unsigned int)path_hash);

  SetEnvironmentVariableW(L"MOZ_APP_DATA", app_data_dir);
  SetEnvironmentVariableW(L"MOZ_LOCAL_APP_DATA", local_app_data_dir);
  SetEnvironmentVariableW(L"MOZ_APP_REMOTINGNAME", remoting_name);
  SetEnvironmentVariableW(L"MOZ_LEGACY_PROFILES", L"1");
  SetEnvironmentVariableW(L"MOZ_CRASHREPORTER_DISABLE", L"1");
  SetEnvironmentVariableW(L"HOME", home_dir);
  SetEnvironmentVariableW(L"ARGON_PORTABLE", L"1");
  SetEnvironmentVariableW(L"ARGON_PORTABLE_DIR", portable_dir);
  SetEnvironmentVariableW(L"ARGON_PORTABLE_PROFILE", profile_dir);

  int argc = 0;
  wchar_t** argv = CommandLineToArgvW(GetCommandLineW(), &argc);
  if (!argv) {
    fail_error(L"Failed to parse command-line arguments.");
    return 2;
  }

  WStrBuf cmdline = {0};
  cmdline.cap = 1024;
  cmdline.data = (wchar_t*)HeapAlloc(GetProcessHeap(), 0, cmdline.cap * sizeof(wchar_t));
  if (!cmdline.data) {
    LocalFree(argv);
    return 2;
  }
  cmdline.data[0] = L'\0';

  if (!buf_append_quoted_arg(&cmdline, app_exe)) {
    LocalFree(argv);
    return 2;
  }

  if (!is_info_only_argv(argc, argv)) {
    if (!buf_append_quoted_arg(&cmdline, L"--profile") ||
        !buf_append_quoted_arg(&cmdline, profile_dir)) {
      LocalFree(argv);
      return 2;
    }
  }

  for (int i = 1; i < argc; ++i) {
    const wchar_t* s = strip_flag_prefix(argv[i]);
    if (s) {
      int m_prof = eq_flag_name(s, L"profile", 1);
      int m_p = eq_flag_name(s, L"p", 1);
      int m_create = eq_flag_name(s, L"createprofile", 1);
      if (m_prof == 1 || m_p == 1 || m_create == 1) {
        if (i + 1 < argc) {
          ++i;
        }
        continue;
      }
      if (m_prof == 2 || m_p == 2 || m_create == 2 ||
          eq_flag_name(s, L"profilemanager", 0) ||
          eq_flag_name(s, L"migration", 0) ||
          eq_flag_name(s, L"reset-profile", 0)) {
        continue;
      }
    }
    if (!buf_append_quoted_arg(&cmdline, argv[i])) {
      LocalFree(argv);
      return 2;
    }
  }
  LocalFree(argv);

  STARTUPINFOW si;
  ZeroMemory(&si, sizeof(si));
  si.cb = sizeof(si);
  PROCESS_INFORMATION pi;
  ZeroMemory(&pi, sizeof(pi));

  HANDLE hIn = GetStdHandle(STD_INPUT_HANDLE);
  HANDLE hOut = GetStdHandle(STD_OUTPUT_HANDLE);
  HANDLE hErr = GetStdHandle(STD_ERROR_HANDLE);
  BOOL inherit = FALSE;
  DWORD creation_flags = CREATE_UNICODE_ENVIRONMENT;
  if ((hIn != NULL && hIn != INVALID_HANDLE_VALUE) ||
      (hOut != NULL && hOut != INVALID_HANDLE_VALUE) ||
      (hErr != NULL && hErr != INVALID_HANDLE_VALUE)) {
    si.dwFlags |= STARTF_USESTDHANDLES;
    si.hStdInput = hIn;
    si.hStdOutput = hOut;
    si.hStdError = hErr;
    inherit = TRUE;
    if ((hOut != NULL && hOut != INVALID_HANDLE_VALUE && GetFileType(hOut) == FILE_TYPE_PIPE) ||
        (hErr != NULL && hErr != INVALID_HANDLE_VALUE && GetFileType(hErr) == FILE_TYPE_PIPE)) {
      creation_flags |= CREATE_NO_WINDOW;
    }
  }

  if (!CreateProcessW(
          app_exe,
          cmdline.data,
          NULL,
          NULL,
          inherit,
          creation_flags,
          NULL,
          NULL,
          &si,
          &pi)) {
    fail_error(L"Failed to start app\\argon.exe.");
    return 1;
  }

  CloseHandle(pi.hThread);
  WaitForSingleObject(pi.hProcess, INFINITE);
  DWORD exit_code = 0;
  if (!GetExitCodeProcess(pi.hProcess, &exit_code)) {
    exit_code = 1;
  }
  CloseHandle(pi.hProcess);
  return (int)exit_code;
}
