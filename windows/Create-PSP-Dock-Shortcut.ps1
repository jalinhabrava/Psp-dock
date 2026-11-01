param(
    [ValidateSet('Install', 'Status', 'Remove')]
    [string]$Action = 'Install',
    [string]$Pythonw = ''
)
$ErrorActionPreference = 'Stop'
$repo = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '..')).Path
$launcher = Join-Path $repo 'start_dock.py'
$desktop = [Environment]::GetFolderPath('Desktop')
$destination = Join-Path $desktop 'PSP Dock.lnk'
$marker = 'PSP Dock - inicio manual'
$shell = New-Object -ComObject WScript.Shell
$present = Test-Path -LiteralPath $destination
$old = if ($present) { $shell.CreateShortcut($destination) } else { $null }
if ($present -and $old.Description -ne $marker) {
    throw 'Ya existe otro acceso PSP Dock. No se modifica.'
}
if ($Action -eq 'Status') {
    [pscustomobject]@{installed=$present;shortcut=$destination;target=$(if($old){$old.TargetPath}else{''})} | ConvertTo-Json
    exit 0
}
if ($Action -eq 'Remove') {
    if ($present) { Remove-Item -LiteralPath $destination }
    [pscustomobject]@{removed=$present;shortcut=$destination} | ConvertTo-Json
    exit 0
}
if (-not (Test-Path -LiteralPath $launcher -PathType Leaf)) {
    throw 'No se encuentra start_dock.py.'
}
if (-not $Pythonw) {
    $Pythonw = (& python -c 'import sys;from pathlib import Path;print(Path(sys.executable).with_name("pythonw.exe"))').Trim()
    if ($LASTEXITCODE -ne 0) { throw 'No se pudo localizar Python para Windows.' }
}
$Pythonw = (Resolve-Path -LiteralPath $Pythonw).Path
if ([IO.Path]::GetFileName($Pythonw) -ine 'pythonw.exe') {
    throw 'Selecciona el pythonw.exe de tu instalacion de Python.'
}
# Keep the original approved thumbnail pixel-for-pixel. ICO is only a
# container for the existing PNG; this does not redraw or resize the image.
$source = Join-Path $repo 'homebrew\composite-mscdemo\assets\ICON0.PNG'
$png = [IO.File]::ReadAllBytes($source)
$width = ([int]$png[16] -shl 24) -bor ([int]$png[17] -shl 16) -bor ([int]$png[18] -shl 8) -bor [int]$png[19]
$height = ([int]$png[20] -shl 24) -bor ([int]$png[21] -shl 16) -bor ([int]$png[22] -shl 8) -bor [int]$png[23]
if ($png.Length -lt 33 -or $png[0] -ne 137 -or $png[1] -ne 80 -or $width -ne 144 -or $height -ne 80) {
    throw 'El thumbnail no corresponde al recurso aprobado de PSP Dock.'
}
$icon = Join-Path $PSScriptRoot 'PSP-Dock.ico'
$memory = [IO.MemoryStream]::new()
$writer = [IO.BinaryWriter]::new($memory)
try {
    $writer.Write([uint16]0); $writer.Write([uint16]1); $writer.Write([uint16]1)
    $writer.Write([byte]$width); $writer.Write([byte]$height)
    $writer.Write([byte]0); $writer.Write([byte]0)
    $writer.Write([uint16]1); $writer.Write([uint16]24)
    $writer.Write([uint32]$png.Length); $writer.Write([uint32]22)
    $writer.Write($png)
    $writer.Flush()
    $iconBytes = $memory.ToArray()
} finally {
    $writer.Dispose()
    $memory.Dispose()
}
[IO.File]::WriteAllBytes($icon, $iconBytes)
Add-Type -AssemblyName System.Drawing
$checkedIcon = [Drawing.Icon]::new($icon)
try {
    if ($checkedIcon.Width -ne 144 -or $checkedIcon.Height -ne 80) {
        throw 'Windows no reconoce el icono de PSP Dock.'
    }
} finally { $checkedIcon.Dispose() }
$arguments = '"' + $launcher + '"'
if ($present -and ($old.TargetPath -ine $Pythonw -or $old.Arguments -ne $arguments)) {
    $backup = Join-Path $repo ('data\windows-manual-entry\shortcut-before-' + (Get-Date -Format 'yyyyMMdd-HHmmss') + '.lnk')
    New-Item -ItemType Directory -Force -Path (Split-Path $backup) | Out-Null
    Copy-Item -LiteralPath $destination -Destination $backup
}
$link = $shell.CreateShortcut($destination)
$link.TargetPath = $Pythonw
$link.Arguments = $arguments
$link.WorkingDirectory = $repo
$link.Description = $marker
$link.IconLocation = $icon + ',0'
$link.WindowStyle = 1
$link.Save()
$check = $shell.CreateShortcut($destination)
if ($check.TargetPath -ine $Pythonw -or $check.Arguments -ne $arguments -or $check.Description -ne $marker) {
    throw 'No se pudo verificar el acceso creado.'
}
[pscustomobject]@{
    kind='manual_desktop_shortcut'
    shortcut=$destination
    target=$check.TargetPath
    arguments=$check.Arguments
    icon=$check.IconLocation
    startup_registered=$false
    service_installed=$false
} | ConvertTo-Json
