# ===========================================================================
# run_paint.ps1 - 第1フェーズ お絵描きツールの起動（PowerShell 版）
#   使い方: project フォルダで  .\run_paint.ps1
#           引数はそのまま nes_paint.py に渡る（例: .\run_paint.ps1 --zoom 3）
#   ※ このファイルは UTF-8（BOM 付き）で保存すること
# ===========================================================================
$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot

if (-not (Get-Command python -ErrorAction SilentlyContinue)) {
    Write-Host '[ERROR] python が見つかりません。Python 3 をインストールしてください。' -ForegroundColor Red
    exit 1
}

New-Item -ItemType Directory -Force -Path 'build\bridge' | Out-Null
& python tools\paint\nes_paint.py @args
exit $LASTEXITCODE
