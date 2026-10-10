[CmdletBinding()]
param(
    [int]$ApiPort = 8765,
    [int]$WebPort = 5173,
    [switch]$SkipApi,
    [switch]$SkipInstall
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
$Web = Join-Path $Root "web"

if (-not (Test-Path (Join-Path $Web "package.json"))) {
    throw "web/package.json을 찾을 수 없습니다."
}

if (-not $SkipApi) {
    $env:PYTHONPATH = Join-Path $Root "src"
    $python = Get-Command python -ErrorAction SilentlyContinue
    if ($python) {
        Write-Host "[SPIDER] API : http://127.0.0.1:$ApiPort" -ForegroundColor DarkCyan
        Start-Process -FilePath $python.Source `
            -ArgumentList @("-m", "uvicorn", "d4d_web.api:app", "--host", "127.0.0.1", "--port", "$ApiPort", "--reload") `
            -WorkingDirectory $Root -WindowStyle Hidden
    }
    else {
        Write-Warning "Python을 찾지 못했습니다. 프론트엔드만 시작합니다."
    }
}

$env:SPIDER_API = "http://127.0.0.1:$ApiPort"
Push-Location $Web
try {
    if (-not $SkipInstall -and -not (Test-Path (Join-Path $Web "node_modules"))) {
        Write-Host "[SPIDER] 프론트엔드 의존성 설치" -ForegroundColor DarkCyan
        npm install
    }
    Write-Host "[SPIDER] WEB : http://127.0.0.1:$WebPort" -ForegroundColor Cyan
    npm run dev -- --port $WebPort
}
finally {
    Pop-Location
}
