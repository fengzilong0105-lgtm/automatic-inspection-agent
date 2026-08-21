#Requires -Version 5.1
<#
.SYNOPSIS
  Build SteadyOps onedir package, optionally wrap with Inno Setup installer,
  and emit dist\version.json for the online-update feed.

.USAGE
  .\scripts\build.ps1
  .\scripts\build.ps1 -Console
  .\scripts\build.ps1 -Installer
  .\scripts\build.ps1 -Installer -ReleaseNotes "修复告警时间显示"
  .\scripts\build.ps1 -Installer -UpdateBaseUrl "http://106.120.201.126:14828/down/steadyOps"

.NOTES
  UpdateBaseUrl defaults to env STEADYOPS_UPDATE_BASE_URL, then the intranet
  download root used by the team. version.json is written next to the Setup.
#>
param(
    [switch]$Console,
    [switch]$Installer,
    [string]$UpdateBaseUrl = "",
    [string]$ReleaseNotes = ""
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
Set-Location $Root

function Get-ProjectVersion {
    $toml = Join-Path $Root "pyproject.toml"
    $line = Select-String -Path $toml -Pattern '^\s*version\s*=\s*"([^"]+)"' | Select-Object -First 1
    if (-not $line) {
        throw "Cannot read version from pyproject.toml"
    }
    return $line.Matches[0].Groups[1].Value
}

function Find-Iscc {
    $candidates = @(
        ${env:INNO_SETUP_ISCC},
        (Join-Path ${env:LocalAppData} "Programs\Inno Setup 6\ISCC.exe"),
        "C:\Program Files (x86)\Inno Setup 6\ISCC.exe",
        "C:\Program Files\Inno Setup 6\ISCC.exe"
    ) | Where-Object { $_ }
    foreach ($path in $candidates) {
        if ($path -and (Test-Path $path)) {
            return $path
        }
    }
    $cmd = Get-Command iscc -ErrorAction SilentlyContinue
    if ($cmd) {
        return $cmd.Source
    }
    return $null
}

function Resolve-UpdateBaseUrl {
    param([string]$Explicit)
    if ($Explicit -and $Explicit.Trim()) {
        return $Explicit.Trim().TrimEnd("/")
    }
    if ($env:STEADYOPS_UPDATE_BASE_URL -and $env:STEADYOPS_UPDATE_BASE_URL.Trim()) {
        return $env:STEADYOPS_UPDATE_BASE_URL.Trim().TrimEnd("/")
    }
    return "http://106.120.201.126:14828/down/steadyOps"
}

function Write-UpdateVersionJson {
    param(
        [string]$SetupPath,
        [string]$Version,
        [string]$BaseUrl,
        [string]$Notes,
        [string[]]$OutputPaths
    )

    $hash = (Get-FileHash -Path $SetupPath -Algorithm SHA256).Hash.ToLowerInvariant()
    $setupName = Split-Path $SetupPath -Leaf
    $url = "$BaseUrl/$setupName"
    if (-not $Notes) {
        $Notes = "SteadyOps $Version"
    }

    $payload = [ordered]@{
        version = $Version
        url     = $url
        sha256  = $hash
        notes   = $Notes
    }
    $json = ($payload | ConvertTo-Json -Depth 5) + "`n"
    $utf8 = New-Object System.Text.UTF8Encoding $false

    foreach ($out in $OutputPaths) {
        $dir = Split-Path $out -Parent
        if ($dir -and -not (Test-Path $dir)) {
            New-Item -ItemType Directory -Path $dir -Force | Out-Null
        }
        [System.IO.File]::WriteAllText($out, $json, $utf8)
        Write-Host "Wrote update feed: $out" -ForegroundColor Green
    }

    Write-Host "  version: $Version"
    Write-Host "  url:     $url"
    Write-Host "  sha256:  $hash"
}

$Python = Join-Path $Root ".venv\Scripts\python.exe"
if (-not (Test-Path $Python)) {
    Write-Host "Virtual environment not found. Creating .venv ..."
    python -m venv .venv
    $Python = Join-Path $Root ".venv\Scripts\python.exe"
}

$Version = Get-ProjectVersion
Write-Host "Project version: $Version"

$VersionPy = Join-Path $Root "agent\version.py"
if (Test-Path $VersionPy) {
    $content = Get-Content $VersionPy -Raw -Encoding UTF8
    $updated = [regex]::Replace(
        $content,
        'APP_VERSION\s*=\s*"[^"]*"',
        "APP_VERSION = `"$Version`""
    )
    if ($updated -ne $content) {
        Set-Content -Path $VersionPy -Value $updated -Encoding UTF8 -NoNewline
        Write-Host "Synced agent/version.py APP_VERSION -> $Version"
    }
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

Write-Host "Running PyInstaller onedir (this may take several minutes) ..."
& $Python -m PyInstaller $Spec --noconfirm --clean
if ($LASTEXITCODE -ne 0) {
    Write-Error "PyInstaller failed with exit code $LASTEXITCODE"
}

$OutDir = Join-Path $Root "dist\SteadyOps"
$OutExe = Join-Path $OutDir "SteadyOps.exe"
if (-not (Test-Path $OutExe)) {
    Write-Error "Build failed: $OutExe not found."
}

Write-Host ""
Write-Host "Onedir build succeeded: $OutDir" -ForegroundColor Green
Write-Host "  Launch: $OutExe"

if ($Console -and (Test-Path (Join-Path $Root "build\_inspection-agent-console.spec"))) {
    Remove-Item (Join-Path $Root "build\_inspection-agent-console.spec") -Force
}

if (-not $Installer) {
    Write-Host ""
    Write-Host "Next: .\scripts\build.ps1 -Installer   # requires Inno Setup 6; also writes dist\version.json"
    Write-Host "Or share/test dist\SteadyOps\ folder directly."
    return
}

$Iscc = Find-Iscc
if (-not $Iscc) {
    Write-Error @"
Inno Setup 6 (ISCC.exe) not found.
Install from https://jrsoftware.org/isinfo.php
Or set env INNO_SETUP_ISCC to the full path of ISCC.exe
"@
}

$Iss = Join-Path $Root "installers\SteadyOps.iss"
Write-Host "Running Inno Setup: $Iscc ..."
& $Iscc "/DMyAppVersion=$Version" $Iss
if ($LASTEXITCODE -ne 0) {
    Write-Error "Inno Setup failed with exit code $LASTEXITCODE"
}

$Setup = Join-Path $Root "dist\SteadyOps-Setup-$Version.exe"
if (-not (Test-Path $Setup)) {
    Write-Error "Installer not found: $Setup"
}

$SizeMB = [math]::Round((Get-Item $Setup).Length / 1MB, 1)
Write-Host ""
Write-Host "Installer succeeded: $Setup ($SizeMB MB)" -ForegroundColor Green

$BaseUrl = Resolve-UpdateBaseUrl -Explicit $UpdateBaseUrl
Write-Host ""
Write-Host "Generating version.json (base: $BaseUrl) ..."
Write-UpdateVersionJson `
    -SetupPath $Setup `
    -Version $Version `
    -BaseUrl $BaseUrl `
    -Notes $ReleaseNotes `
    -OutputPaths @(
        (Join-Path $Root "dist\version.json"),
        (Join-Path $Root "releases\version.json")
    )

Write-Host ""
Write-Host "Upload to server /DATA1/download/steadyOps/ :" -ForegroundColor Cyan
Write-Host "  1) $Setup"
Write-Host "  2) $(Join-Path $Root 'dist\version.json')"
Write-Host "Client feed URL: $BaseUrl/version.json"
Write-Host "Data stays in %APPDATA%\SteadyOps."
