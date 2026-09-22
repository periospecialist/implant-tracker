# Guided per-user host installation from the host release ZIP or a local build.
$ErrorActionPreference = 'Stop'
$BuildFolder = Join-Path $PSScriptRoot 'dist\InventoryServer'
$BuiltExecutable = Join-Path $BuildFolder 'InventoryServer.exe'
if (-not (Test-Path -LiteralPath $BuiltExecutable)) {
    throw 'This package is incomplete. Extract the complete InventoryServer-Windows.zip, or run BUILD_WINDOWS.cmd from the source project, then try again.'
}

$HostRoot = Join-Path $env:LOCALAPPDATA 'ImplantInventoryHost'
$HostApp = Join-Path $HostRoot 'App'
$HostExecutable = Join-Path $HostApp 'InventoryServer.exe'
$Running = Get-Process InventoryServer -ErrorAction SilentlyContinue
if ($Running) {
    throw 'Close the running inventory server windows with Ctrl+C, then run setup again.'
}
New-Item -ItemType Directory -Force -Path $HostApp | Out-Null
Copy-Item -Path (Join-Path $BuildFolder '*') -Destination $HostApp -Recurse -Force

$SuggestedConfig = Join-Path $HostRoot 'server.json'
if ((-not (Test-Path -LiteralPath $SuggestedConfig)) -and (Test-Path -LiteralPath 'C:\ImplantInventoryServer\server.json')) {
    $SuggestedConfig = 'C:\ImplantInventoryServer\server.json'
}
Write-Host 'If you already used this application, select its EXISTING server.json to keep the same database.'
$ConfigInput = Read-Host "Configuration path (Enter for $SuggestedConfig)"
$ConfigPath = if ([string]::IsNullOrWhiteSpace($ConfigInput)) { $SuggestedConfig } else { $ConfigInput.Trim().Trim('"') }
if (-not [IO.Path]::IsPathRooted($ConfigPath)) { throw 'Use a full local configuration path.' }
$ExchangeFolder = (Read-Host 'Shared exchange folder, e.g. \\SERVER\Perio\IMPLANTS\Inventory Exchange').Trim().Trim('"')
if ([string]::IsNullOrWhiteSpace($ExchangeFolder)) { throw 'A shared exchange folder is required.' }

if (Test-Path -LiteralPath $ConfigPath) {
    $Previous = Get-Content -LiteralPath $ConfigPath -Raw | ConvertFrom-Json
    $DatabasePath = if ([IO.Path]::IsPathRooted($Previous.database)) { $Previous.database } else { Join-Path (Split-Path $ConfigPath) $Previous.database }
    if (Test-Path -LiteralPath $DatabasePath) {
        $BackupDirectory = Join-Path $HostRoot 'UpgradeBackups'
        New-Item -ItemType Directory -Force -Path $BackupDirectory | Out-Null
        $BackupPath = Join-Path $BackupDirectory ('inventory-' + (Get-Date -Format 'yyyyMMdd-HHmmss') + '.sqlite3')
        & $HostExecutable --config $ConfigPath --backup $BackupPath
        if ($LASTEXITCODE -ne 0) { throw 'The existing database could not be backed up. Review the error before upgrading.' }
    }
    & $HostExecutable --config $ConfigPath --upgrade --exchange $ExchangeFolder
} else {
    & $HostExecutable --config $ConfigPath --init --exchange $ExchangeFolder
}
if ($LASTEXITCODE -ne 0) { throw 'Host configuration failed; review the error above.' }

$Shell = New-Object -ComObject WScript.Shell
function New-HostShortcut([string]$ShortcutPath) {
    $Shortcut = $Shell.CreateShortcut($ShortcutPath)
    $Shortcut.TargetPath = $HostExecutable
    $Shortcut.Arguments = '--config "' + $ConfigPath + '"'
    $Shortcut.WorkingDirectory = $HostApp
    $Shortcut.Save()
}
New-HostShortcut (Join-Path ([Environment]::GetFolderPath('Desktop')) 'Start Inventory Host.lnk')
$Autostart = Read-Host 'Start the inventory host automatically when you sign in? (Y/N)'
if ($Autostart -match '^[Yy]') {
    New-HostShortcut (Join-Path ([Environment]::GetFolderPath('Startup')) 'Inventory Host.lnk')
}
Write-Host 'Setup complete. Staff PCs should select this same exchange folder in the desktop app.'
Write-Host 'Keep this computer awake and signed in. Locking its screen is fine.'
Start-Process -FilePath $HostExecutable -ArgumentList ('--config "' + $ConfigPath + '"') -WorkingDirectory $HostApp
