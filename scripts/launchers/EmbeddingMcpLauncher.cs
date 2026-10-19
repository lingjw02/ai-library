using System;
using System.Diagnostics;
using System.IO;

namespace AiLibraryLaunchers
{
    class EmbeddingMcpLauncher
    {
        static int Main(string[] args)
        {
            try
            {
                string baseDir = AppDomain.CurrentDomain.BaseDirectory.TrimEnd('\\', '/');
                string projectRoot = baseDir;

                if (!Directory.Exists(Path.Combine(projectRoot, ".venv")))
                {
                    string parent = Path.GetDirectoryName(projectRoot);
                    if (!string.IsNullOrEmpty(parent) && Directory.Exists(Path.Combine(parent, ".venv")))
                    {
                        projectRoot = parent;
                    }
                }

                if (!Directory.Exists(Path.Combine(projectRoot, ".venv")))
                {
                    projectRoot = @"D:\02_Development_Tools\AI library";
                }

                string pythonExe = Path.Combine(projectRoot, @".agent-tmp\embedding-runtime\Scripts\python.exe");
                string scriptPath = Path.Combine(projectRoot, @"scripts\run_embedding_mcp.py");

                if (!File.Exists(pythonExe))
                {
                    Console.Error.WriteLine("[Error] Embedding runtime python not found: " + pythonExe);
                    return 1;
                }
                if (!File.Exists(scriptPath))
                {
                    Console.Error.WriteLine("[Error] Embedding MCP script not found: " + scriptPath);
                    return 1;
                }

                string arguments = "\"" + scriptPath + "\"";
                if (args != null && args.Length > 0)
                {
                    foreach (string arg in args)
                    {
                        if (arg.Contains(" ") || arg.Contains("\""))
                        {
                            arguments += " \"" + arg.Replace("\"", "\\\"") + "\"";
                        }
                        else
                        {
                            arguments += " " + arg;
                        }
                    }
                }

                ProcessStartInfo psi = new ProcessStartInfo
                {
                    FileName = pythonExe,
                    Arguments = arguments,
                    WorkingDirectory = projectRoot,
                    UseShellExecute = false,
                    CreateNoWindow = false
                };

                psi.EnvironmentVariables["PYTHONUTF8"] = "1";

                using (Process proc = Process.Start(psi))
                {
                    proc.WaitForExit();
                    return proc.ExitCode;
                }
            }
            catch (Exception ex)
            {
                Console.Error.WriteLine("[Error] Failed to start Embedding MCP process: " + ex.Message);
                return 1;
            }
        }
    }
}
