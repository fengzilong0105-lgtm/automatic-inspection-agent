#Requires -Version 5.1
<#
.SYNOPSIS
  Build SteadyOps.exe (single-file, shareable).

.USAGE
  .\scripts\build.ps1
  .\scripts\build.ps1 -Console   # keep console window for debugging
#>
param(
    [switch]$Console
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
Set-Location $Root

$Python = Join-Path $Root ".venv\Scripts\python.exe"
if (-not (Test-Path $Python)) {
    Write-Host "Virtual environment not found. Creating .venv ..."
    python -m venv .venv
    $Python = Join-Path $Root ".venv\Scripts\python.exe"
}

Write-Host "Installing project + build dependencies ..."
$PipIndex = if ($env:PIP_INDEX_URL) { $env:PIP_INDEX_URL } else { "https://pypi.tuna.tsinghua.edu.cn/simple" }
$PipHost = if ($env:PIP_TRUSTED_HOST) { $env:PIP_TRUSTED_HOST } else { "pypi.tuna.tsinghua.edu.cn" }
& $Python -m pip install -U pip --default-timeout=600 -i $PipIndex --trusted-host $PipHost | Out-Null
& $Python -m pip install -e ".[build]" --default-timeout=600 -i $PipIndex --trusted-host $PipHost | Out-Null
$Spec = Join-Path $Root "build\inspection-agent.spec"
if ($Console) {
    $SpecContent = Get-Content $Spec -Raw
    $SpecContent = $SpecContent -replace "console=False", "console=True"
    $TempSpec = Join-Path $Root "build\_inspection-agent-console.spec"
    Set-Content -Path $TempSpec -Value $SpecContent -Encoding UTF8
    $Spec = $TempSpec
}

Write-Host "Generating application icon ..."
& $Python (Join-Path $Root "scripts\make_icon.py")
if ($LASTEXITCODE -ne 0) {
    Write-Error "Icon generation failed."
}

Write-Host "Running PyInstaller (this may take several minutes) ..."
& $Python -m PyInstaller $Spec --noconfirm --clean

$Out = Join-Path $Root "dist\SteadyOps.exe"
if (Test-Path $Out) {
    $SizeMB = [math]::Round((Get-Item $Out).Length / 1MB, 1)
    Write-Host ""
    Write-Host "Build succeeded: $Out ($SizeMB MB)" -ForegroundColor Green
    Write-Host "Share dist\SteadyOps.exe — recipients double-click to run the desktop app."
} else {
    Write-Error "Build failed: $Out not found."
}

if ($Console -and (Test-Path (Join-Path $Root "build\_inspection-agent-console.spec"))) {
    Remove-Item (Join-Path $Root "build\_inspection-agent-console.spec") -Force
}
