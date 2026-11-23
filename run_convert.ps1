# ===========================================================================
# run_convert.ps1 - 第2フェーズ 減色変換ツール（GUI）の起動
#   使い方: project フォルダで  .\run_convert.ps1  [画像ファイル]
#   前提  : python -m pip install -r requirements.txt（Pillow / numpy）
#   ※ このファイルは UTF-8（BOM 付き）で保存すること
# ===========================================================================
$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot

if (-not (Get-Command python -ErrorAction SilentlyContinue)) {
    Write-Host '[ERROR] python が見つかりません。Python 3 をインストールしてください。' -ForegroundColor Red
    exit 1
}

& python -c "import importlib.util as u, sys; sys.exit(0 if all(u.find_spec(m) for m in ('PIL', 'numpy')) else 1)"
if ($LASTEXITCODE -ne 0) {
    Write-Host '[ERROR] Pillow / numpy がインストールされていません。次のコマンドを実行してください:' -ForegroundColor Red
    Write-Host '        python -m pip install -r requirements.txt'
    exit 1
}

New-Item -ItemType Directory -Force -Path 'build\bridge' | Out-Null
& python tools\converter\convert_gui.py @args
exit $LASTEXITCODE
