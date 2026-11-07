<#
Run a test stack from the dev branch source instead of the published ghcr images.
Pulls dev, builds the api/web/postgres images locally (tagged filecanopy-*:dev), and starts them.
Keeps your stack's compose.override.yaml and all volumes; never runs `down -v`.
Usage: pwsh scripts/dev-up.ps1 [-Stack D:\development\test-fc2] [-NoPull]
#>
param(
  [string]$Stack = 'D:\development\test-fc2',
  [switch]$NoPull
)
$ErrorActionPreference = 'Stop'
$src = (Resolve-Path "$PSScriptRoot\..").Path

if (-not $NoPull) {
  if (git -C $src status --porcelain) { throw "Uncommitted changes in $src; commit/stash them or pass -NoPull." }
  git -C $src checkout dev
  git -C $src pull --ff-only origin dev
}

$env:FILECANOPY_SRC = $src
$env:FILECANOPY_IMAGE_PREFIX = 'filecanopy'
$env:FILECANOPY_VERSION = 'dev'

$files = @('-f', "$Stack\compose.yaml")
if (Test-Path "$Stack\compose.override.yaml") { $files += @('-f', "$Stack\compose.override.yaml") }
$files += @('-f', "$src\deploy\contrib\build.yaml")

docker compose @files build
if ($LASTEXITCODE) { exit $LASTEXITCODE }
docker compose @files up -d --wait
exit $LASTEXITCODE
