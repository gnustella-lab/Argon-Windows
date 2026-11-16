// SPDX-License-Identifier: MPL-2.0
// Windows portable launcher fallback compiled with .NET Framework csc.exe
// when a native C/C++ compiler is unavailable on a packaging host.
// Uses C# 5 compatible syntax for Windows 10/11 built-in csc.exe.

using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.IO;
using System.Reflection;
using System.Runtime.InteropServices;
using System.Text;
using System.Threading;

namespace ArgonPortableLauncher
{
    internal static class Program
    {
        [DllImport("user32.dll", CharSet = CharSet.Unicode)]
        private static extern int MessageBoxW(IntPtr hWnd, string text, string caption, uint type);

        private static bool HasEnvNonEmpty(string name)
        {
            string val = Environment.GetEnvironmentVariable(name);
            return !string.IsNullOrEmpty(val);
        }

        private static void FailError(string msg)
        {
            try
            {
                Console.Error.WriteLine(msg);
            }
            catch
            {
            }

            bool hasConsole = false;
            try
            {
                hasConsole = Console.IsErrorRedirected || Console.IsOutputRedirected;
            }
            catch
            {
            }

            if (!hasConsole && !HasEnvNonEmpty("ARGON_PORTABLE_NO_GUI") &&
                !HasEnvNonEmpty("MOZ_AUTOMATION") && !HasEnvNonEmpty("CI"))
            {
                MessageBoxW(IntPtr.Zero, msg, "Argon Browser (Portable)", 0x10);
            }
        }

        private static uint Fnv1aLowerPath(string path)
        {
            string normalized = path.TrimEnd('\\', '/').Replace('/', '\\').ToLowerInvariant();
            uint hash = 2166136261u;
            for (int i = 0; i < normalized.Length; i++)
            {
                hash ^= (uint)normalized[i];
                hash *= 16777619u;
            }
            return hash;
        }

        private static string StripFlagPrefix(string arg)
        {
            if (arg.StartsWith("--", StringComparison.Ordinal))
            {
                return arg.Substring(2);
            }
            if (arg.StartsWith("-", StringComparison.Ordinal) || arg.StartsWith("/", StringComparison.Ordinal))
            {
                return arg.Substring(1);
            }
            return null;
        }

        private static int MatchFlagName(string stripped, string name, bool allowEquals)
        {
            if (string.Equals(stripped, name, StringComparison.OrdinalIgnoreCase))
            {
                return 1;
            }
            if (allowEquals && stripped.Length > name.Length &&
                stripped.StartsWith(name + "=", StringComparison.OrdinalIgnoreCase))
            {
                return 2;
            }
            return 0;
        }

        private static bool IsInfoOnly(string[] args)
        {
            for (int i = 0; i < args.Length; i++)
            {
                string s = StripFlagPrefix(args[i]);
                if (s == null)
                {
                    continue;
                }
                if (MatchFlagName(s, "v", false) == 1 ||
                    MatchFlagName(s, "version", false) == 1 ||
                    MatchFlagName(s, "full-version", false) == 1 ||
                    MatchFlagName(s, "h", false) == 1 ||
                    MatchFlagName(s, "help", false) == 1 ||
                    MatchFlagName(s, "?", false) == 1)
                {
                    return true;
                }
            }
            return false;
        }

        private static string QuoteWin32Arg(string arg)
        {
            if (arg.Length == 0)
            {
                return "\"\"";
            }
            bool needsQuotes = false;
            for (int i = 0; i < arg.Length; i++)
            {
                char c = arg[i];
                if (c == ' ' || c == '\t' || c == '"')
                {
                    needsQuotes = true;
                    break;
                }
            }
            if (!needsQuotes)
            {
                return arg;
            }
            StringBuilder sb = new StringBuilder();
            sb.Append('"');
            int backslashes = 0;
            for (int i = 0; i < arg.Length; i++)
            {
                char c = arg[i];
                if (c == '\\')
                {
                    backslashes++;
                }
                else if (c == '"')
                {
                    sb.Append('\\', backslashes * 2 + 1);
                    sb.Append('"');
                    backslashes = 0;
                }
                else
                {
                    if (backslashes > 0)
                    {
                        sb.Append('\\', backslashes);
                        backslashes = 0;
                    }
                    sb.Append(c);
                }
            }
            if (backslashes > 0)
            {
                sb.Append('\\', backslashes * 2);
            }
            sb.Append('"');
            return sb.ToString();
        }

        [STAThread]
        private static int Main(string[] args)
        {
            string exePath = Assembly.GetExecutingAssembly().Location;
            string portableDir = Path.GetDirectoryName(exePath);
            if (string.IsNullOrEmpty(portableDir))
            {
                FailError("Unable to locate the portable Argon Browser directory.");
                return 2;
            }

            string appExe = Path.Combine(portableDir, "app", "argon.exe");
            if (!File.Exists(appExe))
            {
                FailError("Missing app\\argon.exe inside the portable Argon directory.");
                return 2;
            }

            string dataDir = Path.Combine(portableDir, "data");
            string profileDir = Path.Combine(dataDir, "profile");
            string appDataDir = Path.Combine(dataDir, "app-data");
            string localAppDataDir = Path.Combine(dataDir, "local-app-data");
            string homeDir = Path.Combine(dataDir, "home");

            try
            {
                Directory.CreateDirectory(dataDir);
                Directory.CreateDirectory(profileDir);
                Directory.CreateDirectory(appDataDir);
                Directory.CreateDirectory(localAppDataDir);
                Directory.CreateDirectory(homeDir);

                string probe = Path.Combine(profileDir, string.Format(".argon-portable-check-{0}.tmp", Process.GetCurrentProcess().Id));
                using (FileStream fs = new FileStream(probe, FileMode.Create, FileAccess.Write, FileShare.None))
                {
                    fs.WriteByte(0);
                }
                File.Delete(probe);
            }
            catch
            {
                FailError("Portable data directory is not writable. Argon Portable refuses to fall back to the installed user profile.");
                return 2;
            }

            string remotingName = string.Format("argon-portable-{0:x8}", Fnv1aLowerPath(portableDir));
            List<string> forward = new List<string>();
            if (!IsInfoOnly(args))
            {
                forward.Add("--profile");
                forward.Add(profileDir);
            }

            for (int i = 0; i < args.Length; i++)
            {
                string s = StripFlagPrefix(args[i]);
                if (s != null)
                {
                    int mProf = MatchFlagName(s, "profile", true);
                    int mP = MatchFlagName(s, "p", true);
                    int mCreate = MatchFlagName(s, "createprofile", true);
                    if (mProf == 1 || mP == 1 || mCreate == 1)
                    {
                        if (i + 1 < args.Length)
                        {
                            i++;
                        }
                        continue;
                    }
                    if (mProf == 2 || mP == 2 || mCreate == 2 ||
                        MatchFlagName(s, "profilemanager", false) == 1 ||
                        MatchFlagName(s, "migration", false) == 1 ||
                        MatchFlagName(s, "reset-profile", false) == 1)
                    {
                        continue;
                    }
                }
                forward.Add(args[i]);
            }

            StringBuilder cmdArgs = new StringBuilder();
            for (int i = 0; i < forward.Count; i++)
            {
                if (i > 0)
                {
                    cmdArgs.Append(' ');
                }
                cmdArgs.Append(QuoteWin32Arg(forward[i]));
            }

            bool redirOut = false;
            bool redirErr = false;
            try
            {
                redirOut = Console.IsOutputRedirected;
                redirErr = Console.IsErrorRedirected;
            }
            catch
            {
            }

            ProcessStartInfo psi = new ProcessStartInfo();
            psi.FileName = appExe;
            psi.Arguments = cmdArgs.ToString();
            psi.UseShellExecute = false;
            psi.RedirectStandardOutput = redirOut;
            psi.RedirectStandardError = redirErr;
            psi.CreateNoWindow = redirOut || redirErr;
            psi.EnvironmentVariables["MOZ_APP_DATA"] = appDataDir;
            psi.EnvironmentVariables["MOZ_LOCAL_APP_DATA"] = localAppDataDir;
            psi.EnvironmentVariables["MOZ_APP_REMOTINGNAME"] = remotingName;
            psi.EnvironmentVariables["MOZ_LEGACY_PROFILES"] = "1";
            psi.EnvironmentVariables["MOZ_CRASHREPORTER_DISABLE"] = "1";
            psi.EnvironmentVariables["HOME"] = homeDir;
            psi.EnvironmentVariables["ARGON_PORTABLE"] = "1";
            psi.EnvironmentVariables["ARGON_PORTABLE_DIR"] = portableDir;
            psi.EnvironmentVariables["ARGON_PORTABLE_PROFILE"] = profileDir;

            try
            {
                using (Process proc = Process.Start(psi))
                {
                    if (proc == null)
                    {
                        FailError("Failed to start app\\argon.exe.");
                        return 1;
                    }
                    Thread outThread = null;
                    Thread errThread = null;
                    if (redirOut)
                    {
                        outThread = new Thread(delegate()
                        {
                            try
                            {
                                proc.StandardOutput.BaseStream.CopyTo(Console.OpenStandardOutput());
                                Console.Out.Flush();
                            }
                            catch
                            {
                            }
                        });
                        outThread.Start();
                    }
                    if (redirErr)
                    {
                        errThread = new Thread(delegate()
                        {
                            try
                            {
                                proc.StandardError.BaseStream.CopyTo(Console.OpenStandardError());
                                Console.Error.Flush();
                            }
                            catch
                            {
                            }
                        });
                        errThread.Start();
                    }
                    proc.WaitForExit();
                    if (outThread != null) outThread.Join();
                    if (errThread != null) errThread.Join();
                    return proc.ExitCode;
                }
            }
            catch
            {
                FailError("Failed to start app\\argon.exe.");
                return 1;
            }
        }
    }
}
