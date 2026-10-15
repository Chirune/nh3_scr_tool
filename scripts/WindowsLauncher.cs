using System;
using System.Diagnostics;
using System.IO;
using System.Text;
using System.Windows.Forms;

// Small Windows entry point. The bundled Python remains a real interpreter,
// so existing subprocesses (three stages, browser worker, translation) work.
class WindowsLauncher
{
    static string Quote(string value)
    {
        var result = new StringBuilder("\"");
        int slashes = 0;
        foreach (char c in value)
        {
            if (c == '\\') { slashes++; continue; }
            result.Append('\\', c == '"' ? slashes * 2 + 1 : slashes);
            result.Append(c); slashes = 0;
        }
        result.Append('\\', slashes * 2); result.Append('"');
        return result.ToString();
    }

    [STAThread]
    static int Main(string[] args)
    {
        string root = AppDomain.CurrentDomain.BaseDirectory;
        string runtime = Path.Combine(root, "runtime");
        string logFolder = Path.Combine(root, "runtime_data", "日志");
        string log = Path.Combine(logFolder, "启动.log");
        try
        {
            Directory.CreateDirectory(logFolder);
            var start = new ProcessStartInfo(Path.Combine(runtime, "python.exe"));
            start.WorkingDirectory = root;
            start.Arguments = "-B -X utf8 " + Quote(Path.Combine(root, "start_workbench.py"));
            foreach (string arg in args) start.Arguments += " " + Quote(arg);
            start.UseShellExecute = false; start.CreateNoWindow = true;
            start.RedirectStandardOutput = true; start.RedirectStandardError = true;
            start.EnvironmentVariables["PYTHONHOME"] = runtime;
            start.EnvironmentVariables["PYTHONPATH"] = "";
            start.EnvironmentVariables["PYTHONNOUSERSITE"] = "1";
            start.EnvironmentVariables["PYTHONUTF8"] = "1";
            start.EnvironmentVariables["PYTHONDONTWRITEBYTECODE"] = "1";
            start.EnvironmentVariables["TCL_LIBRARY"] = Path.Combine(runtime, "tcl", "tcl8.6");
            start.EnvironmentVariables["TK_LIBRARY"] = Path.Combine(runtime, "tcl", "tk8.6");
            object gate = new object();
            using (var output = new StreamWriter(log, false, new UTF8Encoding(false)))
            using (var process = new Process())
            {
                process.StartInfo = start;
                DataReceivedEventHandler receive = delegate(object sender, DataReceivedEventArgs e) {
                    if (e.Data != null) lock (gate) { output.WriteLine(e.Data); output.Flush(); }
                };
                process.OutputDataReceived += receive; process.ErrorDataReceived += receive;
                process.Start(); process.BeginOutputReadLine(); process.BeginErrorReadLine();
                process.WaitForExit();
                if (process.ExitCode != 0)
                    MessageBox.Show("工作台未正常启动。请查看：\n" + log,
                                    "催化文献数据工作台", MessageBoxButtons.OK, MessageBoxIcon.Error);
                return process.ExitCode;
            }
        }
        catch (Exception error)
        {
            MessageBox.Show("请完整解压后运行，不能单独复制 EXE。\n" + error.Message,
                            "催化文献数据工作台", MessageBoxButtons.OK, MessageBoxIcon.Error);
            return 1;
        }
    }
}
