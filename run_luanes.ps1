# ===========================================================================
# run_luanes.ps1 - LuaNES + bridge.lua で ROM を起動する
#   使い方: project フォルダで  .\run_luanes.ps1
#           .\run_luanes.ps1 -Slideshow     （第3フェーズのスライドショー ROM。= -Rom build\phase3_slideshow.nes）
#           .\run_luanes.ps1 -Rom build\phase1.nes -LuaNES D:\hobby\nes\LuaNES -Love "C:\Program Files\LOVE\love.exe"
#   前提  : LÖVE 11.x がインストール済み、LuaNES を git clone 済み（docs\LUANES.md 参照）
#   ※ このファイルは UTF-8（BOM 付き）で保存すること
# ===========================================================================
param(
    [string]$Rom = 'build\phase1.nes',
    [string]$LuaNES = (Join-Path $PSScriptRoot '..\LuaNES'),
    [string]$Love = '',
    [string]$BridgeDir = (Join-Path $PSScriptRoot 'build\bridge'),
    [switch]$Slideshow
)
$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
if ($Slideshow) { $Rom = 'build\phase3_slideshow.nes' }

function Find-Love {
    param([string]$Given)
    if ($Given) { return $Given }
    foreach ($name in 'lovec', 'love') {
        $cmd = Get-Command $name -ErrorAction SilentlyContinue
        if ($cmd) { return $cmd.Source }
    }
    foreach ($p in "$env:ProgramFiles\LOVE\love.exe", "${env:ProgramFiles(x86)}\LOVE\love.exe") {
        if (Test-Path -LiteralPath $p) { return $p }
    }
    return $null
}

# --- LÖVE ---------------------------------------------------------------
$LoveExe = Find-Love $Love
if (-not $LoveExe -or -not (Test-Path -LiteralPath $LoveExe)) {
    Write-Host '[ERROR] LÖVE (love.exe) が見つかりません。' -ForegroundColor Red
    Write-Host '        https://love2d.org/ から 11.x をインストールするか、-Love でパスを指定してください。'
    exit 1
}

# --- LuaNES -------------------------------------------------------------
if (-not (Test-Path -LiteralPath (Join-Path $LuaNES 'nes.lua'))) {
    Write-Host "[ERROR] LuaNES が見つかりません: $LuaNES" -ForegroundColor Red
    Write-Host '        次のコマンドで取得してください:'
    Write-Host '        git clone https://github.com/nico-abram/LuaNES.git D:\hobby\nes\LuaNES'
    exit 1
}
$LuaNES = (Resolve-Path -LiteralPath $LuaNES).Path

# --- ROM（無ければビルド） ----------------------------------------------
if (-not (Test-Path -LiteralPath $Rom)) {
    if ($Rom -eq 'build\phase1.nes') {
        Write-Host 'ROM が無いのでビルドします...'
        & (Join-Path $PSScriptRoot 'build.ps1')
        if ($LASTEXITCODE -ne 0) { exit 1 }
    } elseif ($Rom -eq 'build\phase3_slideshow.nes') {
        Write-Host 'ROM が無いのでビルドします（assets\slides の画像 + テストページ）...'
        & (Join-Path $PSScriptRoot 'build_slideshow.ps1')
        if ($LASTEXITCODE -ne 0) { exit 1 }
    } else {
        Write-Host "[ERROR] ROM が見つかりません: $Rom" -ForegroundColor Red
        exit 1
    }
}
$RomPath = (Resolve-Path -LiteralPath $Rom).Path

New-Item -ItemType Directory -Force -Path $BridgeDir | Out-Null
$BridgeDir = (Resolve-Path -LiteralPath $BridgeDir).Path

# --- 起動 ---------------------------------------------------------------
$env:LUANES_DIR = $LuaNES
$env:NES_BRIDGE_DIR = $BridgeDir
$env:NES_BRIDGE_LUA = Join-Path $PSScriptRoot 'tools\emu_bridge\bridge.lua'
$HostDir = Join-Path $PSScriptRoot 'tools\luanes_host'

Write-Host "LÖVE   : $LoveExe"
Write-Host "LuaNES : $LuaNES"
Write-Host "ROM    : $RomPath"
Write-Host "Bridge : $BridgeDir"
Write-Host 'キー操作: W/A/S/D=十字  O=A  P=B  I=SELECT  Enter=START'
& $LoveExe $HostDir $RomPath
exit $LASTEXITCODE
