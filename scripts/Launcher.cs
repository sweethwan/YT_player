using System;
using System.Diagnostics;
using System.IO;
using System.Text;
using System.Windows.Forms;

internal static class Launcher
{
    // Encode one Windows command-line argument, including trailing backslashes.
    private static string Quote(string value)
    {
        StringBuilder result = new StringBuilder("\"");
        int slashes = 0;
        foreach (char character in value)
        {
            if (character == '\\') { slashes++; continue; }
            if (character == '"')
            {
                result.Append('\\', slashes * 2 + 1);
                result.Append('"');
            }
            else
            {
                result.Append('\\', slashes);
                result.Append(character);
            }
            slashes = 0;
        }
        result.Append('\\', slashes * 2);
        result.Append('"');
        return result.ToString();
    }

    [STAThread]
    private static int Main(string[] args)
    {
        string directory = Path.Combine(AppDomain.CurrentDomain.BaseDirectory, "dist", "YTPlayer");
        string executable = Path.Combine(directory, "YTPlayer.exe");
        try
        {
            if (!File.Exists(executable))
                throw new FileNotFoundException("dist\\YTPlayer\\YTPlayer.exe 파일이 없습니다. 배포 폴더를 복원하거나 scripts\\build.ps1을 실행하세요.");
            string[] quoted = Array.ConvertAll(args, Quote);
            ProcessStartInfo info = new ProcessStartInfo(executable, String.Join(" ", quoted));
            info.WorkingDirectory = directory;
            info.UseShellExecute = false;
            info.CreateNoWindow = true;
            using (Process process = Process.Start(info))
            {
                process.WaitForExit();
                return process.ExitCode;
            }
        }
        catch (Exception error)
        {
            MessageBox.Show(error.Message, "YT Player 실행 오류", MessageBoxButtons.OK, MessageBoxIcon.Error);
            return 1;
        }
    }
}
