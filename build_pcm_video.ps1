# ===========================================================================
# build_pcm_video.ps1 - $4011 PCM 版の動画 ROM（配布用サンプル）のビルド
#   使い方: project フォルダで
#           .\build_pcm_video.ps1                 （tools\video\titles_pcm.json）
#           .\build_pcm_video.ps1 -Seconds 40     （先頭 40 秒だけ。試し用）
#           .\build_pcm_video.ps1 -Test           （Mesen で書き込みの間隔・音の並び・画面を確かめる）
#   出力  : build\pcmv\pcm_video.nes
#   ※ このファイルは UTF-8（BOM 付き）で保存すること
# ===========================================================================
param(
    [double]$Seconds = 0,
    [switch]$Test,
    [int]$TestFrames = 3500,
    [string]$Mesen = ''
)
$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
$Cc65Bin = Join-Path $PSScriptRoot '..\bin'
$Ca65 = Join-Path $Cc65Bin 'ca65.exe'
$Ld65 = Join-Path $Cc65Bin 'ld65.exe'
$Out = 'build\pcmv\pcm_video.nes'
New-Item -ItemType Directory -Force 'build\pcmv' | Out-Null

Write-Host '[1/3] アセンブル: nes\src\pcm_video.s'
& $Ca65 -g -I nes\src -l build\pcmv\pcm_video.lst -o build\pcmv\pcm_video.o nes\src\pcm_video.s
if ($LASTEXITCODE -ne 0) { Write-Host '[ERROR] アセンブルに失敗しました。' -ForegroundColor Red; exit 1 }
Write-Host '[2/3] リンク: build\pcmv\pcm_video.bin（全バンク共通 3KB）'
& $Ld65 -C nes\cfg\pcm_video.cfg -o build\pcmv\pcm_video.bin -m build\pcmv\pcm_video.map -Ln build\pcmv\pcm_video.lbl build\pcmv\pcm_video.o
if ($LASTEXITCODE -ne 0) { Write-Host '[ERROR] リンクに失敗しました。' -ForegroundColor Red; exit 1 }

Write-Host '[3/3] 動画と音声を変換して ROM を作る'
$pyArgs = @('tools\video\make_pcm_rom.py', '--code', 'build\pcmv\pcm_video.bin', '--labels', 'build\pcmv\pcm_video.lbl', '-o', $Out)
if ($Seconds -gt 0) { $pyArgs += @('--seconds', $Seconds) }
if ($Test) { $pyArgs += @('--mesen-test', $TestFrames) }
& python @pyArgs
if ($LASTEXITCODE -ne 0) { Write-Host '[ERROR] ROM の作成に失敗しました。' -ForegroundColor Red; exit 1 }

if ($Test) {
    if (-not $Mesen) { $Mesen = Join-Path $PSScriptRoot '..\Mesen.exe' }
    $rom = (Resolve-Path $Out).Path
    $lua = (Resolve-Path 'build\pcmv\pcm_video_test.lua').Path
    $log = 'build\pcmv\pcm_video_log.txt'
    Write-Host '[確認] Mesen を画面なしで動かします'
    Start-Process -FilePath $Mesen -ArgumentList '--testrunner', '--timeout=1800', "`"$rom`"", "`"$lua`"" -Wait -NoNewWindow -RedirectStandardOutput $log
    & python tools\video\pcm_test.py build\pcmv\pcm_video $log
    if ($LASTEXITCODE -ne 0) { Write-Host 'Mesen での確認: NG' -ForegroundColor Red; exit 1 }
    Write-Host 'Mesen での確認: OK' -ForegroundColor Green
}
Write-Host "再生: .\run_mesen.ps1 -Rom $Out"
exit 0
