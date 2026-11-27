# release（コンパイル済みの ROM）

ビルドしなくても遊べる、配布用サンプルの ROM です。
Prebuilt sample ROM — no build needed. Open it in an emulator.

| ファイル | 内容 |
|---|---|
| `NES_BadPlayer_sample_pcm.nes` | 配布用サンプル（PCM 版、8,388,624 バイト） |
| `SHA256SUMS.txt` | SHA-256 のチェックサム |

## 入っているもの / Contents
- **OPENING**：オープニング / intro
- **PUNINEKO**：メニューの背景の動画（BGM つき） / menu background animation with music
- **BAD APPLE**：Bad Apple!!（白黒・30fps、約 3 分 39 秒） / black and white, 30 fps, about 3 min 39 s

## 仕様 / Specs
- 映像: 256×240、白黒 / Video: 256×240, 1-bit
- 音: $4011 の 7bit PCM、18.6kHz / Audio: 7-bit PCM via $4011 at 18.6 kHz
- マッパー: BNROM（mapper 34）、NES 2.0 ヘッダ、8MB / Mapper: BNROM (mapper 34), NES 2.0, 8 MB
- 確認済み: puNES、Mesen 2.2.1（実機は未確認） / Tested on puNES and Mesen 2.2.1 (not on real hardware)

操作方法は [README.md](../README.md)（英語は [README_EN.md](../README_EN.md)）を見てください。
GitHub の Release にも同じ ROM があります: https://github.com/ghostinkoma/NES_Bad_Player/releases/latest

## チェックサムの確かめ方 / Verify
```powershell
(Get-FileHash release\NES_BadPlayer_sample_pcm.nes).Hash
```
`D03CAE7E0793AB6595523A96C5E6C55BBCD60109AAB19D770A8E2058C1297D16` と同じなら OK です。

## 権利 / License
- プログラムは非商用に限り自由に使用・改変・再配布できます。 / Free for non-commercial use only.
- 動画・音楽の権利は、それぞれの権利者にあります。素材は提供者の許可を得て収録しています。
  All rights to the video and music belong to their owners; included with permission.
