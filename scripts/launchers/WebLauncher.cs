using System;
using System.Diagnostics;
using System.IO;
using System.Net.Sockets;
using System.Threading;

namespace AiLibraryLaunchers
{
    class WebLauncher
    {
        private static Process _uvicornProc;

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

                string pythonExe = Path.Combine(projectRoot, @".venv\Scripts\python.exe");
                string defaultDataDir = Path.Combine(projectRoot, "data");

                if (!File.Exists(pythonExe))
                {
                    Console.Error.WriteLine("[错误] 未找到 Python 虚拟环境: " + pythonExe);
                    Console.WriteLine("按任意键退出...");
                    Console.ReadKey();
                    return 1;
                }

                int port = 8000;
                int customPort;
                if (args.Length > 0 && int.TryParse(args[0], out customPort))
                {
                    port = customPort;
                }

                Console.Title = "AI Knowledge OS - 3D 宇宙知识库";
                Console.WriteLine("=========================================================");
                Console.WriteLine("    AI Knowledge OS - 3D 宇宙知识库");
                Console.WriteLine("=========================================================");
                Console.WriteLine(" [服务地址]  http://127.0.0.1:" + port);
                Console.WriteLine(" [数据目录]  " + defaultDataDir);
                Console.WriteLine(" [操作说明]  按 Ctrl+C 可停止服务");
                Console.WriteLine("=========================================================");
                Console.WriteLine();
                Console.WriteLine("正在启动服务并打开浏览器，请稍候...");

                ProcessStartInfo psi = new ProcessStartInfo
                {
                    FileName = pythonExe,
                    Arguments = "-m uvicorn backend.adapters.api.app:app --host 127.0.0.1 --port " + port,
                    WorkingDirectory = projectRoot,
                    UseShellExecute = false,
                    CreateNoWindow = false
                };

                psi.EnvironmentVariables["PYTHONUTF8"] = "1";
                if (!psi.EnvironmentVariables.ContainsKey("AI_LIBRARY_DATA_DIR"))
                {
                    psi.EnvironmentVariables["AI_LIBRARY_DATA_DIR"] = defaultDataDir;
                }

                _uvicornProc = Process.Start(psi);

                Console.CancelKeyPress += (s, e) => CleanupProcess();
                AppDomain.CurrentDomain.ProcessExit += (s, e) => CleanupProcess();

                // Open browser in background thread once port is reachable
                string url = "http://127.0.0.1:" + port;
                Thread browserThread = new Thread(() =>
                {
                    for (int i = 0; i < 30; i++)
                    {
                        Thread.Sleep(500);
                        if (_uvicornProc.HasExited) return;
                        try
                        {
                            using (TcpClient client = new TcpClient())
                            {
                                IAsyncResult result = client.BeginConnect("127.0.0.1", port, null, null);
                                bool success = result.AsyncWaitHandle.WaitOne(300);
                                if (success && client.Connected)
                                {
                                    client.EndConnect(result);
                                    Process.Start(new ProcessStartInfo(url) { UseShellExecute = true });
                                    break;
                                }
                            }
                        }
                        catch
                        {
                            // Keep waiting
                        }
                    }
                });
                browserThread.IsBackground = true;
                browserThread.Start();

                _uvicornProc.WaitForExit();
                return _uvicornProc.ExitCode;
            }
            catch (Exception ex)
            {
                Console.Error.WriteLine("[错误] 服务启动失败: " + ex.Message);
                Console.WriteLine("按任意键退出...");
                Console.ReadKey();
                return 1;
            }
            finally
            {
                CleanupProcess();
            }
        }

        private static void CleanupProcess()
        {
            try
            {
                if (_uvicornProc != null && !_uvicornProc.HasExited)
                {
                    _uvicornProc.Kill();
                }
            }
            catch { }
        }
    }
}
