param(
    [switch]$Install,
    [string]$Runtime = 'win-x64'
)

$ErrorActionPreference = 'Stop'
$root = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$project = Join-Path $PSScriptRoot 'WebDockShell/PspDockWebShell.csproj'
$output = Join-Path $root "dist/web-shell/$Runtime"

& dotnet publish $project -c Release -r $Runtime --self-contained true -p:PublishSingleFile=true -p:PublishTrimmed=false -p:IncludeNativeLibrariesForSelfExtract=true -o $output
if ($LASTEXITCODE -ne 0) { throw 'Falló la compilación de WebView2.' }
$exe = Join-Path $output 'PspDockWebShell.exe'
if (-not (Test-Path -LiteralPath $exe)) { throw 'No se encontró PspDockWebShell.exe' }

if ($Install) {
    if (-not $env:LOCALAPPDATA) { throw 'Falta LOCALAPPDATA para instalar la ventana WebView2.' }
    $destination = Join-Path $env:LOCALAPPDATA 'PSP-Dock/runtime/web-shell'
    New-Item -ItemType Directory -Force -Path $destination | Out-Null
    # Copy all publish dependencies. Never touch PSP storage, PPSSPP, or saves.
    Copy-Item -Path (Join-Path $output '*') -Destination $destination -Recurse -Force
    Write-Output "INSTALLED=$(Join-Path $destination 'PspDockWebShell.exe')"
    Write-Output 'PSP Dock usará automáticamente WebView2 en el siguiente arranque.'
}
else {
    Write-Output "PUBLISHED=$exe"
    Write-Output 'Para activarlo, ejecuta de nuevo con -Install o establece PSP_DOCK_WEBVIEW2_SHELL.'
}
