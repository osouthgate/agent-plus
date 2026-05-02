#!/usr/bin/env pwsh
# install.ps1 - agent-plus framework one-shot installer for Windows.
#
# Usage:
#   powershell -c "irm https://github.com/osouthgate/agent-plus/releases/latest/download/install.ps1 | iex"
#
# Environment overrides (set before running):
#   $env:AGENT_PLUS_INSTALL_DIR = "C:\Users\you\bin"        # wrapper .cmd dir
#   $env:AGENT_PLUS_PREFIX      = "C:\Users\you\.agent-plus" # plugin tree dir
#   $env:AGENT_PLUS_VERSION     = "0.15.1"                  # pin a version
#   $env:AGENT_PLUS_DRY_RUN     = "1"                       # print, don't write
#   $env:AGENT_PLUS_NO_INIT     = "1"                       # skip init chain
#   $env:AGENT_PLUS_UNATTENDED  = "1"                       # no prompts, exit 0 on partial
#
# Download and run with flags (alternative to iex):
#   $f = "$env:TEMP\ap-install.ps1"
#   irm https://github.com/osouthgate/agent-plus/releases/latest/download/install.ps1 -OutFile $f
#   & $f
#
# Verify post-install:
#   agent-plus-meta doctor --pretty

$ErrorActionPreference = 'Stop'

$REPO_OWNER = "osouthgate"
$REPO_NAME  = "agent-plus"
$Primitives = @("agent-plus-meta","repo-analyze","diff-summary","skill-feedback","skill-plus")

$InstallDir  = if ($env:AGENT_PLUS_INSTALL_DIR) { $env:AGENT_PLUS_INSTALL_DIR } `
               else { Join-Path $env:USERPROFILE ".local\bin" }
$Prefix      = if ($env:AGENT_PLUS_PREFIX) { $env:AGENT_PLUS_PREFIX } `
               else { Join-Path $env:USERPROFILE ".local\share\agent-plus" }
$DryRun      = $env:AGENT_PLUS_DRY_RUN     -eq "1"
$NoInit      = $env:AGENT_PLUS_NO_INIT     -eq "1"
$Unattended  = $env:AGENT_PLUS_UNATTENDED  -eq "1"

# ---- helpers -----------------------------------------------------------------

function Resolve-Tag {
    if ($env:AGENT_PLUS_VERSION) { return $env:AGENT_PLUS_VERSION }
    try {
        $api  = "https://api.github.com/repos/$REPO_OWNER/$REPO_NAME/releases/latest"
        $json = Invoke-RestMethod -Uri $api -UseBasicParsing -ErrorAction Stop
        return $json.tag_name
    } catch {
        return "main"
    }
}

function Get-TarballUrl([string]$Tag) {
    if ($Tag -eq "main" -or $Tag -eq "master") {
        return "https://github.com/$REPO_OWNER/$REPO_NAME/archive/refs/heads/$Tag.tar.gz"
    } elseif ($Tag.StartsWith("v")) {
        return "https://github.com/$REPO_OWNER/$REPO_NAME/archive/refs/tags/$Tag.tar.gz"
    } else {
        return "https://github.com/$REPO_OWNER/$REPO_NAME/archive/refs/tags/v$Tag.tar.gz"
    }
}

function Find-Python {
    foreach ($cmd in @('python3', 'python', 'py')) {
        try {
            $out = & $cmd --version 2>&1
            if ($out -match 'Python 3') { return $cmd }
        } catch {}
    }
    return $null
}

function Write-Wrapper([string]$Plugin, [string]$PyCmd) {
    $target  = Join-Path $InstallDir "$Plugin.cmd"
    $content = "@echo off`r`nsetlocal`r`n" +
               "if `"%AGENT_PLUS_PREFIX%`"==`"`" set `"AGENT_PLUS_PREFIX=$Prefix`"`r`n" +
               "$PyCmd `"%AGENT_PLUS_PREFIX%\$Plugin\bin\$Plugin`" %*`r`n"
    [System.IO.File]::WriteAllText($target, $content, [System.Text.Encoding]::ASCII)
}

function Install-FromSrc([string]$SrcRoot, [string]$PyCmd) {
    $i      = 0
    $failed = @()
    foreach ($plugin in $Primitives) {
        $i++
        $src = Join-Path $SrcRoot $plugin
        $dst = Join-Path $Prefix  $plugin
        if (-not (Test-Path $src -PathType Container)) {
            Write-Host ("[{0}/{1}] {2,-18} MISSING in source tree ({3})" -f $i, $Primitives.Count, $plugin, $src) -ForegroundColor Yellow
            $failed += $plugin
            continue
        }
        if (Test-Path $dst) { Remove-Item $dst -Recurse -Force }
        Copy-Item $src $dst -Recurse
        Write-Wrapper $plugin $PyCmd
        Write-Host ("[{0}/{1}] {2,-18} installed" -f $i, $Primitives.Count, $plugin)
    }
    return $failed
}

function Add-ToUserPath([string]$Dir) {
    $userPath = [Environment]::GetEnvironmentVariable("PATH", "User")
    $parts    = $userPath -split ';' | Where-Object { $_ -ne '' }
    if ($parts -notcontains $Dir) {
        $newPath = ($parts + $Dir) -join ';'
        [Environment]::SetEnvironmentVariable("PATH", $newPath, "User")
        $env:PATH = "$Dir;$env:PATH"
        return $true
    }
    return $false
}

function Locate-AgentPlusMeta {
    $cmd = Get-Command "agent-plus-meta.cmd" -ErrorAction SilentlyContinue
    if ($cmd) { return $cmd.Source }
    $candidate = Join-Path $InstallDir "agent-plus-meta.cmd"
    if (Test-Path $candidate) { return $candidate }
    return $null
}

function Print-Header {
    Write-Host "agent-plus framework installer (Windows)"
    Write-Host "========================================="
    if ($DryRun)     { Write-Host "(dry run -- nothing will be downloaded or written)" }
    if ($Unattended) { Write-Host "(unattended mode -- no prompts, exit 0 on partial install)" }
}

function Print-Footer {
    Write-Host ""
    Write-Host "Plugin trees installed under: $Prefix"
    Write-Host "Wrapper .cmd files installed: $InstallDir"
    Write-Host ""
    Write-Host "Register with Claude Code (so Claude can call the plugins directly):"
    Write-Host "  claude plugin marketplace add osouthgate/agent-plus"
    Write-Host "  foreach (`$p in @('agent-plus-meta','repo-analyze','diff-summary','skill-feedback','skill-plus')) {"
    Write-Host "    claude plugin install `"`$p@agent-plus`""
    Write-Host "  }"
    Write-Host ""
    Write-Host "Then in any open Claude session run:"
    Write-Host "  /reload-plugins"
    Write-Host "Or open a new Claude session. First thing to try:"
    Write-Host "  Ask Claude: 'what is this repo?' -- triggers repo-analyze"
    Write-Host ""
    Write-Host "Verify:"
    Write-Host "  agent-plus-meta doctor --pretty"
}

# ---- main --------------------------------------------------------------------

Print-Header

$Tag     = Resolve-Tag
$Tarball = Get-TarballUrl $Tag

if ($DryRun) {
    Write-Host ""
    Write-Host "tag:          $Tag"
    Write-Host "tarball:      $Tarball"
    Write-Host "prefix:       $Prefix"
    Write-Host "install dir:  $InstallDir"
    Write-Host ""
    $i = 0
    foreach ($plugin in $Primitives) {
        $i++
        Write-Host ("[{0}/{1}] {2,-18} would install tree at {3} and wrapper at {4}" -f `
            $i, $Primitives.Count, $plugin, (Join-Path $Prefix $plugin), (Join-Path $InstallDir "$plugin.cmd"))
    }
    if ($NoInit) {
        Write-Host ""
        Write-Host "(dry run) would skip agent-plus-meta init (--no-init)"
    } else {
        Write-Host ""
        Write-Host "(dry run) would chain: agent-plus-meta init"
    }
    exit 0
}

# Require Python 3.
$PyCmd = Find-Python
if (-not $PyCmd) {
    Write-Error "install.ps1: Python 3 not found. Install from https://python.org and re-run."
    if ($Unattended) { exit 0 } else { exit 1 }
}
Write-Host "Python command: $PyCmd"

# Require tar (ships with Windows 10 1803+).
if (-not (Get-Command tar -ErrorAction SilentlyContinue)) {
    Write-Error "install.ps1: tar not found. Update Windows 10 to 1803+ or install Git for Windows."
    if ($Unattended) { exit 0 } else { exit 1 }
}

$null = New-Item -ItemType Directory -Force $InstallDir
$null = New-Item -ItemType Directory -Force $Prefix

# Download and extract tarball.
$TmpDir = Join-Path $env:TEMP "agent-plus-install-$([System.IO.Path]::GetRandomFileName())"
$null   = New-Item -ItemType Directory -Force $TmpDir

try {
    Write-Host ""
    Write-Host "Downloading $Tarball ..."
    $TarPath = Join-Path $TmpDir "agent-plus.tar.gz"
    try {
        Invoke-WebRequest -Uri $Tarball -OutFile $TarPath -UseBasicParsing -ErrorAction Stop
    } catch {
        Write-Host "[install_ps1_curl_failed] tarball: $Tarball" -ForegroundColor Red
        if ($Unattended) { exit 0 } else { exit 1 }
    }

    Push-Location $TmpDir
    try {
        tar -xzf "agent-plus.tar.gz"
    } finally {
        Pop-Location
    }

    # Find extracted top-level dir (e.g. "agent-plus-0.15.1").
    $SrcRoot = Get-ChildItem $TmpDir -Directory | Where-Object { $_.Name -like "agent-plus-*" } |
               Select-Object -First 1 -ExpandProperty FullName
    if (-not $SrcRoot) {
        Write-Error "install.ps1: could not find extracted directory under $TmpDir"
        exit 1
    }

    $Failed = Install-FromSrc $SrcRoot $PyCmd

    if ($Failed.Count -gt 0) {
        Write-Host ""
        Write-Host ("install.ps1: the following primitive(s) failed to install: " + ($Failed -join " ")) -ForegroundColor Red
        if ($Unattended) {
            Write-Host "install.ps1: unattended mode -- exit 0 despite partial install." -ForegroundColor Yellow
        } else {
            exit 1
        }
    }

} finally {
    Remove-Item $TmpDir -Recurse -Force -ErrorAction SilentlyContinue
}

# Add InstallDir to user PATH if not present.
if (Add-ToUserPath $InstallDir) {
    Write-Host ""
    Write-Host "Added $InstallDir to your user PATH."
    Write-Host "Restart your terminal (or run: `$env:PATH = `"$InstallDir;`$env:PATH`") to use the new commands."
}

# ---- chain into agent-plus-meta init ----------------------------------------

if ($NoInit) {
    Write-Host ""
    Write-Host "Skipping agent-plus-meta init (AGENT_PLUS_NO_INIT=1)."
    if ($Failed.Count -eq 0) { Print-Footer }
    exit 0
}

$ApmBin = Locate-AgentPlusMeta
if (-not $ApmBin) {
    Write-Host ""
    Write-Host "install.ps1: agent-plus-meta.cmd not reachable -- skipping init chain." -ForegroundColor Yellow
    Write-Host "Hint: restart your terminal so PATH updates, then run: agent-plus-meta init"
    if ($Failed.Count -eq 0) { Print-Footer }
    exit 0
}

Write-Host ""
# Pipe stdout to Out-Null so the machine-readable JSON envelope is silenced.
# All human-readable output goes to stderr and still appears in the terminal.
if ($Unattended) {
    Write-Host "Running agent-plus-meta init --non-interactive --auto..."
    & $ApmBin init --non-interactive --auto | Out-Null
} else {
    Write-Host "Running agent-plus-meta init..."
    & $ApmBin init | Out-Null
}

if ($Failed.Count -eq 0) { Print-Footer }
exit 0
