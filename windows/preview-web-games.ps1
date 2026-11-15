param([switch]$NoWindow, [switch]$SkipBuild)
$ErrorActionPreference = 'Stop'
$repo = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$previewRoot = Join-Path $env:LOCALAPPDATA 'PSP-Dock-SOCOM-Preview'
# Separate from both the installed Dock and the existing aesthetic preview.
$previewData = Join-Path $previewRoot 'datos-juegos-v2'
$metadataFile = Join-Path $previewData 'server.json'
$script = Join-Path $repo 'dock.py'
$project = Join-Path $PSScriptRoot 'WebDockShell/PspDockWebShell.csproj'
$output = Join-Path $repo 'dist/preview-web-shell-start-select/win-x64'
$exe = Join-Path $output 'PspDockWebShell.exe'
if (-not $SkipBuild) {
    $dotnet = Join-Path $env:ProgramFiles 'dotnet/dotnet.exe'
    New-Item -ItemType Directory -Force -Path (Join-Path $PSScriptRoot 'WebDockShell/obj') | Out-Null
    & $dotnet publish $project -c Release -r win-x64 --self-contained true -p:PublishSingleFile=true -p:PublishTrimmed=false -p:IncludeNativeLibrariesForSelfExtract=true -o $output
    if ($LASTEXITCODE -ne 0) { throw 'Falló la compilación local de WebView2.' }
}
if (-not (Test-Path -LiteralPath $exe)) { throw 'Falta el binario local; ejecuta sin -SkipBuild.' }
New-Item -ItemType Directory -Force -Path $previewData | Out-Null
$previewUrl = $null
if (Test-Path -LiteralPath $metadataFile) {
    try {
        $metadata = Get-Content -LiteralPath $metadataFile -Raw | ConvertFrom-Json
        $previewProcess = Get-CimInstance Win32_Process -Filter "ProcessId=$([int]$metadata.pid)"
        if ($metadata.demo -eq $true -and $metadata.preview_web_ports -eq $true -and
            $previewProcess.CommandLine.Contains($previewData) -and
            $previewProcess.CommandLine.Contains($script) -and
            $previewProcess.CommandLine.Contains('--preview-web-ports')) {
            $candidate = "http://127.0.0.1:$([int]$metadata.port)/"
            $health = Invoke-RestMethod ($candidate + 'api/health') -TimeoutSec 3
            if ($health.product -eq 'psp-dock' -and $health.demo -eq $true -and
                $health.preview_web_ports -eq $true) { $previewUrl = $candidate }
        }
    } catch { # Only this isolated demo's stale record may be superseded.
    }
}
if (-not $previewUrl) {
    $python = (Get-Command python -ErrorAction Stop).Source
    $arguments = @('-u', ('"' + $script + '"'), '--demo', '--preview-web-ports',
        '--no-browser', '--port', '0', '--data', ('"' + $previewData + '"'))
    $backend = Start-Process -FilePath $python -ArgumentList $arguments -WorkingDirectory $repo -WindowStyle Hidden -PassThru `
        -RedirectStandardOutput (Join-Path $previewRoot 'web-games.stdout.log') `
        -RedirectStandardError (Join-Path $previewRoot 'web-games.stderr.log')
    $deadline = [DateTime]::UtcNow.AddSeconds(30)
    while ([DateTime]::UtcNow -lt $deadline) {
        if ($backend.HasExited) { throw 'El servidor de prueba terminó; consulta web-games.stderr.log.' }
        if (Test-Path -LiteralPath $metadataFile) {
            try {
                $metadata = Get-Content -LiteralPath $metadataFile -Raw | ConvertFrom-Json
                if ($metadata.pid -eq $backend.Id -and $metadata.preview_web_ports -eq $true) {
                    $candidate = "http://127.0.0.1:$([int]$metadata.port)/"
                    $health = Invoke-RestMethod ($candidate + 'api/health') -TimeoutSec 2
                    if ($health.demo -eq $true -and $health.preview_web_ports -eq $true) {
                        $previewUrl = $candidate
                        break
                    }
                }
            } catch { }
        }
        Start-Sleep -Milliseconds 200
    }
    if (-not $previewUrl) { throw 'El servidor local no confirmó la puerta experimental.' }
}
Write-Output "PREVIEW_URL=$previewUrl"
Write-Output "PREVIEW_EXE=$exe"
Write-Output "PREVIEW_DATA=$previewData"
if (-not $NoWindow) {
    # Explicit interactive window requested by this preview command.
    Start-Process -FilePath $exe -ArgumentList @($previewUrl, '--preview-web-ports') -WorkingDirectory $repo
}
