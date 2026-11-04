param(
    [string]$Python = 'py',
    [string]$Output = '',
    [switch]$SkipInstall
)

$ErrorActionPreference = 'Stop'
$root = (Resolve-Path (Join-Path $PSScriptRoot '../..')).Path
$work = Join-Path $root 'build/windows-release'
$venv = Join-Path $work '.venv'
$pythonExe = Join-Path $venv 'Scripts/python.exe'
$dist = if ($Output) { $Output } else { Join-Path $root 'dist/windows-release' }
New-Item -ItemType Directory -Force -Path $work, $dist | Out-Null

if (-not (Test-Path -LiteralPath $pythonExe)) {
    & $Python -m venv $venv
    if ($LASTEXITCODE -ne 0) { throw 'Failed to create isolated Python environment' }
}
if (-not $SkipInstall) {
    & $pythonExe -m pip install --disable-pip-version-check 'pyinstaller==6.16.0' 'pillow==11.3.0'
    if ($LASTEXITCODE -ne 0) { throw 'Failed to install PyInstaller' }
}

& $pythonExe -m PyInstaller --noconfirm --clean --onefile --windowed `
    --name 'PSP-Dock' --icon (Join-Path $root 'windows/PSP-Dock.ico') `
    --paths $root --add-data "$(Join-Path $root 'web');web" `
    --add-data "$(Join-Path $root 'texture_catalog_sources.json');." `
    --distpath $dist --workpath (Join-Path $work 'pyinstaller') `
    --specpath $work (Join-Path $PSScriptRoot 'windows_entry.py')
if ($LASTEXITCODE -ne 0) { throw 'PyInstaller failed' }

$exe = Join-Path $dist 'PSP-Dock.exe'
if (-not (Test-Path -LiteralPath $exe)) { throw 'Missing PSP-Dock.exe' }
$asset = Join-Path $dist 'PSP-Dock-Windows-v0.1.3.exe'
Move-Item -Force -LiteralPath $exe -Destination $asset
Get-FileHash -Algorithm SHA256 -LiteralPath $asset | Format-List
Write-Output "RELEASE_ASSET=$asset"
