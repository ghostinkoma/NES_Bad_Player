# ===========================================================================
# run_mesen.ps1 - Mesen で ROM を起動する
#   使い方: project フォルダで
#           .\run_mesen.ps1                              （build\video\movies.nes）
#           .\run_mesen.ps1 -Rom build\phase3_slideshow.nes
#   Mesen.exe は D:\hobby\nes\Mesen.exe（このフォルダの 1 つ上）を使う。-Mesen で変更できる
#   ※ このファイルは UTF-8（BOM 付き）で保存すること
# ===========================================================================
param(
    [string]$Rom = 'build\video\movies.nes',
    [string]$Mesen = ''
)
$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot

if (-not $Mesen) { $Mesen = Join-Path $PSScriptRoot '..\Mesen.exe' }
if (-not (Test-Path -LiteralPath $Mesen)) {
    Write-Host "[ERROR] Mesen.exe が見つかりません: $Mesen（-Mesen で場所を指定してください）" -ForegroundColor Red
    exit 1
}
if (-not (Test-Path -LiteralPath $Rom)) {
    if ($Rom -eq 'build\video\movies.nes') {
        Write-Host 'ROM がまだ無いので、先にビルドします（数分かかります）'
        & (Join-Path $PSScriptRoot 'build_video.ps1')
        if ($LASTEXITCODE -ne 0) { exit 1 }
    } else {
        Write-Host "[ERROR] ROM が見つかりません: $Rom" -ForegroundColor Red
        exit 1
    }
}
Start-Process -FilePath $Mesen -ArgumentList "`"$((Resolve-Path $Rom).Path)`""
Write-Host 'Mesen を起動しました。メニュー: ↑↓ 選ぶ / A 再生 / SELECT 再生モード'
Write-Host '再生中: B 一時停止 / 一時停止中に ←→ コマ戻し・コマ送り / SELECT FPS 表示 / START メニューへ'