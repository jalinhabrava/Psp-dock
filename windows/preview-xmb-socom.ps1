param([switch]$NoBrowser)
$ErrorActionPreference = 'Stop'
$repo = Split-Path -Parent $PSScriptRoot
$previewData = Join-Path $env:LOCALAPPDATA 'PSP-Dock-SOCOM-Preview\datos'
$metadataFile = Join-Path $previewData 'server.json'

# Reuse this preview only. Never discover, close or alter an installed Dock.
if (Test-Path -LiteralPath $metadataFile) {
    try {
        $metadata = Get-Content -LiteralPath $metadataFile -Raw | ConvertFrom-Json
        $previewProcess = Get-CimInstance Win32_Process -Filter "ProcessId=$([int]$metadata.pid)"
        if ($metadata.demo -eq $true -and $previewProcess.CommandLine.Contains($previewData) -and
            $previewProcess.CommandLine.Contains('--demo')) {
            $previewUrl = "http://127.0.0.1:$([int]$metadata.port)/"
            $state = Invoke-RestMethod ($previewUrl + 'api/state') -TimeoutSec 3
            if ($state.demo -eq $true) {
                Write-Host "Prueba local: $previewUrl"
                if (-not $NoBrowser) { Start-Process $previewUrl }
                return
            }
        }
    } catch { # A stale preview record can be replaced by the demo server.
    }
}

Push-Location $repo
try {
    $demoArguments = @('-u', 'dock.py', '--demo', '--port', '0', '--data', $previewData)
    if ($NoBrowser) { $demoArguments += '--no-browser' }
    & python @demoArguments
    if ($LASTEXITCODE -ne 0) { throw "No se pudo iniciar la prueba local ($LASTEXITCODE)." }
} finally { Pop-Location }
