# Build native launcher executables for AI Knowledge OS
$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
$csc = "C:\Windows\Microsoft.NET\Framework64\v4.0.30319\csc.exe"

if (-not (Test-Path $csc)) {
    throw "Microsoft .NET C# compiler not found at: $csc"
}

Write-Host "Building launchers from $PSScriptRoot..."

# 1. MCP Memory Launcher
$outMemory = Join-Path $projectRoot "ai-memory-mcp.exe"
$srcMemory = Join-Path $PSScriptRoot "launchers\MemoryMcpLauncher.cs"
Write-Host "Compiling $outMemory..."
& $csc /nologo /optimize /platform:x64 /target:exe /out:$outMemory $srcMemory
if ($LASTEXITCODE -ne 0) { throw "Failed to build ai-memory-mcp.exe" }

# 2. MCP Embedding Launcher
$outEmbedding = Join-Path $projectRoot "ai-embedding-mcp.exe"
$srcEmbedding = Join-Path $PSScriptRoot "launchers\EmbeddingMcpLauncher.cs"
Write-Host "Compiling $outEmbedding..."
& $csc /nologo /optimize /platform:x64 /target:exe /out:$outEmbedding $srcEmbedding
if ($LASTEXITCODE -ne 0) { throw "Failed to build ai-embedding-mcp.exe" }

# 3. Web UI Launcher
$outWeb = Join-Path $projectRoot "ai-library-web.exe"
$srcWeb = Join-Path $PSScriptRoot "launchers\WebLauncher.cs"
Write-Host "Compiling $outWeb..."
& $csc /nologo /optimize /platform:x64 /target:exe /out:$outWeb $srcWeb
if ($LASTEXITCODE -ne 0) { throw "Failed to build ai-library-web.exe" }

Write-Host "All launchers built successfully!" -ForegroundColor Green
Get-Item $outMemory, $outEmbedding, $outWeb | Select-Object Name, Length, LastWriteTime | Format-Table -AutoSize
