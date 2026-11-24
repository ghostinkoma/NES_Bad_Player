# NES_BadPlayer

ファミコン（NES）の画面いっぱい（256×240）で、ROM に入れた動画を音声つきで再生するプレイヤーです。
エミュレータ Mesen で動かします。

- 電源を入れると、オープニング → メニューの順に表示します。メニューで動画を選んで再生します。
- 表示方法は、2値（白黒）、4値グレー、カラー（4×4 ドットのモザイク）、固定4色の4種類です。
- 音声は DPCM で再生し、映像と同期します。ORIGEN とメニューの BGM は 24.9kHz、ほかは 11.2kHz です。
  ROM の容量の中で「画像と音声はトレードオフ」になることを、動画ごとに設定を変えて見せています。
- 音は puNES で聞くのがおすすめです（Mesen では音にジッタが混じります。下の「音をきれいに聞くには」を参照）。
- 一時停止、コマ送り、コマ戻し、FPS 表示ができます。再生モードは REPEAT / STOP / SEQ の3つです。
- マッパーは BNROM（mapper 34）で、ROM は最大 8MB です。
- **配布用サンプル（PCM 版）**：オープニング、メニューの背景の動画（PUNINEKO）、Bad Apple!! の原曲だけを入れ、
  音を $4011 の 7bit PCM（18.6kHz）で鳴らす版です。DPCM 版より音がきれいで、puNES でも Mesen でもきれいに鳴ります。

## 必要なもの
- [cc65](https://cc65.github.io/)：`ca65.exe` と `ld65.exe` をこのフォルダの1つ上の `bin\` に置きます。
- Python 3：Pillow と numpy 2.0 以降が必要です（`python -m pip install -U pillow numpy`）。
- ffmpeg：音声の変換と、プレビュー動画の作成に使います。
- [Mesen](https://www.mesen.ca/) 2.2.1：このフォルダの1つ上に `Mesen.exe` を置きます。

## ビルドと実行
PowerShell でこのフォルダに移動してから実行します。

```powershell
.\build_video.ps1
```

```powershell
.\run_mesen.ps1
```

- 初回のビルドは15〜20分かかります。2回目からは変換結果を使い回すので、1分ほどで終わります。
- 入れる動画や画質は `tools\video\titles.json` で設定します。
- 動画・音楽の素材（`resouce\`）とビルド結果（ROM）は、このリポジトリに入っていません。
  素材は各自で用意して `resouce\` に置き、`titles.json` のパスを合わせてください。
  素材の形式（連番 BMP と同じ名前の .mp4）は [docs/PHASE2X.md](docs/PHASE2X.md) を見てください。
- `.\build_video.ps1 -Test` を実行すると、Mesen を画面なしで動かして、動作を自動で確認します。
- `.\build_pcm_video.ps1` を実行すると、音を $4011 の 7bit PCM（18.6kHz）で鳴らす配布用サンプル ROM を
  `build\pcmv\pcm_video.nes` に作ります（オープニング、メニューの背景の動画、Bad Apple!! の原曲だけ。CPU が 96 サイクルごとに音を書くプレイヤー）。設定は `tools\video\titles_pcm.json` です。
- `.\build_video.ps1 -Sample` を実行すると、DPCM 版の配布用のサンプル ROM（オープニング、メニュー、ORIGEN だけ）を
  `build\sample\NES_BadPlayer_sample.nes` に作ります。設定は `tools\video\titles_sample.json` です。
  `titles.json` で `"enabled": false` にした動画は ROM に入らず、メニューにも出ません。

### 音をきれいに聞くには（puNES がおすすめ）
- Mesen では、音にジッタ（細かいブツブツ・揺れ）が混じります。これはエミュレータの音声出力の特性です。
- [puNES](https://github.com/punesemu/puNES) で `build\video\movies.nes` を開くと、ROM の音声データどおりの、ほぼ理想の音で鳴ることを確認しています。
- ROM の音声を、エミュレータを通さずに鳴らした WAV も作れます（`python tools\video\dpcm_to_wav.py 1 24.9`
  → `build\video\ideal_1_24.9kHz.wav`。数字は動画の番号と kHz）。エミュレータの音と聞き比べると、雑音の原因がどちらにあるか切り分けられます。

## 操作
| 画面 | ボタン | 動き |
|---|---|---|
| オープニング | START | 飛ばしてメニューへ |
| メニュー | ↑ ↓ / A | 動画を選ぶ / 再生 |
| | SELECT | 再生モードの切り替え（REPEAT → STOP → SEQ） |
| 再生中 | B | 一時停止 / 再開 |
| | → / ←（一時停止中） | 1コマ送り / 1コマ戻し |
| | SELECT | FPS 表示の ON / OFF |
| | START | メニューに戻る |

## ドキュメント
- [docs/PHASE2X.md](docs/PHASE2X.md)：使い方、仕組み、ROM の構成、パケットの形式、テスト
- [docs/HISTORY.md](docs/HISTORY.md)：開発の記録
- [docs/BUGFIX.md](docs/BUGFIX.md)：指摘を受けて直した不具合の一覧（配布用サンプル・PCM 版）
- [PLAN.md](PLAN.md)：プロジェクト全体の計画

## ライセンス
Bad Apple!! 系の作品の作者の方々にならい、**非商用に限り自由に**使用・改変・再配布してかまいません。
商用での利用はご遠慮ください。

動画・音楽などの素材の権利は、それぞれの権利者に帰属します。

## 開発後記
ひょんなことから、知人と「NES で何か作ろう」という話が持ち上がりました。
そこで、とりあえず1ドットを描くところから、コツコツと始めてみました。
NES 自体が初めてで、しかも突貫で作ったので、それなりの **BAD** クオリティです。

[Wikipedia](https://ja.wikipedia.org/wiki/Ricoh_2A03) によれば、ファミコンに載っている CPU は、
MOS 6502 をもとにリコーがカスタム製造した RP2A03（8ビット CPU）です。
Gemini によれば、処理速度は推定で約 0.5〜0.8 MIPS、平均すると約 0.6 MIPS 前後だそうです。

言うまでもなく、現代のマイコンと比べても相当な低スペックです。
それでも、いまだに根強い人気があるのではないかと思います。

正直もう触りたくありませんが、また何かの機会があれば、何か作るかもしれません。

全盛期に数々の名作を作ってくださった方々、そして開発ツール群を作成された方々に、心から敬意を表します。
