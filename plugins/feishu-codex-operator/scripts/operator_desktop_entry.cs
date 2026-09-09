// Build as a Windows application; the binary belongs only to the private bundle.
using System;
using System.Diagnostics;
using System.IO;
using System.Windows.Forms;

internal static class OperatorDesktopEntry
{
    [STAThread]
    private static int Main(string[] arguments)
    {
        bool checkOnly = arguments.Length == 1 && arguments[0] == "--check-only";
        if (arguments.Length != 0 && !checkOnly) return 2;
        string bundle = AppDomain.CurrentDomain.BaseDirectory.TrimEnd(Path.DirectorySeparatorChar);
        try
        {
            DirectoryInfo privateRoot = Directory.GetParent(bundle);
            if (privateRoot == null || privateRoot.Name != ".codex" || privateRoot.Parent == null)
                throw new InvalidOperationException("The startup bundle location changed.");
            // Uninstall leaves this tiny native launcher for otherwise invisible
            // taskbar pins. It has no dependency on the removed plugin source.
            if (File.Exists(Path.Combine(bundle, "native-only")))
            {
                int nativeResult = OpenNative(checkOnly);
                if (nativeResult != 0 && !checkOnly)
                    ShowFailure("The installed Codex application could not be opened.", bundle);
                return nativeResult;
            }
            string script = Path.Combine(bundle, "operator_desktop_entry.ps1");
            if (!File.Exists(script)) script = Path.Combine(privateRoot.Parent.FullName,
                "plugins", "feishu-codex-operator", "scripts", "operator_desktop_entry.ps1");
            string powershell = Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.ProgramFiles),
                "PowerShell", "7", "pwsh.exe");
            if (!File.Exists(script) || !File.Exists(powershell))
                throw new FileNotFoundException("Codex startup files are unavailable.");
            var start = new ProcessStartInfo(powershell,
                "-NoLogo -NoProfile -NonInteractive -WindowStyle Hidden -File " + Quote(script) +
                " -StartupBundle " + Quote(bundle) + (checkOnly ? " -CheckOnly" : ""));
            start.UseShellExecute = false;
            start.CreateNoWindow = true;
            start.WindowStyle = ProcessWindowStyle.Hidden;
            start.WorkingDirectory = bundle;
            using (Process child = Process.Start(start))
            {
                child.WaitForExit();
                if (child.ExitCode != 0 && !checkOnly)
                    ShowFailure("Codex startup stopped. No automatic retry was attempted.", bundle);
                return child.ExitCode;
            }
        }
        catch (Exception)
        {
            if (!checkOnly) ShowFailure("Codex startup could not run. Return to the setup task.", bundle);
            return 1;
        }
    }

    private static string Quote(string value)
    {
        // The only values are existing Windows filesystem paths, not shell code.
        if (value.IndexOf('"') >= 0) throw new ArgumentException("Invalid path.");
        return "\"" + value + "\"";
    }

    private static int OpenNative(bool checkOnly)
    {
        string powershell = Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.System),
            "WindowsPowerShell", "v1.0", "powershell.exe");
        string code = "$ErrorActionPreference='Stop'; $p=@(Get-AppxPackage -Name OpenAI.Codex); " +
            "if($p.Count -ne 1){exit 1}; $e=Join-Path $p[0].InstallLocation 'app\\ChatGPT.exe'; " +
            "if(-not(Test-Path -LiteralPath $e -PathType Leaf)){exit 1}; " +
            (checkOnly ? "exit 0" : "Start-Process -FilePath $e -WindowStyle Normal; exit 0");
        string encoded = Convert.ToBase64String(System.Text.Encoding.Unicode.GetBytes(code));
        var start = new ProcessStartInfo(powershell, "-NoProfile -NonInteractive -EncodedCommand " + encoded);
        start.UseShellExecute = false; start.CreateNoWindow = true;
        start.WindowStyle = ProcessWindowStyle.Hidden;
        using (Process child = Process.Start(start)) { child.WaitForExit(); return child.ExitCode; }
    }

    private static void ShowFailure(string message, string bundle)
    {
        string pointer = Path.Combine(bundle, "unified-startup-last-log.txt");
        if (File.Exists(pointer)) message += Environment.NewLine + "Log location: " + pointer;
        MessageBox.Show(message, "Codex", MessageBoxButtons.OK, MessageBoxIcon.Error);
    }
}
