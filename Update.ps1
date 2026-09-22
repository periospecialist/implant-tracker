# Replaces only application files. Client settings and the offline queue remain
# in %LOCALAPPDATA%\ImplantInventory and are never deleted by this script.
[CmdletBinding()]
param(
    [string]$Source = 'P:\Implant_Inventory_Application\dist\ImplantInventory',
    [string]$Destination = (Join-Path $env:LOCALAPPDATA 'ImplantInventory\App'),
    [int]$ProcessId = 0,
    [string]$Launch = 'ImplantInventory.exe',
    [string]$ConfigPath = ''
)

$ErrorActionPreference = 'Stop'
$Source = [IO.Path]::GetFullPath($Source.Trim().Trim('"'))
$Destination = [IO.Path]::GetFullPath($Destination.Trim().Trim('"'))
if (-not (Test-Path -LiteralPath (Join-Path $Source 'ImplantInventory.exe'))) {
    throw "The update source is incomplete: $Source"
}
$SourceRoot = $Source.TrimEnd('\')
$DestinationRoot = $Destination.TrimEnd('\')
if (($SourceRoot -ieq $DestinationRoot) -or
    $DestinationRoot.StartsWith($SourceRoot + '\', [StringComparison]::OrdinalIgnoreCase) -or
    $SourceRoot.StartsWith($DestinationRoot + '\', [StringComparison]::OrdinalIgnoreCase)) {
    throw 'The update source and destination must be separate folders.'
}
if ($Destination -eq [IO.Path]::GetPathRoot($Destination)) {
    throw 'A drive root cannot be used as the application destination.'
}

if ($ProcessId -gt 0) {
    Write-Host 'Waiting for Implant Inventory to close...'
    Wait-Process -Id $ProcessId -ErrorAction SilentlyContinue
}

$Parent = Split-Path $Destination
$Name = Split-Path $Destination -Leaf
$Staging = Join-Path $Parent ($Name + '.new')
$Backup = Join-Path $Parent ($Name + '.previous')
New-Item -Path $Parent -ItemType Directory -Force | Out-Null
Remove-Item -LiteralPath $Staging -Recurse -Force -ErrorAction SilentlyContinue
Remove-Item -LiteralPath $Backup -Recurse -Force -ErrorAction SilentlyContinue
New-Item -Path $Staging -ItemType Directory -Force | Out-Null
Copy-Item -Path (Join-Path $Source '*') -Destination $Staging -Recurse -Force

try {
    if (Test-Path -LiteralPath $Destination) {
        Move-Item -LiteralPath $Destination -Destination $Backup
    }
    Move-Item -LiteralPath $Staging -Destination $Destination
} catch {
    Remove-Item -LiteralPath $Staging -Recurse -Force -ErrorAction SilentlyContinue
    if ((-not (Test-Path -LiteralPath $Destination)) -and (Test-Path -LiteralPath $Backup)) {
        Move-Item -LiteralPath $Backup -Destination $Destination
    }
    throw
}
Remove-Item -LiteralPath $Backup -Recurse -Force -ErrorAction SilentlyContinue

$InstalledExecutable = Join-Path $Destination $Launch
$Shell = New-Object -ComObject WScript.Shell
$Shortcut = $Shell.CreateShortcut((Join-Path ([Environment]::GetFolderPath('Desktop')) 'Implant Inventory.lnk'))
$Shortcut.TargetPath = $InstalledExecutable
$Shortcut.WorkingDirectory = $Destination
$Shortcut.Save()

Write-Host 'Implant Inventory was updated successfully.' -ForegroundColor Green
if ($Launch) {
    if ($ConfigPath) {
        Start-Process -FilePath $InstalledExecutable -ArgumentList @('--config', ('"' + $ConfigPath + '"')) -WorkingDirectory $Destination
    } else {
        Start-Process -FilePath $InstalledExecutable -WorkingDirectory $Destination
    }
}
