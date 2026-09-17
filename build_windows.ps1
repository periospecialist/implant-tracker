# Builds the Windows applications. Installs Python 3.12 for the current user when needed.
$ErrorActionPreference = 'Stop'
Set-Location $PSScriptRoot

function Invoke-Checked {
    param([string]$Program, [string[]]$Arguments)
    & $Program @Arguments
    if ($LASTEXITCODE -ne 0) { throw "$Program failed with exit code $LASTEXITCODE" }
}

$PythonProgram = $null
$PythonPrefix = @()
$Launcher = Get-Command 'py.exe' -ErrorAction SilentlyContinue
if ($Launcher) {
    & $Launcher.Source -3.12 -c 'import sys; assert sys.version_info[:2] == (3, 12)'
    if ($LASTEXITCODE -eq 0) {
        $PythonProgram = $Launcher.Source
        $PythonPrefix = @('-3.12')
    }
}
if (-not $PythonProgram) {
    $DirectPython = Get-Command 'python.exe' -ErrorAction SilentlyContinue
    if ($DirectPython) {
        & $DirectPython.Source -c 'import sys; assert sys.version_info[:2] == (3, 12)'
        if ($LASTEXITCODE -eq 0) {
            $PythonProgram = $DirectPython.Source
        }
    }
}
if (-not $PythonProgram) {
    $ExpectedPython = Join-Path $env:LOCALAPPDATA 'Programs\Python\Python312\python.exe'
    if (-not (Test-Path -LiteralPath $ExpectedPython)) {
        $Winget = Get-Command 'winget.exe' -ErrorAction SilentlyContinue
        if (-not $Winget) {
            throw 'Python 3.12 is missing and Windows Package Manager (winget) is unavailable. Install Python 3.12 x64 from python.org, then run this script again.'
        }
        Write-Host 'Python 3.12 is not installed. Installing it for the current Windows user...'
        Invoke-Checked $Winget.Source @('install', '--id', 'Python.Python.3.12', '--exact', '--scope', 'user', '--accept-package-agreements', '--accept-source-agreements', '--silent')
    }
    if (-not (Test-Path -LiteralPath $ExpectedPython)) {
        throw 'Python installation finished but python.exe was not found in the expected per-user folder. Sign out and back in, then run BUILD_WINDOWS.cmd again.'
    }
    $PythonProgram = $ExpectedPython
}

Invoke-Checked $PythonProgram ($PythonPrefix + @('-m', 'venv', '.venv'))
$InventoryPython = Join-Path $PSScriptRoot '.venv\Scripts\python.exe'
Invoke-Checked $InventoryPython @('-m', 'pip', 'install', '-r', 'requirements-build.txt')
Invoke-Checked $InventoryPython @('-m', 'PyInstaller', '--noconfirm', '--clean', '--onedir', '--windowed', '--name', 'ImplantInventory', 'run_desktop.py')
Invoke-Checked $InventoryPython @('-m', 'PyInstaller', '--noconfirm', '--clean', '--onedir', '--console', '--name', 'InventoryServer', 'run_server.py')
Write-Host 'Build complete. Copy each whole folder under dist; keep its _internal folder beside the EXE.'
Write-Host 'Desktop: dist\ImplantInventory\ImplantInventory.exe'
Write-Host 'Server:  dist\InventoryServer\InventoryServer.exe'
