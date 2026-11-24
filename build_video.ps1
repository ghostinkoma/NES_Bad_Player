# ===========================================================================
# build_video.ps1 - フェーズ2.x 動画 ROM（メニュー付き）のビルド
#   使い方: project フォルダで
#           .\build_video.ps1               （tools\video\titles.json の動画をまとめる）
#           .\build_video.ps1 -Seconds 20   （各動画の先頭 20 秒だけ。試し用）
#           .\build_video.ps1 -Preview      （ROM の中身を再現した mp4 も作る）
#           .\build_video.ps1 -Test         （ビルド後に Mesen で自動確認）
#           .\build_video.ps1 -Sample       （配布用サンプル: titles_sample.json → build\sample\NES_BadPlayer_sample.nes）
#           .\build_video.ps1 -Config tools\video\xxx.json -Out build\xxx\xxx.nes （設定と出力先を指定）
#   出力  : build\video\movies.nes と movies_report.txt
#   ※ このファイルは UTF-8（BOM 付き）で保存すること
# ===========================================================================
param(
    [double]$Seconds = 0,
    [switch]$Preview,
    [switch]$Test,
    [switch]$Sample,
    [string]$Config = 'tools\video\titles.json',
    [string]$Out = 'build\video\movies.nes',
    [string]$Mesen = ''
)
$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
if ($Sample) {
    $Config = 'tools\video\titles_sample.json'
    $Out = 'build\sample\NES_BadPlayer_sample.nes'
}
$OutDir = Split-Path -Parent $Out
$OutBase = Join-Path $OutDir ([IO.Path]::GetFileNameWithoutExtension($Out))

$Cc65Bin = Join-Path $PSScriptRoot '..\bin'
$Ca65 = Join-Path $Cc65Bin 'ca65.exe'
$Ld65 = Join-Path $Cc65Bin 'ld65.exe'
if (-not (Test-Path -LiteralPath $Ca65)) {
    Write-Host "[ERROR] ca65.exe が見つかりません: $Ca65" -ForegroundColor Red
    exit 1
}
& python -c "import importlib.util as u, sys, numpy; sys.exit(0 if u.find_spec('PIL') and hasattr(numpy, 'bitwise_count') else 1)"
if ($LASTEXITCODE -ne 0) {
    Write-Host '[ERROR] Pillow / numpy 2.0 以降が必要です。次のコマンドを実行してください:' -ForegroundColor Red
    Write-Host '        python -m pip install -U pillow numpy'
    exit 1
}
New-Item -ItemType Directory -Force 'build\video' | Out-Null
New-Item -ItemType Directory -Force $OutDir | Out-Null

Write-Host '[1/3] アセンブル: nes\src\video.s'
& $Ca65 -g -I nes\src -o build\video\player.o nes\src\video.s
if ($LASTEXITCODE -ne 0) { Write-Host '[ERROR] アセンブルに失敗しました。' -ForegroundColor Red; exit 1 }

Write-Host '[2/3] リンク: build\video\player.bin（全バンク共通のプレイヤー 4KB）'
& $Ld65 -C nes\cfg\video_code.cfg -o build\video\player.bin -m build\video\player.map -Ln build\video\player.lbl build\video\player.o
if ($LASTEXITCODE -ne 0) { Write-Host '[ERROR] リンクに失敗しました。' -ForegroundColor Red; exit 1 }

Write-Host "[3/3] 動画を変換して ROM を作る（$Config）"
# 変換結果（cache）は設定が違っても build\video\cache で共用する（中身で見分けている）
$pyArgs = @('tools\video\make_video_rom.py', '--code', 'build\video\player.bin', '--labels', 'build\video\player.lbl',
            '--config', $Config, '--cache-dir', 'build\video\cache', '-o', $Out)
if ($Seconds -gt 0) { $pyArgs += @('--seconds', $Seconds) }
if ($Preview) { $pyArgs += '--preview' }
if ($Test) { $pyArgs += '--mesen-test' }
& python @pyArgs
if ($LASTEXITCODE -ne 0) { Write-Host '[ERROR] 動画の変換に失敗しました。' -ForegroundColor Red; exit 1 }

Write-Host ''
Write-Host "ビルド成功: $Out" -ForegroundColor Green

if ($Test) {
    if (-not $Mesen) { $Mesen = Join-Path $PSScriptRoot '..\Mesen.exe' }
    $romFull = (Resolve-Path $Out).Path
    $lua = (Resolve-Path "${OutBase}_mesen_test.lua").Path
    Write-Host '[確認] Mesen を画面なしで動かして、メニュー・再生・コマ送り/戻し・FPS 表示・再生モード・グレー/カラー・音声を確かめます（10 分ほど）'
    $p = Start-Process -FilePath $Mesen -ArgumentList '--testrunner', '--timeout=1500', "`"$romFull`"", "`"$lua`"" -Wait -PassThru -NoNewWindow
    Write-Host '[確認] 音声（DPCM）: DMC が毎フレーム正しいバンクの正しいデータを読んでいるか、音ズレ、音飛び'
    & python tools\video\check_audio.py lua build\video\player.lbl "${OutBase}_audio_test.lua" $OutDir
    $lua2 = (Resolve-Path "${OutBase}_audio_test.lua").Path
    $alog = Join-Path $OutDir 'audio_log.txt'
    Start-Process -FilePath $Mesen -ArgumentList '--testrunner', '--timeout=1500', "`"$romFull`"", "`"$lua2`"" -Wait -NoNewWindow -RedirectStandardOutput $alog
    & python tools\video\check_audio.py check $alog $OutDir
    $audioOk = ($LASTEXITCODE -eq 0)
    if ($p.ExitCode -eq 0 -and $audioOk) {
        Write-Host 'Mesen での確認: OK' -ForegroundColor Green
    } else {
        Write-Host "Mesen での確認: NG（操作 $($p.ExitCode) 件 / 音声 $(if ($audioOk) {'OK'} else {'NG'})）" -ForegroundColor Red
        exit 1
    }
}
if ($Out -eq 'build\video\movies.nes') { Write-Host 'Mesen で再生: .\run_mesen.ps1' }
else { Write-Host "Mesen で再生: .\run_mesen.ps1 -Rom $Out" }
exit 0