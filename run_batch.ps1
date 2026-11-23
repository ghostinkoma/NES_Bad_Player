# ===========================================================================
# run_batch.ps1 - 第2フェーズ 一括事前変換
#   使い方: project フォルダで
#           .\run_batch.ps1 assets\images                 （フォルダ内を全部変換 → build\converted）
#           .\run_batch.ps1 assets\images\cat.jpg --compare （ディザ3種類を書き出して見比べる）
#           .\run_batch.ps1 --help                          （オプション一覧）
#   ※ このファイルは UTF-8（BOM 付き）で保存すること
# ===========================================================================
$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot

& python -c "import importlib.util as u, sys; sys.exit(0 if all(u.find_spec(m) for m in ('PIL', 'numpy')) else 1)"
if ($LASTEXITCODE -ne 0) {
    Write-Host '[ERROR] Pillow / numpy がインストールされていません。次のコマンドを実行してください:' -ForegroundColor Red
    Write-Host '        python -m pip install -r requirements.txt'
    exit 1
}

& python tools\converter\batch_convert.py @args
exit $LASTEXITCODE
