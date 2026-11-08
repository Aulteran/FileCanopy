<#
Self-contained local test stack built from this checkout (no ghcr, works offline once images are cached).
  dev-stack.ps1 [-Port 8080] [-Tailscale <ip>] [-NoPull] [-NoOpen]   interactive menu (what dev-up.cmd runs)
  dev-stack.ps1 up | restart | down | wipe [-Yes]                              the same steps, non-interactive
The stack lives in <repo>\.dev-stack (gitignored): .env, library\, app-data\. Docker project name: filecanopy-dev.
#>
param(
  [Parameter(Position = 0)][ValidateSet('menu', 'up', 'restart', 'down', 'wipe')][string]$Action = 'menu',
  [int]$Port = 8080,
  [string]$Tailscale = '',
  [switch]$NoPull,
  [switch]$NoOpen,
  [switch]$Yes
)
$ErrorActionPreference = 'Stop'
$src = (Resolve-Path "$PSScriptRoot\..").Path
$stack = Join-Path $src '.dev-stack'
$envFile = Join-Path $stack '.env'
$override = Join-Path $stack 'compose.tailscale.yaml'

if (-not (Get-Command docker -ErrorAction SilentlyContinue)) { throw 'Docker is not installed or not on PATH. Start Docker Desktop first.' }
docker info *> $null
if ($LASTEXITCODE) { throw 'Docker is not running. Start Docker Desktop, wait until it says "running", then try again.' }

function Compose([string[]]$cmd, [switch]$Quiet) {
  $env:FILECANOPY_SRC = $src
  $files = @('-f', "$src\deploy\compose.yaml", '-f', "$src\deploy\contrib\build.yaml")
  if (Test-Path $override) { $files += @('-f', $override) }
  $a = @('compose', '-p', 'filecanopy-dev', '--project-directory', $stack, '--env-file', $envFile) + $files + $cmd
  if ($Quiet) {
    # Capture the (very long) BuildKit output; show it only if the command fails.
    $prev = $ErrorActionPreference; $ErrorActionPreference = 'Continue'
    $out = & docker @a 2>&1 | ForEach-Object { "$_" }
    $code = $LASTEXITCODE
    $ErrorActionPreference = $prev
    if ($code -ne 0) { $out | Out-Host }
    return $code
  }
  & docker @a | Out-Host
  return $LASTEXITCODE
}

$script:Recreate = $false
# Set by menu option 4: the user already has the app open, so do not open a new tab.
$script:SkipOpen = $false

function Start-Stack {
if (-not $NoPull) {
  $prevEap = $ErrorActionPreference
  try {
    # git writes progress/info to stderr; do not let that abort the script under 'Stop'.
    $ErrorActionPreference = 'Continue'
    $branch = (git -C $src rev-parse --abbrev-ref HEAD).Trim()
    # .worktrees/ holds other sessions' worktrees; it is expected and must not count as "dirty".
    $pathspec = @('--', '.', ':(exclude).worktrees')
    $dirty = [bool](git -C $src status --porcelain @pathspec)
    $ready = $true
    if ($branch -ne 'dev' -or $dirty) {
      $why = if ($branch -ne 'dev') { "on branch '$branch', not dev" } else { 'uncommitted changes' }
      $msg = "dev-up auto-stash $(Get-Date -Format 'yyyy-MM-dd HH:mm') ($why; was on $branch)"
      if ($dirty) {
        git -C $src stash push --include-untracked -m $msg @pathspec
        if ($LASTEXITCODE) { Write-Host 'Could not stash your changes: leaving everything as-is and skipping the pull.' -ForegroundColor Yellow; $ready = $false }
        else {
          Write-Host "Your uncommitted changes were stashed (nothing was deleted): $msg" -ForegroundColor Yellow
          Write-Host "  Get them back later with: git stash pop   (on branch '$branch' if you want them there)" -ForegroundColor Yellow
        }
      }
      if ($ready -and $branch -ne 'dev') {
        git -C $src checkout dev
        if ($LASTEXITCODE) { Write-Host "Could not switch to dev: using the checkout as-is." -ForegroundColor Yellow; $ready = $false }
        else { Write-Host "Switched from '$branch' to dev (branch '$branch' is untouched)." -ForegroundColor Yellow }
      }
    }
    if ($ready) {
      $env:GIT_HTTP_LOW_SPEED_LIMIT = '1000'; $env:GIT_HTTP_LOW_SPEED_TIME = '8'
      git -C $src pull --ff-only origin dev
      if ($LASTEXITCODE) { Write-Host 'git pull failed (offline?): using the local checkout as-is.' }
    }
  } catch { Write-Host "Skipping git pull ($($_.Exception.Message)); using the local checkout as-is." }
  finally { $ErrorActionPreference = $prevEap }
}

New-Item -ItemType Directory -Force -Path $stack, "$stack\library", "$stack\app-data" | Out-Null
$firstRun = -not (Test-Path $envFile)
if ($firstRun) {
  $bytes = New-Object byte[] 36; [Security.Cryptography.RandomNumberGenerator]::Create().GetBytes($bytes)
  $token = [Convert]::ToBase64String($bytes).Replace('+', 'A').Replace('/', 'B').Replace('=', '')
  @"
FILECANOPY_IMAGE_PREFIX=filecanopy
FILECANOPY_VERSION=dev
FILECANOPY_HTTP_PORT=$Port
FILECANOPY_LIBRARY_PATH=./library
FILECANOPY_APP_DATA_PATH=./app-data
FILECANOPY_SETUP_TOKEN=$token
"@ | Set-Content -Encoding ascii $envFile
}
$token = ((Get-Content $envFile) | Where-Object { $_ -like 'FILECANOPY_SETUP_TOKEN=*' }) -replace '^[^=]*=', ''
$port = ((Get-Content $envFile) | Where-Object { $_ -like 'FILECANOPY_HTTP_PORT=*' }) -replace '^[^=]*=', ''

if ($Tailscale) {
  @"
services:
  web:
    ports: !override
      - "${Tailscale}:${port}:8080"
      - "127.0.0.1:${port}:8080"
  api:
    environment:
      FILECANOPY_PUBLIC_URL: http://${Tailscale}:${port}
      FILECANOPY_COOKIE_SECURE: "false"
"@ | Set-Content -Encoding ascii $override
} elseif (Test-Path $override) { Remove-Item $override }

Write-Host 'Building... (first build is slow; full build log is shown only if it fails)'
if ((Compose @('build') -Quiet) -ne 0) {
  $have = docker image ls --format '{{.Repository}}:{{.Tag}}' | Where-Object { $_ -in 'filecanopy-api:dev', 'filecanopy-web:dev', 'filecanopy-postgres:dev' }
  if (@($have).Count -lt 3) { Write-Host 'Build failed and no earlier build exists. The first build needs internet.' -ForegroundColor Red; return }
  Write-Host 'Build failed (offline?): starting the previously built images instead.' -ForegroundColor Yellow
}
$upArgs = @('up', '-d', '--wait')
if ($script:Recreate) { $upArgs += '--force-recreate' }
if ((Compose $upArgs) -ne 0) { Write-Host 'Start failed. Logs: docker compose -p filecanopy-dev logs api' -ForegroundColor Red; return }

$url = "http://localhost:$port"
Write-Host ''
Write-Host "FileCanopy (dev build) is running at $url" -ForegroundColor Green
if ($firstRun) {
  Write-Host "First run: open the URL and enter this setup token to create your admin account:" -ForegroundColor Cyan
  Write-Host "  $token" -ForegroundColor Cyan
} else {
  Write-Host "Setup token (only needed until the first admin is created): $token"
}
Write-Host "Keep this window open while you test. Use the menu below to restart fresh (2), apply updates (4) or shut down (3)."
if ($script:SkipOpen) {
  Write-Host 'Updated. Refresh your open FileCanopy browser tab (Ctrl+F5) to load the new version.' -ForegroundColor Cyan
} elseif (-not $NoOpen) { Start-Process $url }
}

function Stop-Stack {
  if (Test-Path $envFile) { Compose @('down') | Out-Null }
  Write-Host 'Test app stopped (data kept).'
}

function Wipe-Stack {
  if (-not $Yes) {
    Write-Host "This DELETES the test stack: containers, database, secrets, and the folder $stack (including its library and app-data)." -ForegroundColor Yellow
    if ((Read-Host 'Type YES to continue') -cne 'YES') { Write-Host 'Cancelled; nothing deleted.'; return $false }
  }
  if (Test-Path $envFile) { Compose @('down', '-v', '--remove-orphans') | Out-Null }
  if (Test-Path $stack) { Remove-Item -Recurse -Force $stack }
  Write-Host 'Test stack wiped.'
  return $true
}

switch ($Action) {
  'up'   { Start-Stack; exit 0 }
  'restart' { $script:Recreate = $true; Start-Stack; exit 0 }
  'down' { Stop-Stack; exit 0 }
  'wipe' { Wipe-Stack | Out-Null; exit 0 }
}

# ---- interactive menu: stays open while the app runs ----
while ($true) {
  $script:SkipOpen = $false
  Write-Host ''
  Write-Host '=== FileCanopy test app (built from local dev source) ===' -ForegroundColor Green
  Write-Host '  1  Run it as you left it (keeps your data)'
  Write-Host '  2  Wipe everything and start completely fresh (test first-time setup)'
  Write-Host '  3  Quit (shuts the app down, keeps data)'
  Write-Host '  4  Restart and apply updates (pulls dev, rebuilds, recreates containers, keeps data)'
  $c = Read-Host 'Choose 1, 2, 3 or 4'
  switch ($c) {
    '1' { $script:Recreate = $false; Start-Stack }
    '2' { if (Wipe-Stack) { $script:Recreate = $false; Start-Stack } }
    '3' { Stop-Stack; exit 0 }
    '4' { $script:Recreate = $true; $script:SkipOpen = $true; Start-Stack }
    default { Write-Host 'Please type 1, 2, 3 or 4.' }
  }
}
