# Mikronous zero-prerequisite entry point for Windows 10/11. Run from any PowerShell (no admin needed):
#
#   irm https://raw.githubusercontent.com/GoldenCracker97/Mikronous/Main/scripts/bootstrap.ps1 | iex
#
# Installs Git and Python 3.12 with winget when they are missing, refreshes PATH in this session, clones
# (or updates) the repo under $HOME\Mikronous, then hands over to scripts\install.ps1. Arguments for the
# installer can be passed through $env:MIKRONOUS_INSTALL_ARGS, e.g. "-NoModel -Voice light".
$ErrorActionPreference = "Stop"
$ProgressPreference = "SilentlyContinue"
$Repo = "https://github.com/GoldenCracker97/Mikronous"
$Dest = Join-Path $HOME "Mikronous"

function Have($cmd) { [bool](Get-Command $cmd -ErrorAction SilentlyContinue) }

function Refresh-Path {
  $m = [Environment]::GetEnvironmentVariable("Path", "Machine")
  $u = [Environment]::GetEnvironmentVariable("Path", "User")
  $env:Path = ($m, $u, $env:Path | Where-Object { $_ }) -join ";"
}

function Ensure-Tool($cmd, $wingetId, $label) {
  if (Have $cmd) { Write-Host "found: $label"; return }
  if (-not (Have winget)) {
    throw "$label is missing and winget is not available. Install 'App Installer' from the Microsoft Store (that provides winget), or install $label by hand, then re-run."
  }
  Write-Host "installing $label with winget ..."
  & winget install -e --id $wingetId --accept-source-agreements --accept-package-agreements --silent | Out-Null
  Refresh-Path
  if (-not (Have $cmd)) { throw "$label was installed but '$cmd' is still not on PATH. Open a new PowerShell and re-run this command." }
  Write-Host "installed: $label"
}

Ensure-Tool "git"    "Git.Git"            "Git"
Ensure-Tool "python" "Python.Python.3.12" "Python 3.12"

if (Test-Path (Join-Path $Dest ".git")) {
  Write-Host "updating $Dest"
  & git -C $Dest pull --ff-only | Out-Null
} else {
  Write-Host "cloning into $Dest"
  & git clone $Repo $Dest | Out-Null
}

$args_ = @()
if ($env:MIKRONOUS_INSTALL_ARGS) { $args_ = $env:MIKRONOUS_INSTALL_ARGS -split "\s+" | Where-Object { $_ } }
Set-Location $Dest
Write-Host "running scripts\install.ps1 $($args_ -join ' ')"
& powershell -NoProfile -ExecutionPolicy Bypass -File (Join-Path $Dest "scripts\install.ps1") @args_
