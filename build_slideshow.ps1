# ===========================================================================
# build_slideshow.ps1 - 第3フェーズ スライドショー ROM のビルド
#   使い方: project フォルダで
#           .\build_slideshow.ps1                      （assets\slides の画像 + テストページ）
#           .\build_slideshow.ps1 D:\pics\a.png build\converted\cat.npaint.json
#           .\build_slideshow.ps1 assets\slides --no-test   （テストページを付けない）
#           .\build_slideshow.ps1 --help                     （画像まとめツールのオプション一覧）
#   出力  : build\phase3_slideshow.nes（+ build\slides\ に中身の一覧 index.html）
#   ※ このファイルは UTF-8（BOM 付き）で保存すること
# ===========================================================================
$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot

$Cc65Bin = Join-Path $PSScriptRoot '..\bin'
$Ca65 = Join-Path $Cc65Bin 'ca65.exe'
$Ld65 = Join-Path $Cc65Bin 'ld65.exe'
if (-not (Test-Path -LiteralPath $Ca65)) {
    Write-Host "[ERROR] ca65.exe が見つかりません: $Ca65" -ForegroundColor Red
    exit 1
}

& python -c "import importlib.util as u, sys; sys.exit(0 if all(u.find_spec(m) for m in ('PIL', 'numpy')) else 1)"
if ($LASTEXITCODE -ne 0) {
    Write-Host '[ERROR] Pillow / numpy がインストールされていません。次のコマンドを実行してください:' -ForegroundColor Red
    Write-Host '        python -m pip install -r requirements.txt'
    exit 1
}

Write-Host '[1/3] 画像をまとめる: tools\slideshow\make_slides.py'
& python tools\slideshow\make_slides.py @args
if ($LASTEXITCODE -ne 0) {
    Write-Host '[ERROR] 画像のまとめに失敗しました。' -ForegroundColor Red
    exit 1
}
if ($args -contains '--help' -or $args -contains '-h') { exit 0 }

Write-Host '[2/3] アセンブル: nes\src\slideshow.s'
& $Ca65 -g -I nes\src -I build\slides --bin-include-dir build\slides -o build\slideshow.o nes\src\slideshow.s
if ($LASTEXITCODE -ne 0) {
    Write-Host '[ERROR] アセンブルに失敗しました。上のメッセージを確認してください。' -ForegroundColor Red
    exit 1
}

Write-Host '[3/3] リンク: build\phase3_slideshow.nes'
& $Ld65 -C nes\cfg\unrom128.cfg -o build\phase3_slideshow.nes --dbgfile build\phase3_slideshow.dbg -m build\phase3_slideshow.map build\slideshow.o
if ($LASTEXITCODE -ne 0) {
    Write-Host '[ERROR] リンクに失敗しました。上のメッセージを確認してください。' -ForegroundColor Red
    exit 1
}

$size = (Get-Item -LiteralPath 'build\phase3_slideshow.nes').Length
Write-Host ''
Write-Host "ビルド成功: build\phase3_slideshow.nes ($size バイト)" -ForegroundColor Green
Write-Host '中身の一覧: build\slides\index.html'
Write-Host 'エミュレータで確認: .\run_luanes.ps1 -Slideshow'
exit 0
