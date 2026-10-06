# Builds the Vast gv worker image with Docker Desktop and pushes them to
# ghcr.io/theoduras. Run in PowerShell:  .\build.ps1
# Asks for the GitHub token (classic, write:packages) unless GHCR_TOKEN is set.
$ErrorActionPreference = 'Stop'
$token = $env:GHCR_TOKEN
if (-not $token) { $token = Read-Host 'GitHub token (write:packages)' }
$token | docker login ghcr.io -u theoduras --password-stdin
if ($LASTEXITCODE) { throw 'docker login failed' }

$base = 'https://raw.githubusercontent.com/Theoduras/ai-model-chat/develop/infra/vast/images'
$dir = Join-Path $env:USERPROFILE 'vast-build'
New-Item -ItemType Directory -Force $dir | Out-Null
Set-Location $dir
foreach ($f in 'gv.Dockerfile', 'gv_steps.py') {
    Invoke-WebRequest "$base/$f" -OutFile $f -UseBasicParsing
}

$images = if ($env:IMAGES) { $env:IMAGES -split ' ' } else { 'gv' }
foreach ($name in $images) {
    $tag = "ghcr.io/theoduras/vast-${name}:latest"
    Write-Host "building $name" -ForegroundColor Cyan
    docker build --progress=plain -f "$name.Dockerfile" -t $tag .
    if ($LASTEXITCODE) { throw "FAILED building $name" }
    Write-Host "pushing $name" -ForegroundColor Cyan
    docker push $tag
    if ($LASTEXITCODE) { throw "FAILED pushing $name" }
    docker image rm $tag | Out-Null
    docker builder prune -af | Out-Null
    Write-Host "done $name" -ForegroundColor Green
}
Write-Host 'ALL DONE' -ForegroundColor Green
