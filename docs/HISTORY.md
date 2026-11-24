# 開発履歴

各フェーズで作ったもの、起動方法、確認結果を記録する。

---

## 第1フェーズ: エミュレータ連携お絵描きツール — ✅ 完了（2026-11-22）

### 作ったもの
| ファイル | 内容 |
|---|---|
| `nes/src/main.s`, `nes.inc`, `mailbox.inc` | 表示専用 ROM（UNROM / CHR-RAM）。メールボックス経由で受け取ったデータを VBlank 中に PPU へ転送し、パッド状態を書き出す |
| `nes/cfg/unrom.cfg` | ld65 リンカ設定 |
| `build.ps1` | ROM ビルド（最初は .bat だったが PowerShell 用に書き直した） |
| `tools/paint/` | お絵描き GUI（`nes_paint.py`）、絵データ変換（`paint_model.py`）、パレット（`nes_palette.py`） |
| `tools/emu_bridge/bridge.lua` | GUI ⇔ エミュレータのブリッジ（Mesen2 / FCEUX / LuaNES 対応） |
| `tools/luanes_host/` | LuaNES を改造せずに bridge.lua を差し込む LÖVE 用ホスト |
| `run_paint.ps1`, `run_luanes.ps1` | 起動スクリプト |
| `docs/BUILD.md`, `docs/LUANES.md` | ビルド・起動手順書 |

### 起動方法（確定版）
前提: cc65 を `D:\hobby\nes\` に展開済み、LÖVE 11.x をインストール済み、
LuaNES を `D:\hobby\nes\LuaNES` に clone 済み（`git clone https://github.com/nico-abram/LuaNES.git D:\hobby\nes\LuaNES`）。

PowerShell で `D:\hobby\nes\project` に移動して実行する（今いるフォルダのスクリプトは `.\` を付けて実行する）。

1. ROM をビルド（`run_luanes.ps1` は ROM が無ければ自動でビルドするので省略可）

```powershell
.\build.ps1
```

2. お絵描きツールを起動（PowerShell をもう1つ開いて実行）

```powershell
.\run_paint.ps1
```

3. LuaNES をブリッジ付きで起動

```powershell
.\run_luanes.ps1
```

コンソールに `bridge: ROM に接続しました` と出れば接続成功。GUI で描いた絵が LuaNES の画面に反映され、
LuaNES のキー（W/A/S/D=十字、O=A、P=B、I=SELECT、Enter=START）を押すと GUI 右下にパッドの状態が表示される。

### 確認結果
- 開発側: 6502 シミュレータ、Lua 実行環境、LuaJIT 上の LuaNES（画面描画部分のみ模擬）で自動テスト済み
- **ユーザー環境（実機 PC + LÖVE + LuaNES）で動作確認済み（2026-11-22）**

### 決まったこと・制約
- キャンバスは 128×120 ドット（16×15 タイル）。BG で別々の絵にできるタイルが 256 枚までのため。全画面の絵は第4フェーズ（RP2354 から CHR を動的に供給）で扱う
- GUI → エミュレータの受け渡しはファイル（`build/bridge/state.bin`）のポーリング。プラグインは不要
- 1 フレームに送れるのは最大 128 バイト。画面全体の送信は約 0.6 秒
- スクリプトは PowerShell（.ps1、UTF-8 BOM 付き）で用意する

---

## 第2フェーズ: ファミコンパレット減色変換ツール — ✅ 完了（2026-11-23）

### 作ったもの
| ファイル | 内容 |
|---|---|
| `tools/converter/nes_convert.py` | 変換エンジン（Lab 色空間、4パレット＋ブロック割り当ての交互最適化、ディザ3種） |
| `tools/converter/convert_gui.py` | 減色変換 GUI（パラメータ変更で即再変換 → エミュレータへ自動送信、クリップボード貼り付け、ファイル監視） |
| `tools/converter/batch_convert.py` | 一括事前変換（.npaint.json / プレビュー / ROM 用データ / index.html） |
| `run_convert.ps1`, `run_batch.ps1`, `requirements.txt` | 起動スクリプトと依存ライブラリ |
| `docs/PHASE2.md` | 使い方 |

あわせて第1フェーズのお絵描きツールを修正: 送信番号（seq）を起動時刻から始めるようにした
（ツールを再起動した直後に、ブリッジが前回と同じ番号だと思って無視することがあったため）。

### 起動方法
初回のみ: `python -m pip install -r requirements.txt`

```powershell
.\run_convert.ps1
```
```powershell
.\run_luanes.ps1
```

詳しくは `docs/PHASE2.md`。

### 確認結果
- 開発側: 変換エンジンの制約チェック、GUI の読み込み → 変換 → state.bin 書き出し、一括変換（`--compare`）を自動テスト済み
- **ユーザー環境（実機 PC + LÖVE + LuaNES）で、変換ツール → エミュレータ表示を確認済み（2026-11-23）**

### 決まったこと・制約
- 変換結果の受け渡しは第1フェーズと同じ state.bin。お絵描きツールと変換ツールは同時に起動しない
- 出力は 128×120。用意済みのディザ画像は「縮小方法: ニアレスト」「ディザ: なし」で崩さず変換できる
- 第3フェーズでは、ファミコン用データ書き出し（`_chr.bin` / `_attr.bin` / `_pal.bin`）を ROM に埋め込んで使う
  （実際には `.npaint.json` をそのまま読み込む形にした。第3フェーズ参照）

---

## 第3フェーズ: スライドショー ROM とマジコンでの実機確認 — 🚧 ソース作成済み・ユーザー環境での確認待ち（2026-11-23）

### 作ったもの
| ファイル | 内容 |
|---|---|
| `nes/src/slideshow.s` | スライドショー ROM（UNROM 128KB / CHR-RAM、最大 21 枚、ダブルバッファで切り替え） |
| `nes/src/ui_tiles.inc` | 番号・パッド表示用のタイル（`tools/slideshow/gen_ui_tiles.py` が生成） |
| `nes/cfg/unrom128.cfg` | ld65 リンカ設定（16KB × 8 バンク） |
| `tools/slideshow/make_slides.py` | 画像 / .npaint.json をまとめてバンクデータとスライド表を作る。色見本 7 枚・テスト模様 1 枚も作る |
| `build_slideshow.ps1` | まとめ → アセンブル → リンク |
| `docs/PHASE3.md`, `docs/phase3_report.md` | 手順書、実機確認レポートのひな形 |

あわせて変更:
- `tools/luanes_host/main.lua`: LuaNES の UNROM バンク切り替え不具合を、LuaNES のファイルを変えずにホスト側で修正
  （元のコードはコピーのループが 1 回も回らず、バンクが切り替わらなかった）
- `run_luanes.ps1`: `-Slideshow` を追加（スライドショー ROM で起動。ROM が無ければビルド）

### 起動方法
```powershell
.\build_slideshow.ps1
```
```powershell
.\run_luanes.ps1 -Slideshow
```
画像は `assets\slides\` に置く（.npaint.json がおすすめ）。マジコンには `build\phase3_slideshow.nes` をコピーする。
詳しくは `docs/PHASE3.md`。

### 決まったこと・制約
- マッパーは UNROM（mapper 2）。多くのマジコンが対応し、第4フェーズで RP2354 が真似するのも簡単な方式
- 画像の切り替えは「VBlank ごとに 128 バイトずつ裏のパターンテーブルへ書き、最後に表裏を入れ替える」方式。
  第4フェーズの RP2354 からの転送と同じ考え方で、実機での確認が第4フェーズの裏付けになる
- VBlank 内の処理は最大 2,077 サイクル（上限は約 2,273）。実機で崩れる場合は CHUNK を 64 に下げる

---

## フェーズ2.x: ROM に入れた動画のフルサイズ再生（Mesen 用）— ✅ 段階7（配布用サンプル ROM）完了（2026-11-24）

第3フェーズの途中で追加したフェーズ。ROM に入れた動画を 256×240 で再生する。エミュレータは Mesen（`D:\hobby\nes\Mesen.exe`、2.2.1）。
最終的な目標: メニュー（PuniNeko のループ + スプライトで選択）、4 本の動画（Opening / Origen = 2値、
Tetsuy Komuro Remix = 4値グレー、2026AI = カラー）、SELECT の設定、DPCM による音声。

### 段階1: Origen だけの試作（2026-11-23）
- Origen 全編（219 秒）を 2.36MB で再生。Mesen で全 13,151 フレームの画面がエンコーダの計算と完全一致
- 書き込みの工夫: スタックページから `pla / sta $2007`、タイルの絵のキャッシュ、「直る点 ÷ バイト数」順、白黒反転はパレット

### 段階2: メニュー・再生モード・コマ戻し（2026-11-23）
| ファイル | 内容 |
|---|---|
| `nes/src/video.s` | プレイヤー（メニュー、再生モード、FPS 表示、コマ送り・コマ戻し） |
| `nes/src/video_font.inc` | スプライト文字 41 枚（`tools/video/gen_font.py` が生成） |
| `nes/cfg/video_code.cfg` | プレイヤー 4KB だけを出力する ld65 設定 |
| `tools/video/make_video_rom.py` | 複数の動画 → パケット列 + コマ戻し用スナップショット + 目録 → ROM。確認用 Lua も作る |
| `tools/video/titles.json` | 入れる動画の一覧（2・3 番はまだ `enabled: false`） |
| `tools/video/mesen_timing_shot.lua`, `shot_from_log.py` | 画面の書き出しと書き込みタイミングの測定 |
| `build_video.ps1`, `run_mesen.ps1` | ビルド（`-Test` で Mesen 自動確認）、Mesen での起動 |
| `docs/PHASE2X.md` | 使い方・仕組み・結果 |

### 起動方法
```powershell
.\build_video.ps1
```
```powershell
.\run_mesen.ps1
```
- メニュー: ↑↓ で選ぶ / A で再生 / SELECT で再生モード（REPEAT → STOP → SEQ）
- 再生中: B で一時停止 / 一時停止中に → でコマ送り、← でコマ戻し / SELECT で FPS 表示 / START でメニュー

自動確認付きでビルドするときは `.\build_video.ps1 -Test`。

### 確認結果
- 開発側: `.\build_video.ps1 -Test` で、Mesen の `--testrunner` を使った 24 項目がすべて OK
  （メニュー表示、Origen 600 フレームの画面一致、コマ送り、← 70 回で毎回正しいコマに戻る、FPS 表示、再生モード 3 種）。
  FPS 表示 ON でも、最後の書き込みは VBlank 開始から 2,162 サイクル目
- ユーザー環境（Mesen の画面で見る）: 未確認

### 決まったこと・制約
- マッパーは BNROM（mapper 34 / NES 2.0 submapper 2、32KB 切り替え、最大 8MB）。音声の DPCM を $C000 以降に置いても
  バンクごとに切り替えられるため。この ROM はマジコン・RP2354 向けではなく Mesen 専用
  （スプライトの OAM を毎フレーム書き直さないので、OAM が時間で消える実機では文字が崩れる）
- 1 フレームに 1 パケット。パケットはパソコン側で全部決めておき、ファミコン側は書くだけ
- メニューの文字はスプライトなので 1 行 8 文字まで（「Tetsuy Komuro Remix」は「TK REMIX」）
- コマ戻し用に約 2 秒ごとのスナップショット（1 個 3,009 バイト）。← を押すと最大 0.4 秒ほど画面が消える
- 現在の ROM: 4MB（112 / 128 バンク使用）。Origen 2.69MB、Opening とメニュー背景が各 0.2MB

### 段階3: 音声（DPCM）（2026-11-23）
| ファイル | 内容 |
|---|---|
| `nes/src/video.s` | DMC の鳴らし始め・止め・次のセグメントの予約、音声時計、DMC 対策つきのパッド読み取り |
| `tools/video/make_video_rom.py` | 動画ファイルの音声 → DPCM（16.9kHz）。各バンクに、そのバンクを読む間に鳴る音声を決まった場所に入れる。プレビュー mp4 に音も付ける |
| `tools/video/check_audio.py` | Mesen で毎フレーム DMC が読むデータ・音ズレ・音飛びを確かめる（`build_video.ps1 -Test` から呼ぶ） |

起動方法は段階2と同じ（`.\build_video.ps1` → `.\run_mesen.ps1`）。

#### 確認結果
- 開発側: `.\build_video.ps1 -Test` で、段階2の 24 項目に加えて音声の確認も OK
  （4,226 フレームで DMC が読むデータがすべて正しい、音ズレ −35〜+26ms、音飛び 0 回）。
  音声つきでも VBlank の最後の書き込みは 2,134 サイクル目
- ユーザー環境（Mesen で音を聞く）: 未確認

#### 決まったこと・制約
- DMC rate `$C`（16.9kHz、1 秒 約 2.1KB）。497 バイトのセグメントをループ再生で自動的につなぐ（IRQ は使わない）
- 各バンク `$D800`〜`$EFFF` が音声の置き場（12 スロット）。セグメント j は必ずスロット j mod 12。映像は `$8000`〜`$D7FF` の 22KB
- 音声は「連番フォルダ名.mp4」から取り出す（`titles.json` の `"audio"` で変更可）
- ROM は 4.5MB（138 バンク）。ROM の大きさを 2 のべき乗に揃えるのはやめた（Mesen は問題なく読む）
- （段階4で変更）DMC rate は動画ごとに選べるようにし、今は全部 `$9`（11.2kHz）。スロット数も動画ごとに自動で選ぶ

### 段階4: 4値グレーとカラー（2026-11-23）
| ファイル | 内容 |
|---|---|
| `tools/video/make_video_rom.py` | 4値グレー・カラー（場面ごとのパレット、4×4 モザイク）、ROM の中の書き方を小さく、1 秒あたりの量の上限、動画ごとの音声設定 |
| `tools/video/titles.json` | 4 本すべて入れる。動画ごとに `mode` / `step` / `kbps` / `pixel` / `snap_seconds` / `audio_rate` |
| `nes/src/video.s` | 新しい書き方の展開、16 バイトの絵、動画ごとのパレット・音声設定（目録 48 バイト）、4 段階のスナップショット |
| `tools/video/gen_font.py` | 文字の絵は make_video_rom.py がバンク 0 に入れるようにした（`nes/src/video_font.inc` は廃止） |
| `tools/video/check_audio.py` | 動画ごとの音声設定に対応、カラー・グレーも確認 |

起動方法は段階2と同じ（`.\build_video.ps1` → `.\run_mesen.ps1`）。初回のビルドは 15〜20 分。

#### 確認結果
- 開発側: `.\build_video.ps1 -Test` で 28 項目 + 音声（6,765 フレーム）すべて OK。
  グレー・カラーは 400 フレームの間、PPU の中身がエンコーダの計算と完全に一致、コマ戻しも OK。
  VBlank の最後の書き込みは最大 2,114 サイクル目
- ユーザー環境（Mesen で見る・聞く）: 未確認

#### 決まったこと・制約
- ROM の上限 8MB（256 バンク）が一番の制約。4 本で約 15 分なので、音声込みで平均 8KB/秒ほど。現在 247 バンク
- 調べてわかったこと: ROM の半分は「どこを書き換えるか」の位置の情報だった → 「前の場所から何マス先か」1 バイトに変えて 3 割減
- グレー: 12fps・4.5KB/秒（元画像と違う点 平均 9%）。明暗がほぼ反転する場面はパレットの入れ替えで済ませる
- カラー: 細かいイラストはタイル 256 種類に収まらず崩れるので、4×4 ドットのモザイク・10fps・5KB/秒（違う点 平均 21%）
- Origen は 6.5KB/秒に下げた（違う点 平均 0.9% → 1.1%）。音声は全部 11.2kHz に下げた

### 段階5: オープニングを Bad Player のロゴに差し替え（2026-11-23）
| ファイル | 内容 |
|---|---|
| `tools/video/make_video_rom.py` | 新しいモード `pal4`（決まった 4 色、各点をいちばん近い色に）と `fill_gaps`（連番の抜けを直前のコマで埋める） |
| `tools/video/titles.json` | OPENING を `resouce/openig/bmp`（30fps、黒 $0F・白 $30・赤 $16・緑 $1A、音なし）に |
| `tools/video/shot_from_log.py` | BOM 付きのログも読めるように |
| `nes/src/video.s` | 電源を入れるとオープニングを流し（state 2）、終わるとメニューへ。START で飛ばせる |
| テスト（`make_video_rom.py` の Lua / `check_audio.py` / `mesen_timing_shot.lua`） | 起動時のオープニングを待ってから始める。「起動するとオープニング」「終わるとメニュー」を追加 |

起動方法は段階2と同じ（`.\build_video.ps1` → `.\run_mesen.ps1`）。メニューの背景の PuniNeko はそのまま。
遷移: 電源 ON → オープニング → メニュー。

追加（同日）: オープニングの終わりに、最後のコマで 20 フレーム停止 → 「(C) Ghostinkoma」を重ねて 20 フレーム →
黒い画面に英語の免責文 → メニュー。`make_video_rom.py` に `append`（動画の後ろに静止画・文字の画面を足す。
文字は Tahoma 11px をにじみなしで書く）を追加。免責文を一度に出すと書き終わるまで 0.5 秒ほど文字が崩れて見えたので、
0.2 秒ごとに 1 行ずつ出し（各行は 1/15 秒以内に描き終わる）、全部出てから 2 秒表示。オープニングは全部で約 8.6 秒。
Mesen で画面を書き出して確認。

#### 確認結果
- 開発側: `.\build_video.ps1 -Test` で 29 項目 + 音声（6,768 フレーム）OK。Mesen で再生した画面を書き出して、
  赤いリンゴ・緑のヘタ・文字が出ていることを確認
- ユーザー環境（Mesen で見る）: 未確認

#### 決まったこと・制約
- 元の素材は 2 値だと赤いリンゴが黒に溶け、カラー（モザイク）だと文字がつぶれるので、4 色固定・フル解像度にした
- intro150 が抜けているので intro149 を 2 回出す（141 コマ・4.7 秒）
- かけらが画面いっぱいに速く回る所は、1 回の VBlank で書ける量が足りず消し残りの点が出る（`kbps` を上げても変わらない。
  `step: 2`（15fps）にすると違う点が平均 1.0% → 0.6% に減る）
- ROM は 247 → 242 バンク（空き 14 バンク）

### 段階6: 音質・画質の改善の試算と、音声の変換方法の改善（2026-11-23）
| ファイル | 内容 |
|---|---|
| `tools/video/make_video_rom.py` | 音声の 1 bit 列をビタビ法で決める `dpcm_viterbi` を追加（今までは「目標より低ければ上げる」を 1 つずつ）。振れ幅 `AUDIO_SWING` を 56 → 28 に |
| `tools/video/titles.json` | `audio_rate` を 9（11.2kHz）→ 11（14.0kHz）（後で 9 に戻した） |

起動方法は段階2と同じ（`.\build_video.ps1` → `.\run_mesen.ps1`）。

#### 試算（元の音声を 8kHz までに絞ったものと比べた SNR。各曲 20 秒）
| 方法 | ORIGEN | TK REMIX | 2026AI | 1 秒のデータ |
|---|---|---|---|---|
| 今まで（11.2kHz、1 つずつ決める、振れ幅 56） | 3.7dB | — | — | 1.4KB |
| 11.2kHz、ビタビ、振れ幅 56 | 6.2dB | 3.7dB | 4.1dB | 1.4KB |
| 11.2kHz、ビタビ、振れ幅 28 | 約 7.7dB | 5.1dB | 5.9dB | 1.4KB |
| **14.0kHz、ビタビ、振れ幅 28（採用）** | — | 5.9dB | 6.9dB | 1.75KB |
| 16.9kHz、ビタビ、振れ幅 20 | 10.1dB | — | — | 2.1KB（ROM に入らない） |
| $4011 に 7bit PCM（8kHz / 11.2kHz） | 10.0 / 12.7dB | — | — | 7.0 / 9.8KB（ROM に入らない） |

- DPCM は ±2 ずつしか動けないので、大きな音の速い変化に追いつけない（1kHz なら振れ幅 ±4 程度まで）。
  これが「混信したラジオ」の主な原因。振れ幅を小さくすると音は小さくなるが、追いつけずに崩れる所が減る
- **採用しなかったもの**
  - MMC5 / MMC3 の CHR-ROM 切り替え: MMC5 は PRG・CHR とも最大 1MB（合計 2MB）、MMC3 は 512KB + 256KB。
    今の 4 本（約 7.9MB）が入らない
  - $4011 への 7bit PCM 書き込み: SNR は +3〜5dB 良くなるが、データが 5〜7 倍（4bit ADPCM にしても 2.5 倍）で ROM に入らない。
    さらに VBlank の書き込みを含むメインループ全体をサイクル単位で組み直す必要がある

#### 確認結果
- 開発側: `.\build_video.ps1 -Test` で 29 項目 + 音声（6,768 フレーム、DMC が読むデータ 6,768/6,768 正しい、ずれ −49〜+83ms、音飛び 0）OK
- ユーザー環境（Mesen で聞く）: 未確認

#### 決まったこと・制約
- （いったん）ROM は 242 → 256 バンク（空きなし）。音は前より約 6dB 小さくなる（振れ幅を半分にしたため）

#### ユーザー確認の結果 → 元に戻した（同日）
- Mesen で聞くと、キャリアの音（小さい音のときの「ピー」）が増えて、前の版の方が良いという判断。
  数字（SNR）が良くなっても、耳で聞くと悪くなった。振れ幅を半分にしたぶん、音量に対してキャリアの音が相対的に大きくなり、
  14kHz にしたことでキャリアの高さ（7kHz）も聞こえやすい所に来たのが原因と考えられる
- 既定を前の版（11.2kHz、1 つずつ決める、振れ幅 56）に戻した。ROM は 242 バンク（空き 14）、前の版と同じ中身
- ビタビ法は消さずに titles.json で選べるようにした: `"audio_enc": "viterbi"`、`"audio_swing": 28` など（全体または動画ごと）

#### ORIGEN だけ 33kHz（rate $F）を試す → ROM に入らず（同日）
- ORIGEN を `audio_rate: 15`・`kbps: 5.5` にすると、ORIGEN が 68 → 約 89 バンク（コマ戻し用込み）になり、合計 260 バンク超で入らなかった
- 33kHz では 1 秒 4.1KB の音声を、そのバンクを読む時間ぶん全部バンクに置く必要がある。DMC が読めるのは $C000〜$EFFF の 12KB
  （24 スロット = 約 2.9 秒）だけなので、1 バンクで進める時間が音声で決まり、映像の量（kbps）を下げてもバンクは減らない
- 見つかった不具合を直した: スロット数が $C000 より前にはみ出す数まで選ばれていた（`choose_slots` に上限 24）。
  256 バンクを超えたときに、わかりやすいメッセージで止まるようにした
- titles.json は元に戻した（ROM は 242 バンクのまま）

#### ORIGEN だけ 24.9kHz（rate $E）にする（同日）
- `audio_rate: 14` だけだと 257 バンクで 1 つ足りないので、ORIGEN の `kbps` を 6.5 → 6.0 に下げた
- ORIGEN: 77 バンク（前は 68）、音声 688KB（1 バンクに 20 スロット）、映像の違う点 平均 1.12% → 1.32%
- ROM は 254 バンク（空き 2）。前の版は `build/video/movies_prev.nes` に残した（聞き比べ用）
- キャリアは約 12.4kHz（11.2kHz のときは約 5.6kHz）。ユーザー確認: キャリアの音が減って良くなった。
  Mesen で聞こえるジッタは、理想の再生音（`tools/video/dpcm_to_wav.py` で作る WAV）には無いので、Mesen の音声出力の特性と判断
- 全部を 24.9kHz にする試し（TK REMIX を 2 値・3.0KB/秒、2026AI を 4.3KB/秒）は 270 バンク超で入らなかった。
  24.9kHz では 1 バンクの音声の置き場（$C000〜$EFFF、20〜24 スロット）で鳴らせるのが約 3.2〜3.8 秒なので、
  映像をいくら減らしても「動画の長さ ÷ 約 3.6 秒」のバンクが要る（4 本の合計 約 860 秒 → 約 240 バンク + コマ戻し用）

#### 決定（同日、ユーザー）
- **ORIGEN だけ 24.9kHz、ほかは 11.2kHz の今の ROM（254 バンク）で確定**
- ジッタはエミュレータ依存と判明。puNES では理想の再生音（WAV）とほぼ同じ音質で鳴る
- 「画像と音声はトレードオフ」がよくわかるサンプルとして、動画ごとに設定が違うままにしておく
- 追加: メニューの BGM も 24.9kHz に（`menu` に `"audio_rate": 14`）。メニューは 6 → 7 バンク、ROM は **256 バンク（空きなし）**。
  `.\build_video.ps1 -Test` で 29 項目 + 音声（6,768 フレーム全部正しい、ずれ −34〜+83ms、音飛び 0）OK
- 開発側: `.\build_video.ps1 -Test` で 29 項目 + 音声（6,768 フレーム、DMC が読むデータ全部正しい、ずれ −34〜+83ms、音飛び 0）OK
- ユーザー環境（Mesen で聞く）: 未確認

### 段階7: 配布用サンプル ROM（2026-11-24）
権利の確認が取れた素材（オープニング、メニュー画面と BGM、ORIGEN）だけを入れた、配布用の ROM を作れるようにした。

| ファイル | 内容 |
|---|---|
| `tools/video/titles_sample.json` | 配布用の設定。titles.json と同じで、TK REMIX と 2026AI を `"enabled": false` にしたもの |
| `build_video.ps1` | `-Sample`（配布用をビルド）、`-Config`・`-Out`（設定と出力先を指定）を追加。変換結果は `build\video\cache` を共用 |
| `nes/src/video.s` | メニューで、入っていない動画の名前を出さない（今までは灰色で出していた）。カーソルも入っていない動画を飛ばす |
| `tools/video/make_video_rom.py` | `--cache-dir` を追加。`--mesen-test` は OPENING と ORIGEN があれば動き、グレー・カラーの確認は入っている動画だけ |
| `tools/video/check_audio.py` | 音声の確認も、入っている動画だけを再生する |

#### 起動方法
```powershell
.\build_video.ps1 -Sample -Test
.\run_mesen.ps1 -Rom build\sample\NES_BadPlayer_sample.nes
```

#### 確認結果
- `build\sample\NES_BadPlayer_sample.nes`: 3,080,208 バイト（94 バンク）。OPENING 2 + ORIGEN 77 + メニュー 7 バンク
- 開発側: 25 項目 + 音声（4,229 フレーム、DMC が読むデータ全部正しい、ずれ −23〜+25ms、音飛び 0）OK
- 気づいたこと: メニューでカーソルを動かすたびに背景が 1/60 秒止まり、BGM が映像より約 17ms 先に進む（前からの動き）。
  最初のテストでは、入っていない動画を探して ↓ を 8 回押したため +124ms までずれて NG になった。テストを直して解決。
  背景が最後まで行って最初に戻ると、ずれはなくなる
- ユーザー環境: 未確認
