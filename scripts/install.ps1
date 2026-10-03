# Mikronous installer for Windows 10/11 (PowerShell 5.1 or 7). No admin rights needed.
#
#   powershell -ExecutionPolicy Bypass -File scripts\install.ps1            # everything
#   powershell -ExecutionPolicy Bypass -File scripts\install.ps1 -NoModel   # keep your own llama-server on :8081
#   powershell -ExecutionPolicy Bypass -File scripts\install.ps1 -NoTray    # no tray app / hotkey / autostart
#
# Installs Hermes Agent when it is missing, creates the `mikronous` profile, links the plugin and skills,
# installs the `mik` CLI, downloads a prebuilt llama.cpp (CUDA 13.4 / CUDA 12.4 / Vulkan / CPU, chosen from the
# hardware) and the default model, installs the Hermes gateway as a scheduled task, and starts the tray.
# Idempotent: re-running updates config in place and never overwrites secrets, memories or an edited SOUL.md.
[CmdletBinding()]
param(
  [switch]$NoModel,
  [switch]$NoTray,
  [string]$ModelRepo = "unsloth/Qwen3-4B-Instruct-2507-GGUF",
  [string]$ModelFile = "Qwen3-4B-Instruct-2507-Q4_K_M.gguf",
  [string]$LlamaBackend = "",      # cuda-13.4 | cuda-12.4 | vulkan | cpu (default: detect)
  [string]$LlamaTag = "",          # pin a llama.cpp nightly tag, e.g. b11146
  [string]$Voice = "full",         # plain | light | full (kept on re-runs)
  [string]$Hotkey = "Ctrl+Alt+Space",
  [string]$HotkeySelection = "Ctrl+Alt+Shift+Space"
)
$ErrorActionPreference = "Stop"
$ProgressPreference = "SilentlyContinue"

$RepoDir     = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
$Profile_    = "mikronous"
$HermesHome  = if ($env:HERMES_HOME) { $env:HERMES_HOME } else { Join-Path $env:LOCALAPPDATA "hermes" }
$ProfileHome = Join-Path $HermesHome "profiles\$Profile_"
$ConfDir     = Join-Path $env:LOCALAPPDATA "mikronous"
$LlamaPrefix = Join-Path $ConfDir "llama.cpp"
$ModelsDir   = Join-Path $HermesHome "models"
$Failures    = New-Object System.Collections.Generic.List[string]
$Step        = "start"

function Step($name) { $script:Step = $name; Write-Host "`n==> $name" -ForegroundColor White }
function Fail($msg)  { Write-Host "   !! $msg" -ForegroundColor Yellow; $Failures.Add("$Step`: $msg") }
function Refresh-Path {
  $env:Path = [Environment]::GetEnvironmentVariable("Path", "User") + ";" + [Environment]::GetEnvironmentVariable("Path", "Machine")
  $env:Path = (Join-Path $HermesHome "bin") + ";" + (Join-Path $env:APPDATA "Python\Scripts") + ";" + $env:Path
}
function Have($cmd) { return [bool](Get-Command $cmd -ErrorAction SilentlyContinue) }
function Hermes { & hermes @args }
function HP { & hermes -p $Profile_ @args }
function Sha($path) { (Get-FileHash -Algorithm SHA256 $path).Hash.ToLower() }
function Read-Env($path) {
  $h = @{}
  if (Test-Path $path) {
    foreach ($line in Get-Content $path) {
      if ($line -match '^\s*#' -or $line -notmatch '=') { continue }
      $k, $v = $line -split '=', 2
      $h[$k.Trim()] = ($v -split ' #', 2)[0].Trim().Trim('"').Trim("'")
    }
  }
  return $h
}
function Ensure-EnvKey($file, $key, $value) {
  if (-not (Test-Path $file)) { New-Item -ItemType File -Path $file -Force | Out-Null }
  if (-not (Select-String -Path $file -Pattern "^$key=" -Quiet)) { Add-Content $file "$key=$value"; Write-Host "set $key in $file" }
}
function New-Key { -join ((1..48) | ForEach-Object { '{0:x}' -f (Get-Random -Maximum 16) }) }
function Download($url, $dest) {
  if (Have curl.exe) { & curl.exe -fL --retry 3 -o $dest $url; if ($LASTEXITCODE -ne 0) { throw "download failed: $url" } }
  else { Invoke-WebRequest -Uri $url -OutFile $dest -UseBasicParsing }
}

Refresh-Path

# ---------------------------------------------------------------------------
Step "Hermes Agent"
if (Have hermes) {
  Write-Host "found: $((Get-Command hermes).Source)"
} elseif ($env:MIKRONOUS_SKIP_HERMES_INSTALL -eq "1") {
  throw "hermes not found and MIKRONOUS_SKIP_HERMES_INSTALL=1; install it: iex (irm https://hermes-agent.nousresearch.com/install.ps1)"
} else {
  Write-Host "hermes not found; installing Hermes Agent under $HermesHome (non-interactive)"
  $installer = Invoke-RestMethod -Uri "https://hermes-agent.nousresearch.com/install.ps1" -UseBasicParsing
  & ([scriptblock]::Create($installer)) -NonInteractive
  Refresh-Path
  if (-not (Have hermes)) { throw "Hermes installed but 'hermes' is not on PATH; open a new terminal and re-run scripts\install.ps1" }
  Write-Host "installed: $((Get-Command hermes).Source)"
}

# ---------------------------------------------------------------------------
Step "Hermes profile '$Profile_'"
$CreatedNow = $false
if ((Test-Path $ProfileHome) -and ((Test-Path "$ProfileHome\config.yaml") -or (Test-Path "$ProfileHome\SOUL.md") -or (Test-Path "$ProfileHome\.env"))) {
  Write-Host "profile exists at $ProfileHome"
} else {
  Hermes profile create $Profile_
  if ($LASTEXITCODE -ne 0) { throw "hermes profile create failed" }
  $CreatedNow = $true
}
if (-not (Test-Path $ProfileHome)) { throw "expected $ProfileHome after profile create" }
New-Item -ItemType Directory -Force -Path "$ProfileHome\.mikronous" | Out-Null

# SOUL.md: install ours on first install, over Hermes's starter, or over our own unmodified version.
$SoulSrc = "$RepoDir\profile\SOUL.md"; $SoulDst = "$ProfileHome\SOUL.md"
$ShaFile = "$ProfileHome\.mikronous\soul.sha"; $SrcShaFile = "$ProfileHome\.mikronous\soul.src.sha"
$installSoul = $false; $reason = ""
if (-not (Test-Path $SoulDst) -or $CreatedNow) { $installSoul = $true; $reason = "first install" }
elseif ((Test-Path $ShaFile) -and ((Get-Content $ShaFile -Raw).Trim() -eq (Sha $SoulDst)) -and (Test-Path $SrcShaFile) -and ((Get-Content $SrcShaFile -Raw).Trim() -eq (Sha $SoulSrc))) { $reason = "up to date" }
elseif ((Get-Content $SoulDst -Raw) -match '^You are Hermes Agent') { $installSoul = $true; $reason = "replacing Hermes starter" }
elseif ((Test-Path $ShaFile) -and ((Get-Content $ShaFile -Raw).Trim() -eq (Sha $SoulDst))) { $installSoul = $true; $reason = "updating unmodified Mikronous version" }
elseif ($env:MIKRONOUS_FORCE_SOUL -eq "1") { $installSoul = $true; $reason = "forced" }
else { $reason = "edited locally; keeping it (MIKRONOUS_FORCE_SOUL=1 to overwrite)" }
$prevVoice = ""
if (Test-Path $SoulDst) {
  $m = Select-String -Path $SoulDst -Pattern '## Voice: (plain|light|full)' | Select-Object -First 1
  if ($m) { $prevVoice = $m.Matches[0].Groups[1].Value }
}
$VoiceLevel = if ($prevVoice) { $prevVoice } else { $Voice }
if ($installSoul) {
  Copy-Item $SoulSrc $SoulDst -Force
  $block = (Get-Content "$RepoDir\profile\voices\$VoiceLevel.md" -Raw).Trim() + "`n"
  $text = Get-Content $SoulDst -Raw
  $text = [regex]::Replace($text, '(?s)<!-- voice:start -->\n.*?<!-- voice:end -->', ("<!-- voice:start -->`n" + $block + "<!-- voice:end -->").Replace('$', '$$'))
  [IO.File]::WriteAllText($SoulDst, $text)
  (Sha $SoulDst) | Set-Content $ShaFile; (Sha $SoulSrc) | Set-Content $SrcShaFile
  $reason = "$reason, voice: $VoiceLevel"
}
Write-Host "SOUL.md: $reason"

# config.yaml: ours is the source of truth (backup anything that differs).
$CfgDst = "$ProfileHome\config.yaml"
if ((Test-Path $CfgDst) -and ((Get-Content $CfgDst -Raw) -ne (Get-Content "$RepoDir\profile\config.yaml" -Raw))) {
  Copy-Item $CfgDst "$CfgDst.bak" -Force
}
Copy-Item "$RepoDir\profile\config.yaml" $CfgDst -Force
Write-Host "config.yaml installed"

# .env: add missing keys only, never overwrite.
$EnvDst = "$ProfileHome\.env"
foreach ($line in Get-Content "$RepoDir\profile\env.example") {
  if ($line -match '^\s*#' -or $line -notmatch '=') { continue }
  $k, $v = $line -split '=', 2
  if ($k -eq "API_SERVER_KEY") { $v = New-Key }
  Ensure-EnvKey $EnvDst $k $v
}

# ---------------------------------------------------------------------------
Step "Mikronous plugin + skills"
$PluginDst = "$ProfileHome\plugins\mikronous"; $SkillsDst = "$ProfileHome\skills\mikronous"
New-Item -ItemType Directory -Force -Path "$ProfileHome\plugins", "$ProfileHome\skills" | Out-Null
foreach ($pair in @(@($PluginDst, "$RepoDir\hermes_plugin\mikronous"), @($SkillsDst, "$RepoDir\skills"))) {
  $dst, $src = $pair
  if (Test-Path $dst) { $item = Get-Item $dst; if ($item.LinkType -ne "Junction" -and $item.LinkType -ne "SymbolicLink") { Remove-Item $dst -Recurse -Force } else { (Get-Item $dst).Delete() } }
  New-Item -ItemType Junction -Path $dst -Target $src | Out-Null   # junctions need no developer mode
  Write-Host "linked $dst -> $src"
}
HP plugins enable mikronous 2>$null | Out-Null

# ---------------------------------------------------------------------------
Step "mik CLI"
$Extras = if ($NoTray) { "" } else { "[tray]" }
$Mik = $null
if (Have pipx) {
  pipx install --force --editable "$RepoDir$Extras" | Out-Null; Refresh-Path
  if (Have mik) { $Mik = (Get-Command mik).Source; Write-Host "installed: mik (pipx)" } else { Fail "pipx install failed" }
} else {
  $Venv = Join-Path $ConfDir "venv"
  $py = if (Have py) { "py -3" } elseif (Have python) { "python" } else { $null }
  if (-not $py) { Fail "no Python found; install Python 3.11+ from python.org (tick 'Add to PATH') and re-run" }
  else {
    Invoke-Expression "$py -m venv `"$Venv`""
    & "$Venv\Scripts\python.exe" -m pip install --quiet --upgrade pip
    & "$Venv\Scripts\python.exe" -m pip install --quiet --editable "$RepoDir$Extras"
    $Mik = "$Venv\Scripts\mik.exe"
    $BinDir = Join-Path $ConfDir "bin"; New-Item -ItemType Directory -Force -Path $BinDir | Out-Null
    Set-Content "$BinDir\mik.cmd" "@echo off`r`n`"$Mik`" %*"
    $userPath = [Environment]::GetEnvironmentVariable("Path", "User")
    if ($userPath -notlike "*$BinDir*") { [Environment]::SetEnvironmentVariable("Path", "$userPath;$BinDir", "User"); Write-Host "added $BinDir to your user PATH (open a new terminal for 'mik')" }
    $env:Path = "$BinDir;$env:Path"
    Write-Host "installed: mik (venv at $Venv)"
  }
}

# ---------------------------------------------------------------------------
if (-not $NoModel) {
  Step "Local model + llama-server"
  try {
    # Backend from the hardware: NVIDIA driver CUDA version -> cuda-13.4 / cuda-12.4, other GPU -> vulkan, none -> cpu.
    $backend = $LlamaBackend
    if (-not $backend) {
      $backend = "cpu"
      if (Have nvidia-smi) {
        $banner = (& nvidia-smi 2>$null) -join "`n"
        if ($banner -match 'CUDA Version:\s*(\d+)\.(\d+)') {
          $ver = [double]("$($Matches[1]).$($Matches[2])")
          $backend = if ($ver -ge 13.4) { "cuda-13.4" } elseif ($ver -ge 12.4) { "cuda-12.4" } else { "vulkan" }
        } else { $backend = "vulkan" }
      } elseif ((Get-CimInstance Win32_VideoController | Where-Object { $_.Name -match 'AMD|Radeon|Intel Arc' })) { $backend = "vulkan" }
    }
    $tag = $LlamaTag
    if (-not $tag) { $tag = (Invoke-RestMethod -Uri "https://github.com/ggml-org/llama.cpp/releases/latest/download/nightly-tag.txt" -UseBasicParsing).Trim() }
    Write-Host "[install-llama] backend: $backend  tag: $tag"
    $binDir = Join-Path $LlamaPrefix "bin"
    $tagFile = Join-Path $LlamaPrefix "TAG"; $backendFile = Join-Path $LlamaPrefix "BACKEND"
    $fresh = (Test-Path "$binDir\llama-server.exe") -and (Test-Path $tagFile) -and ((Get-Content $tagFile -Raw).Trim() -eq $tag) -and (Test-Path $backendFile) -and ((Get-Content $backendFile -Raw).Trim() -eq $backend) -and ($env:MIKRONOUS_LLAMA_REINSTALL -ne "1")
    if ($fresh) { Write-Host "[install-llama] already installed: $binDir ($backend, $tag)" }
    else {
      $tmp = Join-Path $env:TEMP "mikronous-llama"; if (Test-Path $tmp) { Remove-Item $tmp -Recurse -Force }; New-Item -ItemType Directory -Path $tmp | Out-Null
      $base = "https://github.com/ggml-org/llama.cpp/releases/download/$tag"
      $assets = @("llama-$tag-bin-win-$backend-x64.zip")
      if ($backend -like "cuda-*") { $assets += "cudart-llama-bin-win-$backend-x64.zip" }
      if (Test-Path $LlamaPrefix) { Remove-Item $LlamaPrefix -Recurse -Force }
      New-Item -ItemType Directory -Force -Path $binDir | Out-Null
      foreach ($a in $assets) {
        Write-Host "[install-llama] downloading $a"
        Download "$base/$a" "$tmp\$a"
        Expand-Archive -Path "$tmp\$a" -DestinationPath "$tmp\x" -Force
      }
      $server = Get-ChildItem -Path "$tmp\x" -Recurse -Filter "llama-server.exe" | Select-Object -First 1
      if (-not $server) { throw "llama-server.exe not found in the archive" }
      Get-ChildItem -Path "$tmp\x" -Recurse -File | ForEach-Object { Copy-Item $_.FullName -Destination $binDir -Force }
      $tag | Set-Content $tagFile; $backend | Set-Content $backendFile
      Remove-Item $tmp -Recurse -Force
      Write-Host "[install-llama] installed $binDir\llama-server.exe ($backend, $tag)"
    }
    # Model
    New-Item -ItemType Directory -Force -Path $ModelsDir | Out-Null
    $modelPath = Join-Path $ModelsDir $ModelFile
    if (-not (Test-Path $modelPath)) {
      Write-Host "downloading $ModelRepo/$ModelFile -> $modelPath (a few GB)"
      Download "https://huggingface.co/$ModelRepo/resolve/main/$ModelFile" "$modelPath.part"
      Move-Item "$modelPath.part" $modelPath -Force
    } else { Write-Host "model present: $modelPath" }
    # llama.env (same keys as Linux; mik model rewrites it)
    New-Item -ItemType Directory -Force -Path $ConfDir | Out-Null
    $llamaEnv = Join-Path $ConfDir "llama.env"
    if (-not (Test-Path $llamaEnv)) {
      $threads = [math]::Max(1, [int]($env:NUMBER_OF_PROCESSORS) / 2)
      (Get-Content "$RepoDir\systemd\mikronous-llama.env.example") |
        ForEach-Object { $_ -replace '^LLAMA_SERVER=.*', "LLAMA_SERVER=$binDir\llama-server.exe" -replace '^LLAMA_MODEL=.*', "LLAMA_MODEL=$modelPath" -replace '^LLAMA_THREADS=.*', "LLAMA_THREADS=$threads" } |
        Set-Content $llamaEnv
      Write-Host "wrote $llamaEnv"
    } else {
      (Get-Content $llamaEnv) | ForEach-Object { $_ -replace '^LLAMA_SERVER=.*', "LLAMA_SERVER=$binDir\llama-server.exe" } | Set-Content $llamaEnv
      Write-Host "kept existing $llamaEnv (edit it or run 'mik model')"
    }
    if ($Mik) {
      & $Mik model start
      if ($LASTEXITCODE -ne 0) { Fail "llama-server did not come up; see $ConfDir\llama-server.log" } else { Write-Host "llama-server is up" }
    }
  } catch { Fail "model step failed: $($_.Exception.Message) (try -LlamaBackend vulkan or cpu, or -NoModel with your own server on :8081)" }
}

# ---------------------------------------------------------------------------
Step "Hermes gateway (API server + cron) as a scheduled task"
# Standalone per-profile gateway (gateway.standalone: true in config.yaml). Give it its own port if the
# default profile's gateway owns 8642.
$hostEnv = Read-Env (Join-Path $HermesHome ".env"); $hostPort = if ($hostEnv["API_SERVER_PORT"]) { $hostEnv["API_SERVER_PORT"] } else { "8642" }
$profEnv = Read-Env $EnvDst
if (-not $profEnv["API_SERVER_PORT"] -or $profEnv["API_SERVER_PORT"] -eq $hostPort) {
  $apiPort = [int]$hostPort + 1
  $content = Get-Content $EnvDst | Where-Object { $_ -notmatch '^API_SERVER_PORT=' }
  $content + "API_SERVER_PORT=$apiPort" | Set-Content $EnvDst
  Write-Host "set API_SERVER_PORT=$apiPort in $EnvDst (host gateway owns $hostPort)"
}
try {
  "y`ny`ny`n" | HP gateway install | Out-Host
  "y`ny`n" | HP gateway restart | Out-Host
} catch { Fail "gateway install failed: $($_.Exception.Message); run: hermes -p $Profile_ gateway install" }

# ---------------------------------------------------------------------------
if (-not $NoTray -and $Mik) {
  Step "Tray app (hotkey $Hotkey)"
  try {
    # hotkey preference lives in the tray's state file; autostart via the per-user Run key (no admin).
    $stateFile = Join-Path $ConfDir "tray.json"
    $state = if (Test-Path $stateFile) { Get-Content $stateFile -Raw | ConvertFrom-Json } else { [pscustomobject]@{} }
    $state | Add-Member -NotePropertyName hotkey -NotePropertyValue $Hotkey -Force
    $state | Add-Member -NotePropertyName hotkey_selection -NotePropertyValue $HotkeySelection -Force
    $state | ConvertTo-Json | Set-Content $stateFile
    $pyw = Join-Path (Split-Path $Mik) "pythonw.exe"
    if (-not (Test-Path $pyw)) { $pyw = (Get-Command pythonw -ErrorAction SilentlyContinue).Source }
    $cmd = if ($pyw) { "`"$pyw`" -m mikronous_tray" } else { "`"$Mik`" tray" }
    New-ItemProperty -Path "HKCU:\Software\Microsoft\Windows\CurrentVersion\Run" -Name "Mikronous" -Value $cmd -PropertyType String -Force | Out-Null
    Write-Host "autostart: HKCU Run key -> $cmd"
    & $Mik show
    Write-Host "tray started (look for the cog in the notification area; $Hotkey toggles the window, $HotkeySelection acts on selected text)"
  } catch { Fail "tray step failed: $($_.Exception.Message); run: mik tray" }
}

# ---------------------------------------------------------------------------
Step "Done"
if ($Failures.Count) { Write-Host "Finished with $($Failures.Count) problem(s):"; $Failures | ForEach-Object { Write-Host "  - $_" } }
else { Write-Host "Everything installed." }
Write-Host ""
Write-Host "Check everything:   mik doctor"
Write-Host "Talk to it now:     hermes -p $Profile_ chat"
if (-not $NoTray) { Write-Host "Desktop:            $Hotkey toggles the chat window; so does 'mik toggle'." }
