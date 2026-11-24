# ===========================================================================
# build_pcm_proto.ps1 - $4011 PCM 再生の試作 ROM（音だけ）のビルド
#   使い方: project フォルダで
#           .\build_pcm_proto.ps1          （ORIGEN の音声で 2 つの ROM を作る）
#           .\build_pcm_proto.ps1 -Test    （Mesen で書き込みの間隔と値を自動で確認）
#           .\build_pcm_proto.ps1 -Cycles 96 （1 サンプル 96 サイクル = 18,644Hz。出力は *_96.nes）
#   出力  : build\pcm\pcm_proto.nes     （補正なし: 値をそのまま $4011 へ）
#           build\pcm\pcm_proto_nl.nes  （ファミコンの音の混ぜ方のゆがみを打ち消すように補正）
#           それぞれの _ideal.wav（エミュレータを通さない理想の音）
#   ※ このファイルは UTF-8（BOM 付き）で保存すること
# ===========================================================================
param(
    [switch]$Test,
    [int]$Cycles = 81,          # 1 サンプルのサイクル数（81 = 22,096Hz / 96 = 18,644Hz）
    [string]$Mesen = ''
)
$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
$Cc65Bin = Join-Path $PSScriptRoot '..\bin'
$Ca65 = Join-Path $Cc65Bin 'ca65.exe'
$Ld65 = Join-Path $Cc65Bin 'ld65.exe'
New-Item -ItemType Directory -Force 'build\pcm' | Out-Null

Write-Host '[1/3] アセンブル: nes\src\pcm_proto.s'
& $Ca65 -g -I nes\src -D "CYC=$Cycles" -l build\pcm\pcm_proto.lst -o build\pcm\pcm_proto.o nes\src\pcm_proto.s
if ($LASTEXITCODE -ne 0) { Write-Host '[ERROR] アセンブルに失敗しました。' -ForegroundColor Red; exit 1 }
Write-Host '[2/3] リンク: build\pcm\pcm_proto.bin（全バンク共通 4KB）'
& $Ld65 -C nes\cfg\video_code.cfg -o build\pcm\pcm_proto.bin -m build\pcm\pcm_proto.map -Ln build\pcm\pcm_proto.lbl build\pcm\pcm_proto.o
if ($LASTEXITCODE -ne 0) { Write-Host '[ERROR] リンクに失敗しました。' -ForegroundColor Red; exit 1 }

Write-Host '[3/3] 音声を変換して ROM を作る'
$sfx = $(if ($Cycles -eq 81) { '' } else { "_$Cycles" })
$roms = @(@{ Out = "build\pcm\pcm_proto$sfx.nes"; Args = @() }, @{ Out = "build\pcm\pcm_proto_nl$sfx.nes"; Args = @('--nl') })
foreach ($r in $roms) {
    $pyArgs = @('tools\video\make_pcm_proto.py', '--code', 'build\pcm\pcm_proto.bin', '--labels', 'build\pcm\pcm_proto.lbl',
                '-o', $r.Out, '--cycles', $Cycles) + $r.Args
    if ($Test) { $pyArgs += '--mesen-test' }
    & python @pyArgs
    if ($LASTEXITCODE -ne 0) { Write-Host '[ERROR] ROM の作成に失敗しました。' -ForegroundColor Red; exit 1 }
}

if ($Test) {
    if (-not $Mesen) { $Mesen = Join-Path $PSScriptRoot '..\Mesen.exe' }
    $fails = 0
    foreach ($r in $roms) {
        $base = [IO.Path]::ChangeExtension($r.Out, $null).TrimEnd('.')
        $rom = (Resolve-Path $r.Out).Path
        $lua = (Resolve-Path "${base}_test.lua").Path
        $log = "${base}_test_log.txt"
        Start-Process -FilePath $Mesen -ArgumentList '--testrunner', '--timeout=300', "`"$rom`"", "`"$lua`"" -Wait -NoNewWindow -RedirectStandardOutput $log
        $line = (Get-Content $log | Where-Object { $_ -like 'writes *' } | Select-Object -Last 1)
        $exp = (Get-Content "${base}_expect.txt").Split(' ')
        $f = $line.Split(' ')
        # writes N bad B min X max Y hash H
        $ok = ($f[3] -eq '0') -and ($f[5] -eq "$Cycles") -and ($f[7] -eq "$Cycles") -and ($f[9] -eq $exp[1]) -and ([int]$f[1] -ge [int]$exp[0])
        Write-Host ("{0}: 書き込み {1} 回 / {6} サイクル以外 {2} 回（最短 {3} 最長 {4}）/ 値の並び {5}" -f $r.Out, $f[1], $f[3], $f[5], $f[7], $(if ($f[9] -eq $exp[1]) { '一致' } else { '不一致' }), $Cycles)
        if (-not $ok) { $fails++ }
    }
    if ($fails -eq 0) { Write-Host 'Mesen での確認: OK' -ForegroundColor Green } else { Write-Host 'Mesen での確認: NG' -ForegroundColor Red; exit 1 }
}
Write-Host '再生: .\run_mesen.ps1 -Rom build\pcm\pcm_proto.nes（puNES でも同じファイルを開く）'
exit 0
