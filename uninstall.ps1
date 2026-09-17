# Guided removal for the desktop client and shared-folder inventory host.
[CmdletBinding()]
param(
    [switch]$RemoveSharedExchange,
    [switch]$SkipBackup,
    [switch]$Force
)

$ErrorActionPreference = 'Stop'
$HostRoot = Join-Path $env:LOCALAPPDATA 'ImplantInventoryHost'
$ClientRoot = Join-Path $env:LOCALAPPDATA 'ImplantInventory'
$DefaultConfig = Join-Path $HostRoot 'server.json'
$LegacyConfig = 'C:\ImplantInventoryServer\server.json'
$ConfigPath = if (Test-Path -LiteralPath $DefaultConfig) {
    $DefaultConfig
} elseif (Test-Path -LiteralPath $LegacyConfig) {
    $LegacyConfig
} else {
    $null
}

function Read-YesNo([string]$Prompt, [bool]$Default = $false) {
    $suffix = if ($Default) { ' [Y/n]' } else { ' [y/N]' }
    $answer = (Read-Host ($Prompt + $suffix)).Trim()
    if (-not $answer) { return $Default }
    return $answer -match '^[Yy]'
}

function Resolve-DatabasePath([string]$Path, $Config) {
    if (-not $Config -or -not $Config.database) { return $null }
    if ([IO.Path]::IsPathRooted([string]$Config.database)) {
        return [IO.Path]::GetFullPath([string]$Config.database)
    }
    return [IO.Path]::GetFullPath((Join-Path (Split-Path $Path) ([string]$Config.database)))
}

function Remove-IfPresent([string]$Path) {
    if ($Path -and (Test-Path -LiteralPath $Path)) {
        Remove-Item -LiteralPath $Path -Recurse -Force
        Write-Host "Removed: $Path"
    }
}

$DetectedAppFolders = @()
function Add-DetectedAppFolder([string]$Path) {
    if ($Path -and $DetectedAppFolders -inotcontains $Path) {
        $script:DetectedAppFolders += $Path
    }
}
$RunningClients = @(Get-Process ImplantInventory -ErrorAction SilentlyContinue)
foreach ($ClientProcess in $RunningClients) {
    try {
        if ($ClientProcess.Path) {
            Add-DetectedAppFolder (Split-Path ([IO.Path]::GetFullPath($ClientProcess.Path)))
        }
    } catch { }
}

$Config = $null
$DatabasePath = $null
$ExchangeFolder = $null
if ($ConfigPath) {
    try {
        $Config = Get-Content -LiteralPath $ConfigPath -Raw | ConvertFrom-Json
        $DatabasePath = Resolve-DatabasePath $ConfigPath $Config
        $ExchangeFolder = [string]$Config.exchange_folder
    } catch {
        throw "Could not read the host configuration at $ConfigPath. Nothing was removed. $($_.Exception.Message)"
    }
}

Write-Host ''
Write-Host 'Implant Inventory complete uninstaller' -ForegroundColor Cyan
Write-Host 'This removes this Windows user''s client cache, pending entries, host program,'
Write-Host 'local host database, shortcuts, and InventoryServer startup tasks.'
if ($ExchangeFolder) {
    Write-Host "Shared exchange folder detected: $ExchangeFolder"
}
Write-Host ''

if (-not $Force) {
    $confirmation = Read-Host 'Type UNINSTALL to continue'
    if ($confirmation -cne 'UNINSTALL') {
        Write-Host 'Cancelled. Nothing was removed.'
        exit 0
    }
}

# Stop both executables before copying or deleting database/cache files.
Get-Process InventoryServer, ImplantInventory -ErrorAction SilentlyContinue |
    Stop-Process -Force -ErrorAction Stop

$BackupFolder = $null
if ($DatabasePath -and (Test-Path -LiteralPath $DatabasePath) -and -not $SkipBackup) {
    $makeBackup = $Force -or (Read-YesNo 'Create a final recovery backup on the Desktop?' $true)
    if ($makeBackup) {
        $stamp = Get-Date -Format 'yyyyMMdd-HHmmss'
        $BackupFolder = Join-Path ([Environment]::GetFolderPath('Desktop')) "ImplantInventory-UninstallBackup-$stamp"
        New-Item -ItemType Directory -Force -Path $BackupFolder | Out-Null
        $BackupDatabase = Join-Path $BackupFolder 'inventory.sqlite3'
        $HostExecutable = Join-Path $HostRoot 'App\InventoryServer.exe'
        if (Test-Path -LiteralPath $HostExecutable) {
            & $HostExecutable --config $ConfigPath --backup $BackupDatabase
            if ($LASTEXITCODE -ne 0 -or -not (Test-Path -LiteralPath $BackupDatabase)) {
                throw "The final database backup failed. Nothing else was removed. Backup folder: $BackupFolder"
            }
        } else {
            # The process has been stopped, so a direct copy is consistent.
            Copy-Item -LiteralPath $DatabasePath -Destination $BackupDatabase -Force
        }
        Copy-Item -LiteralPath $ConfigPath -Destination (Join-Path $BackupFolder 'server.json') -Force
        if (Test-Path -LiteralPath $ClientRoot) {
            Copy-Item -LiteralPath $ClientRoot -Destination (Join-Path $BackupFolder 'DesktopCache') -Recurse -Force
        }
        Write-Host "Recovery backup created: $BackupFolder" -ForegroundColor Green
    }
}

# Remove scheduled tasks whose action launches this inventory server.
if (Get-Command Get-ScheduledTask -ErrorAction SilentlyContinue) {
    $InventoryTasks = Get-ScheduledTask -ErrorAction SilentlyContinue | Where-Object {
        $_.Actions | Where-Object {
            $ActionPath = [Environment]::ExpandEnvironmentVariables([string]$_.Execute).Trim('"')
            [IO.Path]::GetFileName($ActionPath) -ieq 'InventoryServer.exe'
        }
    }
    foreach ($Task in $InventoryTasks) {
        try {
            Unregister-ScheduledTask -TaskName $Task.TaskName -TaskPath $Task.TaskPath -Confirm:$false
            Write-Host "Removed scheduled task: $($Task.TaskPath)$($Task.TaskName)"
        } catch {
            Write-Warning "Could not remove scheduled task $($Task.TaskName). Open Task Scheduler as an administrator and remove it manually."
        }
    }
}

$ShortcutPaths = @(
    (Join-Path ([Environment]::GetFolderPath('Desktop')) 'Start Inventory Host.lnk'),
    (Join-Path ([Environment]::GetFolderPath('Desktop')) 'Implant Inventory.lnk'),
    (Join-Path ([Environment]::GetFolderPath('Startup')) 'Inventory Host.lnk'),
    (Join-Path ([Environment]::GetFolderPath('CommonDesktopDirectory')) 'Start Inventory Host.lnk'),
    (Join-Path ([Environment]::GetFolderPath('CommonDesktopDirectory')) 'Implant Inventory.lnk')
)
$ShortcutShell = New-Object -ComObject WScript.Shell
foreach ($Shortcut in $ShortcutPaths | Select-Object -Unique) {
    if (-not (Test-Path -LiteralPath $Shortcut)) { continue }
    try {
        $Target = $ShortcutShell.CreateShortcut($Shortcut).TargetPath
        if ($Target -and ([IO.Path]::GetFileName($Target) -ieq 'ImplantInventory.exe')) {
            Add-DetectedAppFolder (Split-Path ([IO.Path]::GetFullPath($Target)))
        }
    } catch { }
}
foreach ($Shortcut in $ShortcutPaths | Select-Object -Unique) {
    try { Remove-IfPresent $Shortcut } catch { Write-Warning "Could not remove shortcut: $Shortcut" }
}

Remove-IfPresent $ClientRoot
Remove-IfPresent $HostRoot

# Clean an older C:\ImplantInventoryServer installation without deleting an
# unrelated custom parent directory.
if ($ConfigPath -and $ConfigPath -ieq $LegacyConfig) {
    Remove-IfPresent 'C:\ImplantInventoryServer'
} elseif ($ConfigPath -and -not $ConfigPath.StartsWith($HostRoot, [StringComparison]::OrdinalIgnoreCase)) {
    Remove-IfPresent $ConfigPath
    if ($DatabasePath) {
        Remove-IfPresent $DatabasePath
        Remove-IfPresent ($DatabasePath + '-wal')
        Remove-IfPresent ($DatabasePath + '-shm')
        Remove-IfPresent ($DatabasePath + '.processor.lock')
    }
}

$DeleteShared = $RemoveSharedExchange
if (-not $Force -and -not $RemoveSharedExchange -and $ExchangeFolder) {
    $DeleteShared = Read-YesNo 'Also delete the shared exchange folder for every workstation?' $false
}
if ($DeleteShared) {
    if (-not $ExchangeFolder) {
        throw 'The exchange folder could not be found in server.json, so shared files were not removed.'
    }
    if (-not $Force) {
        Write-Host "This will permanently delete: $ExchangeFolder" -ForegroundColor Yellow
        $sharedConfirmation = Read-Host 'Type DELETE SHARED INVENTORY to confirm'
        if ($sharedConfirmation -cne 'DELETE SHARED INVENTORY') {
            Write-Host 'Shared exchange folder retained.'
            $DeleteShared = $false
        }
    }
    if ($DeleteShared) { Remove-IfPresent $ExchangeFolder }
}

foreach ($DetectedFolder in $DetectedAppFolders) {
    if ($Force -or (Read-YesNo "Remove detected desktop application folder $DetectedFolder?" $true)) {
        if (Test-Path -LiteralPath (Join-Path $DetectedFolder 'ImplantInventory.exe')) {
            Remove-IfPresent $DetectedFolder
        }
    }
}

$AdditionalAppFolder = $null
if (-not $Force) {
    $AdditionalAppFolder = (Read-Host 'Optional: paste another ImplantInventory application folder to remove it, or press Enter to skip').Trim().Trim('"')
}
if ($AdditionalAppFolder) {
    $ResolvedAppFolder = [IO.Path]::GetFullPath($AdditionalAppFolder)
    if (-not (Test-Path -LiteralPath (Join-Path $ResolvedAppFolder 'ImplantInventory.exe'))) {
        Write-Warning 'That folder does not contain ImplantInventory.exe, so it was retained.'
    } elseif ($ResolvedAppFolder -eq [IO.Path]::GetPathRoot($ResolvedAppFolder)) {
        Write-Warning 'A drive root cannot be removed.'
    } else {
        Remove-IfPresent $ResolvedAppFolder
    }
}

Write-Host ''
Write-Host 'Uninstall complete for this Windows user.' -ForegroundColor Green
if ($BackupFolder) { Write-Host "Recovery backup retained at: $BackupFolder" }
if (-not $DeleteShared -and $ExchangeFolder) {
    Write-Host "Shared exchange data retained at: $ExchangeFolder"
}
Write-Host 'Python was retained because other programs may use it.'
