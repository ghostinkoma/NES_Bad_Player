# ===========================================================================
# build.ps1 - 第1フェーズ ROM のビルド（PowerShell 版）
#   使い方: project フォルダで  .\build.ps1
#   出力  : build\phase1.nes（+ デバッグ用 phase1.dbg / phase1.map）
#   ※ このファイルは UTF-8（BOM 付き）で保存すること（Windows PowerShell 5.1 の文字化け対策）
# ===========================================================================
$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot

# cc65 の場所（project フォルダの1つ上に展開してある前提）
$Cc65Bin = Join-Path $PSScriptRoot '..\bin'
$Ca65 = Join-Path $Cc65Bin 'ca65.exe'
$Ld65 = Join-Path $Cc65Bin 'ld65.exe'

if (-not (Test-Path -LiteralPath $Ca65)) {
    Write-Host "[ERROR] ca65.exe が見つかりません: $Ca65" -ForegroundColor Red
    Write-Host '        build.ps1 の $Cc65Bin を cc65 の bin フォルダに合わせてください。'
    exit 1
}

New-Item -ItemType Directory -Force -Path 'build\bridge' | Out-Null

Write-Host '[1/2] アセンブル: nes\src\main.s'
& $Ca65 -g -I nes\src -o build\main.o nes\src\main.s
if ($LASTEXITCODE -ne 0) {
    Write-Host '[ERROR] アセンブルに失敗しました。上のメッセージを確認してください。' -ForegroundColor Red
    exit 1
}

Write-Host '[2/2] リンク: build\phase1.nes'
& $Ld65 -C nes\cfg\unrom.cfg -o build\phase1.nes --dbgfile build\phase1.dbg -m build\phase1.map build\main.o
if ($LASTEXITCODE -ne 0) {
    Write-Host '[ERROR] リンクに失敗しました。上のメッセージを確認してください。' -ForegroundColor Red
    exit 1
}

$size = (Get-Item -LiteralPath 'build\phase1.nes').Length
Write-Host ''
Write-Host "ビルド成功: build\phase1.nes ($size バイト)" -ForegroundColor Green
exit 0
