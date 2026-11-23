# LuaNES + ブリッジでの起動手順

[LuaNES](https://github.com/nico-abram/LuaNES)（MIT ライセンス）は Lua で書かれたファミコンエミュレータで、
**LÖVE（love2d）** というゲームエンジンの上で動きます。

LuaNES にはプラグイン機構や外部と通信する仕組みがありません。そこで、LuaNES 本体には手を加えずに、
**自作の起動用ホスト（`tools/luanes_host`）が LuaNES を読み込み、その中に `bridge.lua` を差し込む**
方式にしています。

```
run_luanes.ps1
   └─ love.exe  tools\luanes_host  build\phase1.nes
         ├─ LuaNES の main.lua をそのまま読み込む（エミュレータ本体）
         └─ bridge.lua を読み込み、1フレームごとに呼ぶ
               └─ state.bin（お絵描き GUI）⇔ CPU RAM のメールボックス ⇔ ROM
```

---

## 1. 準備（初回のみ）

### 1.1 LÖVE をインストール
LuaNES は LÖVE **11.x** 用です（`love.errorhandler` などの 11 系の API を使っているため）。

- 公式サイト https://love2d.org/ から Windows 版（64bit インストーラ）を入れる。
  既定の場所 `C:\Program Files\LOVE\love.exe` にインストールすれば、スクリプトが自動で見つけます。
- scoop を使う場合（この PC には scoop があります）:

```powershell
scoop bucket add extras
```
```powershell
scoop install love
```

### 1.2 LuaNES を取得
`D:\hobby\nes\LuaNES` に clone します。この場所なら `run_luanes.ps1` が自動で見つけます。

```powershell
git clone https://github.com/nico-abram/LuaNES.git D:\hobby\nes\LuaNES
```

フォルダ構成はこうなります:
```
D:\hobby\nes\
├─ bin\ ...           ← cc65
├─ LuaNES\            ← エミュレータ（改造しない）
└─ project\
   ├─ run_luanes.ps1  ← 起動スクリプト
   └─ tools\luanes_host\main.lua, conf.lua  ← ブリッジを差し込むホスト
```

---

## 2. 起動

PowerShell で `project` フォルダに移動して、次の順に実行します。

1. **お絵描きツールを起動**（PowerShell の別ウィンドウで）

```powershell
.\run_paint.ps1
```

2. **LuaNES をブリッジ付きで起動**

```powershell
.\run_luanes.ps1
```

- `build\phase1.nes` が無ければ、先に自動でビルドします。
- エミュレータの画面と、ログ用のコンソールウィンドウが開きます。
  コンソールに `bridge: ROM に接続しました` と出れば成功です。
- GUI で描いた絵が LuaNES の画面に反映され、LuaNES でキーを押すと GUI 右下にパッドの状態が表示されます。

### 2.1 LuaNES のキー操作

| キー | ファミコンのボタン |
|---|---|
| W / A / S / D | 上 / 左 / 下 / 右 |
| O | A |
| P | B |
| I | SELECT |
| Enter | START |

### 2.2 オプション

```powershell
.\run_luanes.ps1 -Rom build\phase1.nes -LuaNES D:\hobby\nes\LuaNES -Love "C:\Program Files\LOVE\love.exe"
```

| 引数 | 既定値 | 内容 |
|---|---|---|
| `-Rom` | `build\phase1.nes` | 起動する ROM |
| `-LuaNES` | `..\LuaNES` | LuaNES を clone したフォルダ |
| `-Love` | 自動検出（PATH の `lovec`/`love`、`Program Files\LOVE`） | `love.exe` のパス |
| `-BridgeDir` | `build\bridge` | state.bin / pad.txt の場所（GUI の `--bridge` と合わせる） |

### 2.3 手動で起動する場合
スクリプトを使わずに起動するときは、次のようにします（`project` フォルダで実行）。

```powershell
$env:LUANES_DIR = 'D:\hobby\nes\LuaNES'; & 'C:\Program Files\LOVE\love.exe' tools\luanes_host build\phase1.nes
```

---

## 3. 注意点・制限

| 項目 | 内容 |
|---|---|
| 速度 | LuaNES は Lua だけで書かれたエミュレータなので、PC によっては実機より遅くなります（画面左上に Tick Rate が表示されます。60 前後なら実速） |
| リセット | LuaNES にはリセットキーがありません。ウィンドウを閉じて起動し直してください。ブリッジは再接続時に全データを送り直します |
| マッパー | LuaNES の UNROM（mapper 2）にはバンク切り替えの不具合があります（切り替えても中身が変わらない）。第1フェーズの ROM は切り替えないので影響しませんが、第3フェーズのスライドショー ROM は切り替えるため、`tools/luanes_host/main.lua` が起動時に正しい処理へ差し替えます（コンソールに「UNROM のバンク切り替えを修正しました」と出ます）。LuaNES を `love` で直接起動した場合は直らないので、必ず `run_luanes.ps1` から起動してください |
| 上書きしない | LuaNES 本体のファイルは一切変更しません。LuaNES を `git pull` で更新しても、ホスト側はそのまま使えます（LuaNES の内部構造が大きく変わった場合を除く） |

---

## 4. トラブルシューティング

| 症状 | 対処 |
|---|---|
| `LÖVE (love.exe) が見つかりません` | LÖVE をインストールするか、`-Love` で love.exe のパスを指定 |
| `LuaNES が見つかりません` | 1.2 の git clone を実行、または `-LuaNES` でフォルダを指定 |
| LÖVE の青いエラー画面に `attempt to call ... jit` など | LÖVE 11.x（LuaJIT 搭載版）を使っているか確認 |
| `state.bin がまだありません` とコンソールに出る | お絵描きツールが起動していないか、`-BridgeDir` と GUI の `--bridge` が違う |
| 絵が反映されない | GUI の「エミュレータへ自動送信」が ON か確認 |

---

## 5. 検証状況（2026-11-22 時点）

| 項目 | 状況 |
|---|---|
| LuaNES 本体 + ホスト + bridge.lua + phase1.nes | ✅ LuaJIT 2.1 上で本物の LuaNES を動かして確認（LÖVE の描画部分だけ模擬）。GUI の絵を送り、LuaNES が描いた画面の 128×120 ドットすべてが GUI の絵と一致 |
| pad.txt の書き出し | ✅ |
| 実際の LÖVE ウィンドウでの表示・キー入力 | ✅ ユーザー環境で動作確認済み（2026-11-22） |
