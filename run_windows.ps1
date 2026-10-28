# Lanzador de QuantFlow para Windows.
# No requiere permisos de administrador: crea .venv dentro de la carpeta del repo.

$ErrorActionPreference = "Stop"
Set-Location -LiteralPath $PSScriptRoot

function Stop-WithMessage([string]$Message, [string]$Url) {
    Write-Host ""
    Write-Host $Message -ForegroundColor Red
    if ($Url) {
        try { Start-Process $Url } catch { }
    }
    Read-Host "Presiona Enter para cerrar"
    exit 1
}

# ---------------------------------------------------------------- Python
$candidates = @(
    @{ Exe = "py"; Args = @("-3.13") },
    @{ Exe = "py"; Args = @("-3.12") },
    @{ Exe = "py"; Args = @("-3.11") },
    @{ Exe = "py"; Args = @("-3") },
    @{ Exe = "python"; Args = @() }
)

$pythonExe = $null
$pythonArgs = @()

foreach ($candidate in $candidates) {
    if (-not (Get-Command $candidate.Exe -ErrorAction SilentlyContinue)) { continue }

    $argList = @()
    $argList += $candidate.Args
    $argList += @("-c", "import sys; print('%d.%d' % sys.version_info[:2])")

    try {
        $version = & $candidate.Exe @argList 2>$null
    } catch {
        continue
    }

    if ($LASTEXITCODE -eq 0 -and $version -match '^3\.(1[1-9]|[2-9][0-9])$') {
        $pythonExe = $candidate.Exe
        $pythonArgs = $candidate.Args
        break
    }
}

if (-not $pythonExe) {
    Stop-WithMessage "No encontre Python 3.11 o superior. Instalalo desde python.org y volve a intentar." "https://www.python.org/downloads/windows/"
}

Write-Host "Python encontrado: $pythonExe $($pythonArgs -join ' ')" -ForegroundColor Green

# ---------------------------------------------------------------- entorno
$venvPython = Join-Path $PSScriptRoot ".venv\Scripts\python.exe"

if (-not (Test-Path -LiteralPath $venvPython)) {
    Write-Host "Creando entorno virtual..." -ForegroundColor Cyan
    $venvArgs = @()
    $venvArgs += $pythonArgs
    $venvArgs += @("-m", "venv", ".venv")
    & $pythonExe @venvArgs
    if ($LASTEXITCODE -ne 0) {
        Stop-WithMessage "No se pudo crear el entorno virtual."
    }
}

Write-Host "Instalando dependencias (la primera vez tarda unos minutos)..." -ForegroundColor Cyan
& $venvPython -m pip install --disable-pip-version-check -r requirements.txt
if ($LASTEXITCODE -ne 0) {
    Stop-WithMessage "No se pudieron instalar las dependencias. Revisa tu conexion a internet o el antivirus."
}

# ---------------------------------------------------------------- config
if (-not (Test-Path -LiteralPath "config.local.yaml")) {
    Copy-Item -LiteralPath "config.yaml" -Destination "config.local.yaml"
    Write-Host "Cree config.local.yaml. Editalo si queres cambiar la passphrase del webhook." -ForegroundColor Yellow
}

# ---------------------------------------------------------------- arranque
Write-Host ""
Write-Host "Iniciando QuantFlow. Panel: http://127.0.0.1:8000" -ForegroundColor Green
Write-Host "Deja esta ventana abierta. Ctrl+C para detener." -ForegroundColor Green
Write-Host ""

Start-Job -ScriptBlock {
    Start-Sleep -Seconds 5
    Start-Process "http://127.0.0.1:8000"
} | Out-Null

& $venvPython -m quantflow --config config.local.yaml live

if ($LASTEXITCODE -ne 0) {
    Read-Host "QuantFlow se detuvo. Presiona Enter para cerrar"
}
