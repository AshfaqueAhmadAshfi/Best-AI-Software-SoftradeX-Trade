# =====================================================================
#  Copies the EA bridge into every MetaTrader 5 terminal found on this PC
#  Usage:  powershell -ExecutionPolicy Bypass -File tools\install_ea.ps1
# =====================================================================
$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
$src  = Join-Path $root 'ea'

if (-not (Test-Path $src)) { throw "ea folder not found: $src" }

$roaming = Join-Path $env:APPDATA 'MetaQuotes\Terminal'
if (-not (Test-Path $roaming)) { throw "No MetaQuotes terminal folder found: $roaming" }

$installed = 0
Get-ChildItem $roaming -Directory | ForEach-Object {
    $mql = Join-Path $_.FullName 'MQL5'
    if (-not (Test-Path $mql)) { return }
    $dest = Join-Path $mql 'Experts\ExnessDashboard'
    New-Item -ItemType Directory -Force -Path $dest | Out-Null

    Copy-Item (Join-Path $src 'DashboardBridge.mqh') $dest -Force
    Copy-Item (Join-Path $src 'DashboardBridge.mq5') $dest -Force
    Write-Host "  copied -> $dest"

    # try to compile with the matching MetaEditor
    $origin = Join-Path $_.FullName 'origin.txt'
    $termDir = if (Test-Path $origin) { (Get-Content $origin -Raw).Trim() } else { '' }
    $editor = if ($termDir) { Join-Path $termDir 'MetaEditor64.exe' } else { '' }
    if ($editor -and (Test-Path $editor)) {
        $log = Join-Path $env:TEMP 'exdash_compile.log'
        & $editor "/compile:$dest\DashboardBridge.mq5" "/log:$log" | Out-Null
        Start-Sleep -Milliseconds 800
        if (Test-Path (Join-Path $dest 'DashboardBridge.ex5')) {
            Write-Host "  compiled OK -> DashboardBridge.ex5"
            $installed++
        } else {
            Write-Host "  compile failed - open the file in MetaEditor and press F7"
        }
    } else {
        Write-Host "  MetaEditor not found - open DashboardBridge.mq5 in MetaEditor and press F7"
        $installed++
    }
}

if ($installed -eq 0) { Write-Host "No MQL5 terminal folder was found." }
else { Write-Host "`nDone. In MT5: Tools > Options > Expert Advisors > allow WebRequest for http://127.0.0.1:8777" }
