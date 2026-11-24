#!/usr/bin/env python3
"""連番 BMP から、ファミコンでフルサイズ再生する動画 ROM を作る（フェーズ2.x）。

tools/video/titles.json に並べた動画（タイトル 4 本 + メニューの背景）を 1 つの ROM にまとめる。

仕組み（詳しくは docs/PHASE2X.md）
  - 1 フレーム（1/60 秒）ごとに「VBlank 中に PPU へ書くデータ」= パケットを 1 個用意する。
  - パターンテーブル（256 タイル）をキャッシュとして使い、画面に必要なタイルの絵だけを書き足す。
  - 1 回の VBlank で書ける量を超えないよう、「直る量 ÷ 使うバイト数」が大きいものから選ぶ。
  - ROM の中では「前に書いた場所から何マス先か」で位置を表して小さくし、ファミコン側で展開する。
  - コマ戻し用に、数秒ごとに PPU の中身まるごと（スナップショット）も入れておく。
  - 音声は DPCM。各バンクの後ろの方に、そのバンクを読んでいる間に鳴る音を決まった位置に入れる。

モード（titles.json の "mode"）
  mono : 白黒 2 値 / gray : 4 段階のグレー / color: 場面ごとのパレット 4 組
  pal4 : 決まった 4 色（"colors" で指定）。各点をいちばん近い色にする（フル解像度）

マッパー: BNROM（mapper 34 / NES 2.0 submapper 2、32KB 単位で切り替え、CHR-RAM 8KB、最大 256 バンク = 8MB）
  各 32KB バンク = データ $8000-$EFFF (28KB) + プレイヤーのコード $F000-$FFFF (4KB、全バンク共通)
  データ = 映像のパケット（前から）+ 音声のスロット（後ろから、動画ごとに数を決める）
  バンク 0 は目録（どの動画がどこにあるか）、スプライト文字、スナップショット表。

使い方:
  python tools/video/make_video_rom.py --code build/video/player.bin
"""
import argparse
import hashlib
import json
import os
import pickle
import sys
import time

import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
NES_FPS = 60.0988           # NTSC のフレームレート
DATA_SIZE = 0x7000          # 1 バンクのデータ領域（$8000-$EFFF）
CODE_SIZE = 0x1000          # プレイヤーのコード（$F000-$FFFF）
MAX_RUN = 63                # 1 ランの最大バイト数（プレイヤーの展開ループと合わせる）
BUF_LIMIT = 224             # スタックページに置けるバッファの大きさ（$0100-$01DF）
CYCLE_LIMIT = 2040          # VBlank 中の書き込みに使ってよいサイクル数（FPS 表示の書き換え分を残す）
RUN_CYCLES = 27             # 1 ランの手間（アドレス設定など）
BYTE_CYCLES = 8             # 1 バイトの書き込み（pla / sta $2007）
END_CYCLES = 7              # 終端の判定
ALL_ONES = np.uint64(0xFFFFFFFFFFFFFFFF)
UPLOAD_FIXED_CYCLES = 12    # 絵の枚数を読む手間
UPLOAD_CYCLES = 91          # 白黒の絵 1 枚（番号からアドレスを引く + 8 バイト）
UPLOAD_BUF = 9              # 白黒の絵 1 枚がバッファ / ROM で使うバイト数
UPLOAD_CYCLES2 = 155        # 4 段階の絵 1 枚（16 バイト）
UPLOAD_BUF2 = 17
MERGE_GAP = 1               # ネームテーブルで、間が何マス以内なら 1 つのランにまとめるか
ENCODER_VERSION = 5         # 変えるとエンコード結果のキャッシュを使わなくなる

# パケットの終端 $F0 | フラグ（ROM の中でのネームテーブルの書き方は emit_nt を参照）
TERM_NORMAL, TERM_NEXT_BANK, TERM_END = 0xF0, 0xF1, 0xF2
TERM_FRAME_END = 0x08       # このパケットで 1 コマ分が終わる（コマ送り用）
OP_SKIP, OP_PAL2, OP_PAL16 = 0xC0, 0xC1, 0xC2

# バンク 0
CATALOG_TITLES = 0x10       # 目録: $8010 + 番号*48
ENTRY_SIZE = 48
FONT_AT = 0x100             # スプライト文字（$8100-）
SNAP_TABLE = 0x400          # スナップショット表（$8400-）
NSTREAMS = 5                # タイトル 4 本 + メニュー
MENU_ID = 4

# 音声（DPCM）
CPU_HZ = 1789772.727
DMC_PERIODS = [428, 380, 340, 320, 286, 254, 226, 214, 190, 160, 142, 128, 106, 84, 72, 54]
SEG_BYTES = 497             # 1 セグメント = DMC の長さ 16*31+1 バイト（ループ再生で自動的に次へ）
AUDIO_SLOT = 512            # 1 セグメントの置き場（64 の倍数）
CHUNKS_PER_SEG = 8          # 途中から鳴らすときは 64 バイト単位
FRAME2 = 59561              # 1 フレーム = 29780.5 CPU サイクル（の 2 倍）
#   音の振れ幅（64 ± audio_swing、既定 56）と 1 bit 列の決め方（audio_enc）は titles.json で選ぶ。
#   audio_enc: "greedy" = 目標より低ければ上げる（既定）/ "viterbi" = 差の 2 乗和が最小（dpcm_viterbi）。
#   viterbi・振れ幅 28 は数字上の誤差は減るが、小さい音のときの「ピー」というキャリアの音が目立った（段階6）
AUDIO_TOL_FRAMES = 6        # バンクに入れる音声の時間の余裕


class AudioCfg:
    """動画 1 本分の音声の設定（rate と、1 バンクに置くスロットの数）"""
    def __init__(self, rate, nslots):
        self.rate, self.nslots = rate, nslots
        self.period = DMC_PERIODS[rate]
        self.seg2 = SEG_BYTES * 8 * self.period * 2        # 1 セグメントの長さ（CPU サイクルの 2 倍）
        self.chunk2 = 64 * 8 * self.period * 2
        self.off = DATA_SIZE - nslots * AUDIO_SLOT          # バンク内の音声の置き場の先頭
        self.addr = 0x8000 + self.off

    def seg_window(self, p0, p1):
        """パケット p0〜p1 を読む間に DMC が読みうるセグメント番号の範囲（両端を含む）"""
        t0 = max(0, (p0 - AUDIO_TOL_FRAMES) * FRAME2)
        t1 = (p1 + AUDIO_TOL_FRAMES) * FRAME2
        return t0 // self.seg2, t1 // self.seg2

    def clock_at(self, p):
        """パケット p を作る直前のプレイヤーの音声時計 (acc, slot)"""
        t = p * FRAME2
        return t % self.seg2, (t // self.seg2) % self.nslots


# ---------------------------------------------------------------- フレーム読み込み
#
# どのモードでも、1 コマ = 960 マスの「絵」（plane0, plane1 の 64bit 2 つ）+ 属性 64 バイト + パレット 16 バイト。
#   mono : 白黒 2 値。plane1 は常に 0、属性も 0。パレットは黒・白（エンコーダが白黒反転に使う）
#   gray : 4 段階のグレー。パレットは $0F $00 $10 $30 で固定
#   color: 場面ごとにパレット 4 組を選び、16x16 ドットごとに 1 組を割り当てる（第2フェーズと同じ考え方）
#   pal4 : titles.json の "colors"（ファミコンの色番号 4 つ）で固定。各点を Lab でいちばん近い色にする

GRAY_PAL = bytes([0x0F, 0x00, 0x10, 0x30] * 4)
GRAY_PAL_INV = bytes([0x30, 0x10, 0x00, 0x0F] * 4)
MONO_PAL = bytes([0x0F, 0x30, 0x0F, 0x30] * 4)


class Frames:
    def __init__(self, mode, t0, t1, attr, pal_id, palettes, fps):
        self.mode, self.t0, self.t1, self.attr = mode, t0, t1, attr
        self.pal_id, self.palettes, self.fps = pal_id, palettes, fps

    def __len__(self):
        return len(self.t0)

    def pal(self, f):
        return self.palettes[int(self.pal_id[f])]


def planes_from_index(idx):
    """(240,256) の 0-3 → (plane0, plane1) それぞれ 960 個の 64bit"""
    out = []
    for bit in (1, 2):
        b = (idx & bit) != 0
        packed = np.packbits(b.reshape(30, 8, 32, 8), axis=3)[..., 0].transpose(0, 2, 1).reshape(960, 8)
        out.append(packed.view(">u8").reshape(960).astype(np.uint64))
    return out


def list_frames(folder):
    files = sorted(f for f in os.listdir(folder) if f.lower().endswith((".bmp", ".png")))
    if not files:
        sys.exit(f"画像が見つかりません: {folder}")
    return [os.path.join(folder, f) for f in files]


def fill_gaps(files):
    """連番の抜け（intro149 の次が intro151 など）を、直前のコマを繰り返して埋める"""
    import re
    out, prev = [], None
    for f in files:
        m = re.search(r"(\d+)\.\w+$", f)
        n = int(m.group(1)) if m else None
        if prev is not None and n is not None and prev[0] is not None:
            out += [prev[1]] * max(0, n - prev[0] - 1)
        out.append(f)
        prev = (n, f)
    return out


def _font(name, size):
    from PIL import ImageFont
    for path in (os.path.join(os.environ.get("WINDIR", r"C:\Windows"), "Fonts", name), name):
        try:
            return ImageFont.truetype(path, size)
        except OSError:
            pass
    sys.exit(f"フォントが見つかりません: {name}")


def append_frames(last, items, fps, cache_dir):
    """titles.json の "append": 動画の後ろに足すコマ（最後のコマで止める・文字を出す）→ BMP のパスのリスト

    1 件ごとに:
      "nes_frames": ファミコンの何フレーム（1/60 秒）分か / または "seconds"
      "base"      : "last"（動画の最後のコマの上に）/ "black"（黒い画面に）/ "prev"（1 つ前の件の絵の上に）
      "texts"     : [{"text": 文字, "y": 上端, "size": 大きさ, "font": フォント, "align": "center" / "left",
                      "x": 左端（left のとき）, "width": 折り返す幅（ドット）, "line": 行の高さ, "color": [r,g,b]}]
    文字はにじみ（アンチエイリアス）なしで書く（4 色に減らしたときに変な色が出ないように）。
    """
    from PIL import Image, ImageDraw
    key = hashlib.sha1(json.dumps([os.path.abspath(last), items], sort_keys=True).encode()).hexdigest()[:12]
    out_dir = os.path.join(cache_dir, f"append_{key}")
    os.makedirs(out_dir, exist_ok=True)
    out, prev = [], Image.open(last).convert("RGB")
    for i, it in enumerate(items):
        n = round(it["nes_frames"] * fps / NES_FPS) if "nes_frames" in it else round(it.get("seconds", 1) * fps)
        base = it.get("base", "prev")
        im = (Image.new("RGB", (256, 240), (0, 0, 0)) if base == "black" else
              Image.open(last).convert("RGB") if base == "last" else prev.copy())
        d = ImageDraw.Draw(im)
        d.fontmode = "1"
        for tx in it.get("texts", []):
            f = _font(tx.get("font", "tahoma.ttf"), int(tx.get("size", 11)))
            color = tuple(tx.get("color", [255, 255, 255]))
            lines = []
            for para in tx["text"].split("\n"):
                cur = ""
                for w in para.split():
                    trial = (cur + " " + w).strip()
                    if cur and d.textlength(trial, font=f) > tx.get("width", 224):
                        lines.append(cur)
                        cur = w
                    else:
                        cur = trial
                lines.append(cur)
            y = int(tx.get("y", 112))
            for ln in lines:
                if tx.get("align", "center") == "center":
                    d.text((128, y), ln, font=f, fill=color, anchor="ma")
                else:
                    d.text((int(tx.get("x", 16)), y), ln, font=f, fill=color)
                y += int(tx.get("line", int(tx.get("size", 11)) + 4))
        path = os.path.join(out_dir, f"append_{i:02d}.bmp")
        im.save(path)
        out += [path] * max(1, n)
        prev = im
    return out


def pal4_colors(t):
    """titles.json の "colors"（"0F" のような 16 進の文字列か数）→ 色番号 4 つ"""
    cols = [int(c, 16) if isinstance(c, str) else int(c) for c in t.get("colors", ["0F", "00", "10", "30"])]
    if len(cols) != 4 or not all(0 <= c < 64 for c in cols):
        sys.exit(f"{t.get('name')}: \"colors\" にはファミコンの色番号を 4 つ書いてください")
    return cols


def open_256x240(path, mode, x, bg):
    from PIL import Image
    im = Image.open(path).convert("RGB" if mode in ("color", "pal4") else "L")
    if im.size[1] != 240:
        im = im.resize((max(1, round(im.size[0] * 240 / im.size[1])), 240), Image.LANCZOS)
    if im.size[0] > 256:
        im = im.resize((256, 240), Image.LANCZOS)
    if im.size[0] < 256:
        canvas = Image.new(im.mode, (256, 240), bg if im.mode == "L" else (bg, bg, bg))
        left = (256 - im.size[0]) // 2 if x == "center" else int(x)
        canvas.paste(im, (left, 0))
        im = canvas
    return np.asarray(im)


def load_frames(t, folder, fps, cache_dir, max_frames=None):
    mode = t.get("mode", "mono")
    step = max(1, int(t.get("step", 1)))          # 何コマおきに使うか（fps を下げる）
    x, bg = t.get("x", "center"), t.get("bg", 255)
    files = list_frames(folder)
    if t.get("fill_gaps"):
        files = fill_gaps(files)
    if t.get("append"):
        files = files + append_frames(files[-1], t["append"], fps, cache_dir)
    files = files[::step]
    fps = fps / step
    if max_frames:
        files = files[:max_frames]
    opts = dict(mode=mode, step=step, hyst=1, x=x, bg=bg, n=len(files), th=t.get("threshold", 128),
                levels=t.get("levels", [48, 128, 208]), scene=t.get("scene_threshold", 40),
                pixel=t.get("pixel", 1), min_scene=t.get("min_scene_frames", 30),
                v=2)
    if mode == "pal4":
        opts["colors"] = pal4_colors(t)
        opts["fill_gaps"] = bool(t.get("fill_gaps"))
    if t.get("append"):
        opts["append"] = t["append"]
    key = hashlib.sha1(json.dumps([os.path.basename(os.path.normpath(folder)), opts], sort_keys=True).encode()).hexdigest()[:16]
    cache = os.path.join(cache_dir, f"frames_{key}.npz")
    if os.path.exists(cache):
        z = np.load(cache, allow_pickle=True)
        return Frames(mode, z["t0"], z["t1"], z["attr"], z["pal_id"], list(z["palettes"]), fps)
    n = len(files)
    t0 = np.zeros((n, 960), dtype=np.uint64)
    t1 = np.zeros((n, 960), dtype=np.uint64)
    attr = np.zeros((n, 64), dtype=np.uint8)
    pal_id = np.zeros(n, dtype=np.int32)
    tm = time.time()
    if mode == "color":
        palettes = quantize_color(files, t, x, bg, t0, t1, attr, pal_id, opts)
    elif mode == "pal4":
        nc, _, _ = _nes_colors()
        import nes_palette
        table = nes_palette.builtin_rgb()
        cols = opts["colors"]
        lab4 = nc.rgb_to_lab(np.array([table[c] for c in cols], dtype=np.float64))
        palettes = [bytes(cols * 4)]
        for i, path in enumerate(files):
            lab = nc.rgb_to_lab(open_256x240(path, mode, x, bg).reshape(-1, 3).astype(np.float64))
            idx = ((lab[:, None, :] - lab4[None]) ** 2).sum(2).argmin(1).astype(np.uint8).reshape(240, 256)
            t0[i], t1[i] = planes_from_index(idx)
    else:
        palettes = [MONO_PAL if mode == "mono" else GRAY_PAL]
        for i, path in enumerate(files):
            g = open_256x240(path, mode, x, bg).astype(np.int16)
            if mode == "mono":
                idx = (g >= opts["th"]).astype(np.uint8)
            else:
                idx = np.digitize(g, opts["levels"]).astype(np.uint8)
            t0[i], t1[i] = planes_from_index(idx)
            if i % 1000 == 0:
                print(f"    読み込み {i}/{n}  ({time.time() - tm:.0f} 秒)")
    os.makedirs(cache_dir, exist_ok=True)
    np.savez(cache, t0=t0, t1=t1, attr=attr, pal_id=pal_id, palettes=np.array(palettes, dtype=object))
    return Frames(mode, t0, t1, attr, pal_id, palettes, fps)


# ---- カラー: 場面ごとのパレット

def _nes_colors():
    sys.path.insert(0, os.path.join(ROOT, "tools", "converter"))
    import nes_convert
    import nes_palette
    table = nes_palette.builtin_rgb()
    cands = nes_convert.candidate_colors(table, 0x0F)
    rgb = np.array([table[i] for i in cands], dtype=np.float64)
    return nes_convert, cands, nes_convert.rgb_to_lab(rgb)


def _scene_cuts(files, x, bg, threshold, min_len):
    """小さく縮めたコマどうしの違いで場面の切れ目を探す（短すぎる場面は作らず、長すぎる場面は 180 コマで切る）"""
    from PIL import Image
    prev, cuts = None, [0]
    for i, path in enumerate(files):
        im = Image.fromarray(open_256x240(path, "color", x, bg)).resize((32, 30), Image.BOX)
        a = np.asarray(im, dtype=np.float64)
        if prev is not None and ((np.abs(a - prev).mean() > threshold and i - cuts[-1] >= min_len)
                                 or i - cuts[-1] > 180):
            cuts.append(i)
        prev = a
    return cuts + [len(files)]


def _solve_palettes(nc, lab_c, bg_i, X, blk, nblk, iterations=8):
    """画素 X（Lab）とそのブロック番号から、背景色 + 3 色 × 4 組を選ぶ"""
    D = ((X[:, None, :] - lab_c[None, :, :]) ** 2).sum(2)
    dbg = D[:, bg_i]
    cnt = np.maximum(np.bincount(blk, minlength=nblk), 1).astype(np.float64)
    means = np.stack([np.bincount(blk, weights=X[:, k], minlength=nblk) / cnt for k in range(3)], 1)
    centers = [means[np.argmax(((means - means.mean(0)) ** 2).sum(1))]]
    while len(centers) < 4:
        d = np.min([((means - c) ** 2).sum(1) for c in centers], axis=0)
        centers.append(means[int(d.argmax())])
    assign = ((means[:, None, :] - np.array(centers)[None]) ** 2).sum(2).argmin(1)
    pals = [[] for _ in range(4)]
    for _ in range(iterations):
        pix = assign[blk]
        for p in range(4):
            m = pix == p
            pals[p] = nc._greedy_palette(D[m] if m.any() else D, dbg[m] if m.any() else dbg, bg_i,
                                         refine=1 if m.any() else 0)
        E = np.stack([np.bincount(blk, weights=np.minimum(dbg, D[:, pals[p]].min(1)), minlength=nblk)
                      for p in range(4)])
        new = E.argmin(0)
        if np.array_equal(new, assign):
            break
        assign = new
    L = lab_c[:, 0]
    return [sorted(pl, key=lambda c: L[c]) for pl in pals]


def quantize_color(files, t, x, bg, t0, t1, attr, pal_id, opts):
    from PIL import Image
    nc, cands, lab_c = _nes_colors()
    bg_i = cands.index(0x0F)
    cuts = _scene_cuts(files, x, bg, opts["scene"], opts["min_scene"])
    px = int(opts["pixel"])                                           # 1 = そのまま / 4 = 4x4 ドットのモザイク
    gw, gh = 256 // px, 240 // px
    bs = 16 // px                                                     # 16x16 ドットのブロックが何マス分か
    print(f"    場面 {len(cuts) - 1} 個")
    by, bx = np.mgrid[0:gh, 0:gw]
    blk_full = ((by // bs) * 16 + (bx // bs)).ravel()             # 15x16 = 240 ブロック
    sw, sh = (128, 120) if px == 1 else (gw, gh)
    sy, sx = np.mgrid[0:sh, 0:sw]
    blk_half = ((sy // (sh // 15)) * 16 + (sx // (sw // 16))).ravel()
    palettes = []
    tm = time.time()
    for s in range(len(cuts) - 1):
        a, b = cuts[s], cuts[s + 1]
        sample = np.linspace(a, b - 1, min(8, b - a)).round().astype(int)
        Xs, Bs = [], []
        for k, fi in enumerate(sample):
            im = Image.fromarray(open_256x240(files[fi], "color", x, bg)).resize((sw, sh), Image.BOX)
            Xs.append(nc.rgb_to_lab(np.asarray(im, dtype=np.float64).reshape(-1, 3)))
            Bs.append(blk_half + k * 240)
        pals = _solve_palettes(nc, lab_c, bg_i, np.concatenate(Xs), np.concatenate(Bs), 240 * len(sample))
        palettes.append(bytes(sum(([0x0F] + [cands[c] for c in pl] for pl in pals), [])))
        S = lab_c[np.array([[bg_i] + pl for pl in pals])]          # (4 組, 4 色, 3)
        prev_assign = prev_idx = prev_pal = None
        for fi in range(a, b):
            src = open_256x240(files[fi], "color", x, bg)
            if px > 1:
                src = np.asarray(Image.fromarray(src).resize((gw, gh), Image.BOX))
            X = nc.rgb_to_lab(src.reshape(-1, 3).astype(np.float64))
            D = ((X[:, None, None, :] - S[None]) ** 2).sum(3)          # (画素, 4 組, 4 色)
            best = D.min(2)                                          # (画素, 4 組)
            E = np.stack([np.bincount(blk_full, weights=best[:, p], minlength=240) for p in range(4)], 1)
            assign = E.argmin(1)
            if prev_assign is not None:                              # ちらつき防止: 1 割以上良くならなければ前のまま
                keep = E[np.arange(240), prev_assign] <= E[np.arange(240), assign] * 1.1
                assign = np.where(keep, prev_assign, assign)
            prev_assign = assign
            Dp = D[np.arange(gw * gh), assign[blk_full], :]
            idx = Dp.argmin(1)
            if prev_idx is not None:                                 # ちらつき防止: ほとんど同じなら前の色のまま
                pk = Dp[np.arange(gw * gh), prev_idx]
                keep = (prev_pal == assign[blk_full]) & (pk <= Dp[np.arange(gw * gh), idx] * 1.3 + 30)
                idx = np.where(keep, prev_idx, idx)
            prev_idx, prev_pal = idx, assign[blk_full]
            idx = idx.astype(np.uint8).reshape(gh, gw)
            if px > 1:
                idx = np.repeat(np.repeat(idx, px, 0), px, 1)
            t0[fi], t1[fi] = planes_from_index(idx)
            g = np.zeros((16, 16), dtype=np.uint8)
            g[:15] = assign.reshape(15, 16)
            attr[fi] = (g[0::2, 0::2] | (g[0::2, 1::2] << 2) | (g[1::2, 0::2] << 4) | (g[1::2, 1::2] << 6)).reshape(64)
            pal_id[fi] = s
            if fi % 500 == 0:
                print(f"    カラー変換 {fi}/{len(files)}  ({time.time() - tm:.0f} 秒)")
    return palettes


# ---------------------------------------------------------------- エンコーダ

class PPUState:
    def __init__(self, palette):
        self.nt = np.zeros(1024, dtype=np.int32)       # ネームテーブル 960 + 属性 64
        self.pt0 = np.zeros(256, dtype=np.uint64)      # 各タイル番号の絵（plane0 / plane1）
        self.pt1 = np.zeros(256, dtype=np.uint64)
        self.ref = np.zeros(256, dtype=np.int32)       # そのタイルを使っているマスの数
        self.ref[0] = 960
        self.last = np.full(256, -1, dtype=np.int64)   # 最後に使ったパケット番号（追い出し順）
        self.lookup = {0: 0}                           # 絵 → タイル番号
        self.pal = bytes(palette)

    def copy(self):
        s = PPUState.__new__(PPUState)
        s.nt, s.pt0, s.pt1, s.ref, s.last = (self.nt.copy(), self.pt0.copy(), self.pt1.copy(),
                                            self.ref.copy(), self.last.copy())
        s.lookup = dict(self.lookup)
        s.pal = self.pal
        return s


def popcount(a):
    return np.bitwise_count(a).astype(np.int32)


def tile_err(a0, a1, b0, b1):
    """2 つの絵の違い（4 段階の値の差の 2 乗の合計）。白黒（plane1 = 0）なら違う点の数"""
    d0, d1 = a0 ^ b0, a1 ^ b1
    both = d0 & d1
    same = ~(a0 ^ a1)
    return (popcount(d0 & ~d1) + 4 * popcount(d1 & ~d0) + 9 * popcount(both & same)
            + popcount(both & ~same))


def tkey(p0, p1):
    return (int(p1) << 64) | int(p0)


def build_runs(st, writes):
    """writes: {マス: 新しい値}（0-959 = ネームテーブル、960-1023 = 属性）→ ラン [(addr, bytes)]
    すぐ近くのマス（間が MERGE_GAP 以内）は 1 つのランにまとめ、間のマスには今の値を書き直す"""
    runs = []
    cells = sorted(writes)
    i = 0
    while i < len(cells):
        start = end = cells[i]
        i += 1
        while i < len(cells) and cells[i] - end <= MERGE_GAP + 1 and cells[i] - start < MAX_RUN:
            end = cells[i]
            i += 1
        data = bytes(writes.get(c, int(st.nt[c])) for c in range(start, end + 1))
        runs.append((0x2000 + start, data))
    return runs


def packet_cost(n_uploads, runs, two_planes):
    """(VBlank 中のサイクル数, NMI 用バッファのバイト数)"""
    uc, ub = (UPLOAD_CYCLES2, UPLOAD_BUF2) if two_planes else (UPLOAD_CYCLES, UPLOAD_BUF)
    cyc = UPLOAD_FIXED_CYCLES + uc * n_uploads + END_CYCLES
    cyc += sum(RUN_CYCLES + BYTE_CYCLES * len(d) for _, d in runs)
    buf = 1 + ub * n_uploads + sum(4 + len(d) for _, d in runs) + 1
    return cyc, buf


def emit_nt(runs):
    """ラン → ROM の中の書き方。「前に書き終えた場所から何マス先か」で位置を表す（$2000 のマス 0 から数える）
      $00-$7F s, v      : s マス先の 1 マスに v
      $80-$BF s, v...   : s マス先から (下位 6bit + 2) マスに v...
      $C0 s             : s マス進むだけ
      $C1 a b / $C2 x16 : パレット（$3F00 から 2 / 16 バイト）"""
    out = bytearray()
    cur = 0
    for addr, data in runs:
        if addr == 0x3F00:
            out += bytes([OP_PAL2 if len(data) == 2 else OP_PAL16]) + data
            continue
        cell = addr - 0x2000
        skip = cell - cur
        assert skip >= 0
        while skip > 127:
            s = min(skip, 255)
            out += bytes([OP_SKIP, s])
            skip -= s
        if len(data) == 1:
            out += bytes([skip, data[0]])
        else:
            assert 2 <= len(data) <= 63
            out += bytes([0x80 | (len(data) - 2), skip]) + data
        cur = cell + len(data)
    return out


def plan_packet(st, g0, g1, pkt_no, cyc_budget, buf_budget, rom_budget, tol, two_planes, forced=()):
    """st を書き換えながら、このパケットで書く内容を決める。

    マスごとに 2 つの直し方がある:
      A) いまパターンテーブルにある一番近い絵に差し替える（ネームテーブル 1 マス）
      B) 正しい絵を新しく書き込んで差し替える（絵 9 / 17 バイト + 1 マス）
    「直る量 ÷ 使う ROM のバイト数」が大きいものから順に、予算（VBlank の時間・バッファ・ROM）が尽きるまで選ぶ。
    forced: 必ず書くマス（属性）{マス: 値}
    """
    ub = UPLOAD_BUF2 if two_planes else UPLOAD_BUF
    uc = UPLOAD_CYCLES2 if two_planes else UPLOAD_CYCLES
    writes, uploads, uploaded = dict(forced), [], {}
    sel_mask = np.zeros(1024, dtype=bool)
    cyc = UPLOAD_FIXED_CYCLES + END_CYCLES
    buf = 2
    rom = 2
    for c in sorted(forced):
        near = sel_mask[max(0, c - MERGE_GAP - 1):c + MERGE_GAP + 2].any()
        cyc += BYTE_CYCLES + (0 if near else RUN_CYCLES)
        buf += 1 + (0 if near else 4)
        rom += 1 if near else 2
        sel_mask[c] = True
        st.nt[c] = forced[c]
    nt = st.nt[:960]
    err = tile_err(st.pt0[nt], st.pt1[nt], g0, g1)
    cand = np.nonzero(err)[0]
    if len(cand) == 0:
        return writes, uploads
    dist = tile_err(g0[cand][:, None], g1[cand][:, None], st.pt0[None, :], st.pt1[None, :])
    best_d = dist.min(axis=1)
    e = err[cand]
    gain_a = (e - best_d) / 1.8
    gain_b = np.where(best_d > tol, e / (ub + 1.8), 0.0)
    score = np.maximum(gain_a, gain_b)
    order = np.argsort(-score, kind="stable")

    reserved = set()
    free_cache = None
    for k in order:
        if score[k] <= 0:
            break
        c = int(cand[k])
        p0, p1 = g0[c], g1[c]
        key = tkey(p0, p1)
        cur_err = int(err[c])
        need_upload = False
        slot = st.lookup.get(key)
        if slot is None:
            slot = uploaded.get(key)
        if slot is None:
            d = tile_err(st.pt0, st.pt1, p0, p1)       # このパケットで書き換えた絵も反映
            s = int(np.argmin(d))
            ds = int(d[s])
            want_upload = ds > tol and cur_err / (ub + 1.8) > (cur_err - ds) / 1.8
            if want_upload:
                if not free_cache:
                    free_cache = sorted((int(f) for f in np.nonzero(st.ref == 0)[0] if int(f) not in reserved),
                                        key=lambda f: st.last[f])
                while free_cache and (st.ref[free_cache[0]] != 0 or free_cache[0] in reserved):
                    free_cache.pop(0)
                if free_cache:
                    slot = free_cache[0]
                    need_upload = True
            if slot is None:
                if ds >= cur_err:
                    continue
                slot = s
        if slot == int(st.nt[c]):
            continue
        near = sel_mask[max(0, c - MERGE_GAP - 1):c + MERGE_GAP + 2].any()
        add_c = BYTE_CYCLES + (0 if near else RUN_CYCLES)
        add_b = 1 + (0 if near else 4)
        add_r = 1 if near else 2
        if need_upload:
            add_c += uc
            add_b += ub
            add_r += ub
        if cyc + add_c > cyc_budget or buf + add_b > buf_budget or rom + add_r > rom_budget:
            continue
        cyc += add_c
        buf += add_b
        rom += add_r
        sel_mask[c] = True
        if need_upload:
            free_cache.pop(0)
            old = tkey(st.pt0[slot], st.pt1[slot])
            if st.lookup.get(old) == slot:
                del st.lookup[old]
            st.pt0[slot], st.pt1[slot] = p0, p1
            st.lookup[key] = slot
            uploads.append((slot, int(p0), int(p1)))
            uploaded[key] = slot
            reserved.add(slot)
        prev = int(st.nt[c])
        st.ref[prev] -= 1
        st.ref[slot] += 1
        st.nt[c] = slot
        st.last[slot] = pkt_no
        writes[c] = slot
    return writes, uploads


def snap_bytes(st, mode):
    """コマ戻し用スナップショット: 白黒 = ネームテーブル 960 + 絵 256x8 + 反転 1 /
    それ以外 = ネームテーブル + 属性 1024 + 絵 256x16（CHR と同じ並び）+ パレット 16"""
    if mode == "mono":
        return (bytes(st.nt[:960].astype(np.uint8)) + b"".join(int(v).to_bytes(8, "big") for v in st.pt0)
                + bytes([1 if st.pal[0] == 0x30 else 0]))
    chr_ = b"".join(int(a).to_bytes(8, "big") + int(b).to_bytes(8, "big") for a, b in zip(st.pt0, st.pt1))
    return bytes(st.nt.astype(np.uint8)) + chr_ + st.pal


def snap_size(mode):
    return 960 + 2048 + 1 if mode == "mono" else 1024 + 4096 + 16


def encode(fr, tol, snap_every, kbps=None, log_every=2000):
    """fr: Frames → dict(packets=[(body, frame_end)], snaps=[(p, 終わったコマ数, bytes)], ...)
    kbps: 映像の 1 秒あたりの ROM のバイト数の上限（KB）。場面転換などでは最大 2 秒分まで先取りできる"""
    nframes = len(fr)
    fps = fr.fps
    mono = fr.mode == "mono"
    two = not mono
    npk = int(np.ceil(nframes * NES_FPS / fps))
    st = PPUState(MONO_PAL if mono else fr.pal(0))
    packets, snaps = [], []
    frame_err = np.zeros(nframes, dtype=np.int64)
    max_cyc = max_buf = 0
    rate = kbps * 1024 / NES_FPS if kbps else None
    bucket = rate * 60 if rate else 0
    done_frames = 0
    t0 = time.time()
    for p in range(npk):
        if p == 0 or (p - snaps[-1][0] >= snap_every and packets[-1][1]):
            snaps.append((p, done_frames, snap_bytes(st, fr.mode)))
        f = min(int(p * fps / NES_FPS), nframes - 1)
        f_next = int((p + 1) * fps / NES_FPS)
        g0, g1 = fr.t0[f], fr.t1[f]
        pre_runs = []
        if fr.mode in ("mono", "gray"):
            # 白黒（明暗）がほぼ反転した場面は、パレットを逆順にするだけで済ませる。
            # 絵の 2 つのビットを反転すると値 i が 3-i になるので、パレットも逆にすれば同じ見た目になる
            inv = st.pal[0] == 0x30
            nt = st.nt[:960]
            m0 = ALL_ONES if inv else np.uint64(0)
            m1 = m0 if two else np.uint64(0)
            s0, s1 = st.pt0[nt] ^ m0, st.pt1[nt] ^ m1
            e_now = int(tile_err(s0, s1, g0, g1).sum())
            f1 = ~s1 if two else s1
            if e_now > 3000 and int(tile_err(~s0, f1, g0, g1).sum()) < e_now * 0.6:
                inv = not inv
                if mono:
                    pre_runs = [(0x3F00, bytes([0x30, 0x0F] if inv else [0x0F, 0x30]))]
                    st.pal = bytes([0x30, 0x0F] * 8) if inv else MONO_PAL
                else:
                    st.pal = GRAY_PAL_INV if inv else GRAY_PAL
                    pre_runs = [(0x3F00, st.pal)]
            if inv:
                g0 = g0 ^ ALL_ONES
                if two:
                    g1 = g1 ^ ALL_ONES
        else:
            want = bytes(fr.pal(f))
            if want != st.pal:
                pre_runs = [(0x3F00, want)]
                st.pal = want
        forced = {}
        if fr.mode == "color":
            for i in np.nonzero(st.nt[960:] != fr.attr[f])[0]:
                forced[960 + int(i)] = int(fr.attr[f][i])
        cb, bb = CYCLE_LIMIT, BUF_LIMIT
        rb = 10 ** 9 if rate is None else max(4, int(bucket))
        for _, d in pre_runs:
            cb -= RUN_CYCLES + BYTE_CYCLES * len(d)
            bb -= 4 + len(d)
            rb -= 1 + len(d)
        while True:
            trial = st.copy()
            writes, uploads = plan_packet(trial, g0, g1, p, cb, bb, rb, tol, two, forced)
            runs = pre_runs + build_runs(st, writes)
            cyc, buf = packet_cost(len(uploads), runs, two)
            if cyc <= CYCLE_LIMIT and buf <= BUF_LIMIT:
                break
            cb, bb = cb - 40, bb - 6
        st = trial
        max_cyc, max_buf = max(max_cyc, cyc), max(max_buf, buf)
        body = bytearray([len(uploads) | (0x80 if two and uploads else 0)])
        for slot, a, b in uploads:
            body += bytes([slot]) + a.to_bytes(8, "big") + (b.to_bytes(8, "big") if two else b"")
        body += emit_nt(runs)
        if rate is not None:
            bucket = min(bucket - len(body) - 1 + rate, rate * 120)
        is_end = (f_next != f) or p == npk - 1
        packets.append((bytes(body), is_end))
        if is_end:
            done_frames += 1
            nt = st.nt[:960]
            frame_err[f] = int(popcount((st.pt0[nt] ^ g0) | (st.pt1[nt] ^ g1)).sum())   # 違う点の数（反転中は反転後どうしで比べる）
        if log_every and p % log_every == 0 and p:
            print(f"    エンコード {p}/{npk} パケット  ({time.time() - t0:.0f} 秒)")
    return dict(packets=packets, snaps=snaps, frame_err=frame_err, max_cyc=max_cyc, max_buf=max_buf,
                nframes=nframes, fps=fps, mode=fr.mode, palette0=MONO_PAL if mono else bytes(fr.pal(0)))


# ---------------------------------------------------------------- 音声（DPCM）

def dpcm_greedy(t):
    """1 bit ずつ: 目標より低ければ +2、高ければ -2（DMC と同じく 0〜127 の外には出ない）。長さ len(t)+1、先頭は 64"""
    lv, ll = 64, [64]
    for v in t.tolist():
        if v > lv:
            if lv <= 125:
                lv += 2
        elif lv >= 2:
            lv -= 2
        ll.append(lv)
    return np.array(ll, dtype=np.int16)


def encode_audio(path, n_packets, rate, enc="greedy", swing=56):
    """動画ファイルの音声 → DPCM のセグメント（1 個 AUDIO_SLOT バイト）のリスト

    セグメント = 1 bit の増減 497 バイト（DMC の長さ 16*31+1）+ 64 バイトごとの開始レベル 8 バイト + 余り
    再生時間の最後（映像の終わり + 2 秒）までセグメントを用意し、音声が足りない分は無音にする。
    """
    import subprocess
    period = DMC_PERIODS[rate]
    seg2 = SEG_BYTES * 8 * period * 2
    hz = CPU_HZ / period
    cmd = ["ffmpeg", "-v", "error", "-i", path, "-vn", "-ac", "1", "-ar", str(int(round(hz))),
           "-af", f"highpass=f=60,lowpass=f={int(min(10000, hz * 0.42))}", "-f", "f32le", "-"]   # 11.2kHz なら 4.7kHz
    raw = subprocess.run(cmd, stdout=subprocess.PIPE, check=True).stdout
    x = np.frombuffer(raw, dtype=np.float32).astype(np.float64)
    peak = np.percentile(np.abs(x), 99.9) if len(x) else 1.0
    x = np.clip(x / max(peak, 1e-6), -1, 1)
    target = 64 + x * swing
    bits_per_seg = SEG_BYTES * 8
    need_t2 = n_packets * FRAME2 + 2 * FRAME2 * 60
    nseg = int(need_t2 // seg2) + 2
    nbits = nseg * bits_per_seg
    t = np.full(nbits, 64.0)
    n = min(nbits, len(target))
    t[:n] = target[:n]
    levels = dpcm_viterbi(t) if enc == "viterbi" else dpcm_greedy(t)
    nxt, prv = levels[1:], levels[:-1]
    bits = ((nxt > prv) | ((nxt == prv) & (prv >= 126))).astype(np.uint8)   # 上がる / 126 に張り付き = 1
    segs = []
    for j in range(nseg):
        b = bits[j * bits_per_seg:(j + 1) * bits_per_seg]
        data = np.packbits(b.reshape(-1, 8)[:, ::-1], axis=1)[:, 0].tobytes()   # 1 バイトの中は下位ビットから鳴る
        starts = bytes(int(levels[j * bits_per_seg + k * 512]) for k in range(CHUNKS_PER_SEG))
        seg = data + starts
        segs.append(seg + b"\x00" * (AUDIO_SLOT - len(seg)))
    return segs


def dpcm_viterbi(t, chunk=4096, group=256):
    """目標の波形 t（0〜127）→ DMC のレベル列（長さ len(t)+1、先頭は 64）

    1 bit ずつ ±2 しか動けない DMC で、「目標との差の 2 乗和」が最小になる道をビタビ法で探す。
    （目標より低ければ上げる、という 1 つずつの決め方より、急な変化の前から準備できるぶん誤差が減る）
    レベル 0,2,…,126 の 64 状態。chunk サンプルごとに区切り、区切りのレベルは目標に近い 4 の倍数に固定する。
    こうすると区切りどうしが独立になるので、numpy でまとめて（group 区切りずつ）解ける。
    """
    n = len(t)
    nch = -(-n // chunk)
    tt = np.full(nch * chunk, 64.0)
    tt[:n] = t
    tt = tt.reshape(nch, chunk)
    # 区切りの状態（状態番号 = レベル/2。偶数にそろえ、chunk も偶数なので必ず届く）
    bnd = (np.clip(np.round(tt[:, 0] / 4), 0, 31) * 2).astype(np.int64)
    bnd[0] = 32
    ends = np.r_[bnd[1:], 32]
    S = np.arange(64) * 2.0
    pa = np.r_[0, np.arange(63)]            # bit 1 で来る元（s=0 は 0 で bit 0 の張り付きだが、元は同じ 0）
    pb = np.r_[np.arange(1, 64), 63]        # bit 0 で来る元（s=63 は 63 で bit 1 の張り付き）
    out = np.empty((nch, chunk), np.int16)
    for g0 in range(0, nch, group):
        g1 = min(nch, g0 + group)
        k = g1 - g0
        cost = np.full((k, 64), 1e18)
        cost[np.arange(k), bnd[g0:g1]] = 0.0
        back = np.empty((chunk, k, 64), np.uint8)
        seg = tt[g0:g1]
        for i in range(chunk):
            ca, cb = cost[:, pa], cost[:, pb]
            ta = ca <= cb
            back[i] = np.where(ta, pa, pb)
            cost = np.where(ta, ca, cb) + (S[None, :] - seg[:, i:i + 1]) ** 2
        # 終わりは次の区切りの状態に固定して、後ろからたどる
        s = ends[g0:g1].copy()
        rows = np.arange(k)
        for i in range(chunk - 1, -1, -1):
            out[g0:g1, i] = s * 2
            s = back[i, rows, s]
    lv = np.empty(n + 1, np.int16)
    lv[0] = 64
    lv[1:] = out.reshape(-1)[:n]
    return lv


def dmc_levels(segs):
    """セグメントの列を DMC と同じように鳴らしたときのレベル（プレビュー用）"""
    data = np.frombuffer(b"".join(s[:SEG_BYTES] for s in segs), dtype=np.uint8)
    bits = np.unpackbits(data.reshape(-1, 1), axis=1)[:, ::-1].reshape(-1).tolist()
    lv, out = 64, [0] * len(bits)
    for i, b in enumerate(bits):
        if b:
            if lv <= 125:
                lv += 2
        elif lv >= 2:
            lv -= 2
        out[i] = lv
    return np.array(out, dtype=np.int16)


# ---------------------------------------------------------------- ROM の組み立て

class Bank:
    """1 バンク分のデータ。video は $8000 から、音声はスロット（セグメント j は j mod nslots 番目）"""
    def __init__(self, limit=DATA_SIZE, acfg=None):
        self.video = bytearray()
        self.limit = limit
        self.acfg = acfg
        self.audio = {}

    def image(self):
        out = bytearray(self.video) + b"\xFF" * (DATA_SIZE - len(self.video))
        for slot, data in self.audio.items():
            o = self.acfg.off + slot * AUDIO_SLOT
            out[o:o + AUDIO_SLOT] = data
        return bytes(out)


def pack_stream(packets, segs, acfg):
    """パケットをバンクに詰める。segs（音声）があれば、そのバンクを読んでいる間に鳴るセグメントも同じバンクに入れる
    → (Bank のリスト, 各パケットの (何番目のバンク, 位置))"""
    limit = acfg.off if segs else DATA_SIZE
    banks, pos = [], []
    i, n = 0, len(packets)
    while i < n:
        bank = Bank(limit, acfg)
        start = i
        while i < n:
            body = packets[i][0]
            if len(bank.video) + len(body) + 1 > limit:
                break
            if segs:
                j0, j1 = acfg.seg_window(start, i + 1)
                if j1 - j0 + 1 > acfg.nslots:
                    break
            pos.append((len(banks), len(bank.video)))
            term = TERM_NORMAL | (TERM_FRAME_END if packets[i][1] else 0)
            if i == n - 1:
                term = TERM_END | (TERM_FRAME_END if packets[i][1] else 0)
            bank.video += body + bytes([term])
            i += 1
        assert i > start, "1 パケットがバンクに入りません"
        if i < n:
            bank.video[-1] |= 0x01           # 最後のパケットの終端を「次のバンクへ」に
        if segs:
            j0, j1 = acfg.seg_window(start, i)
            for j in range(j0, j1 + 1):
                bank.audio[j % acfg.nslots] = segs[min(j, len(segs) - 1)]
        banks.append(bank)
    return banks, pos


def choose_slots(enc, rate):
    """バンクの数がいちばん少なくなる音声スロットの数を選ぶ"""
    if not enc.get("segs"):
        return AudioCfg(rate, 1)
    best = None
    max_ns = (DATA_SIZE - 0x4000) // AUDIO_SLOT      # DMC が読めるのは $C000 から（$C000〜音声の置き場の終わり）
    for ns in range(6, min(40, max_ns) + 1):
        acfg = AudioCfg(rate, ns)
        nb = len(pack_stream(enc["packets"], enc["segs"], acfg)[0])
        if best is None or nb < best[0]:
            best = (nb, acfg)
    return best[1]


def font_bytes():
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    import gen_font
    data = b"".join(bytes(gen_font.tile(gen_font.G[ch])) for ch in gen_font.ORDER)
    return data + bytes(16)                     # 41 枚目 = 空白


def build_rom(streams, code):
    """streams: 番号 → encode() の結果 + "segs"（音声）+ "acfg" + "rate"。入っていない番号は無し"""
    assert len(code) == CODE_SIZE, f"player.bin は {CODE_SIZE} バイトのはず（{len(code)}）"
    cat = Bank()
    cat.video = bytearray(SNAP_TABLE)
    cat.video[0:4] = b"NV2X"
    font = font_bytes()
    cat.video[FONT_AT:FONT_AT + len(font)] = font
    banks = [cat]
    layout = {}
    for sid in range(NSTREAMS):
        enc = streams.get(sid)
        if enc is None:
            continue
        segs, acfg = enc.get("segs"), enc["acfg"]
        start = len(banks)
        sbanks, pos = pack_stream(enc["packets"], segs, acfg)
        banks += sbanks
        print(f"  [{sid}] {len(sbanks)} バンク（ここまで {len(banks)}）")
        if len(banks) > 256:
            sys.exit(f"ROM に入りません（{len(banks)} バンク以上。上限 256）。"
                     "titles.json の kbps（映像の量）や audio_rate を下げてください")
        if len(enc["snaps"]) > 255:
            sys.exit("スナップショットが多すぎます（255 まで）。snap_seconds を大きくしてください")
        table = 0x8000 + len(cat.video)
        e = CATALOG_TITLES + sid * ENTRY_SIZE
        ent = bytearray(ENTRY_SIZE)
        ent[0:6] = bytes([1, start, len(enc["snaps"]), table & 0xFF, table >> 8, 1 if segs else 0])
        ent[6] = 0 if enc["mode"] == "mono" else 1
        ent[7] = acfg.nslots
        ent[8] = (acfg.addr - 0xC000) // 64 if segs else 0
        ent[9] = (acfg.addr >> 8) + 1
        ent[10] = 0x40 | acfg.rate
        ent[11:14] = acfg.seg2.to_bytes(3, "little")
        ent[14:17] = acfg.chunk2.to_bytes(3, "little")
        ent[17:33] = enc["palette0"]
        cat.video[e:e + ENTRY_SIZE] = ent
        for p, done, data in enc["snaps"]:
            if len(banks[-1].video) + len(data) > banks[-1].limit:
                banks.append(Bank())
            sb, sa = len(banks) - 1, 0x8000 + len(banks[-1].video)
            banks[-1].video += data
            pb, po = pos[p]
            pa = 0x8000 + po
            acc, slot = acfg.clock_at(p)
            cat.video += bytes([sb, sa & 0xFF, sa >> 8, start + pb, pa & 0xFF, pa >> 8, done & 0xFF, done >> 8,
                                acc & 0xFF, (acc >> 8) & 0xFF, acc >> 16, slot, 0, 0, 0, 0])
        if len(cat.video) > DATA_SIZE:
            sys.exit("目録がバンク 0 に入りきりません")
        layout[sid] = (start, len(sbanks))
    if len(banks) > 256:
        sys.exit(f"ROM が大きすぎます（{len(banks)} バンク。BNROM は 256 バンク = 8MB まで）。"
                 "titles.json の kbps（映像の量）や audio_rate を下げてください")
    prg = bytearray()
    for b in banks:
        prg += b.image() + code
    units = len(prg) // 16384
    hdr = bytes([0x4E, 0x45, 0x53, 0x1A, units & 0xFF, 0,
                 0x21,                 # mapper 34 下位 4bit / 垂直ミラー
                 0x28,                 # mapper 34 上位 4bit / NES 2.0
                 0x20,                 # submapper 2（BNROM）
                 (units >> 8) & 0x0F,  # PRG サイズ上位
                 0x00,                 # PRG-RAM なし
                 0x07,                 # CHR-RAM 8KB
                 0, 0, 0, 0])
    return hdr + bytes(prg), len(banks), layout


# ---------------------------------------------------------------- ROM を読み直して確かめる

class Decoded:
    """ROM を読み直した結果（パケットごと）"""
    def __init__(self):
        self.nt, self.p0, self.p1, self.pal, self.ends, self.banks = [], [], [], [], [], []


def walk_stream(rom, start_bank, pal0, keep=True):
    """ROM のバイト列だけを見てプレイヤーと同じ処理をする"""
    prg = rom[16:]
    nt = np.zeros(1024, dtype=np.int32)
    p0 = np.zeros(256, dtype=np.uint64)
    p1 = np.zeros(256, dtype=np.uint64)
    pal = bytes(pal0)
    bank, ptr = start_bank, 0
    out = Decoded()
    while True:
        base = bank * 0x8000
        out.banks.append(bank)
        k = prg[base + ptr]
        two = k & 0x80
        k &= 0x7F
        ptr += 1
        for _ in range(k):
            slot = prg[base + ptr]
            p0[slot] = int.from_bytes(prg[base + ptr + 1: base + ptr + 9], "big")
            if two:
                p1[slot] = int.from_bytes(prg[base + ptr + 9: base + ptr + 17], "big")
                ptr += 17
            else:
                ptr += 9
        cur = 0
        while True:
            b = prg[base + ptr]
            if b >= 0xF0:
                break
            if b < 0x80:
                cur += b
                nt[cur] = prg[base + ptr + 1]
                cur += 1
                ptr += 2
            elif b < 0xC0:
                n = (b & 0x3F) + 2
                cur += prg[base + ptr + 1]
                nt[cur:cur + n] = list(prg[base + ptr + 2: base + ptr + 2 + n])
                cur += n
                ptr += 2 + n
            elif b == OP_SKIP:
                cur += prg[base + ptr + 1]
                ptr += 2
            elif b in (OP_PAL2, OP_PAL16):
                n = 2 if b == OP_PAL2 else 16
                d = bytes(prg[base + ptr + 1: base + ptr + 1 + n])
                pal = d if n == 16 else d + pal[2:]
                ptr += 1 + n
            else:
                raise AssertionError(f"知らない命令 {b:02X}")
            assert cur <= 1024
        ptr += 1
        if keep:
            out.nt.append(nt.copy())
            out.p0.append(p0.copy())
            out.p1.append(p1.copy())
            out.pal.append(pal)
        out.ends.append(bool(b & TERM_FRAME_END))
        if (b & 0x03) == 1:
            bank, ptr = bank + 1, 0
        elif (b & 0x03) == 2:
            return out


def state_bytes(nt, p0, p1, pal):
    """Mesen の確認用: ネームテーブル + 属性 1024 / CHR $0000-$0FFF / パレット 16"""
    chr_ = b"".join(int(a).to_bytes(8, "big") + int(b).to_bytes(8, "big") for a, b in zip(p0, p1))
    return bytes(nt.astype(np.uint8)) + chr_ + bytes(pal)


def state_hash(data):
    h = 0
    for b in data:
        h = (h * 31 + b) & 0xFFFFFFFF
    return h


def render_mono(nt, pt, inv=0):
    tiles = pt[nt[:960]] ^ (ALL_ONES if inv else np.uint64(0))
    by = tiles.astype(">u8").view(np.uint8).reshape(30, 32, 8)
    bits = np.unpackbits(by, axis=2)
    return bits.reshape(30, 32, 8, 8).transpose(0, 2, 1, 3).reshape(240, 256)


def index_image(nt, p0, p1):
    def bits(pt):
        by = pt[nt[:960]].astype(">u8").view(np.uint8).reshape(30, 32, 8)
        return np.unpackbits(by, axis=2).reshape(30, 32, 8, 8).transpose(0, 2, 1, 3).reshape(240, 256)
    return bits(p0) | (bits(p1) << 1)


def render_rgb(nt, p0, p1, pal):
    """ファミコンで見える色（プレビュー用）"""
    sys.path.insert(0, os.path.join(ROOT, "tools", "paint"))
    import nes_palette
    rgb = np.array(nes_palette.builtin_rgb(), dtype=np.uint8)
    idx = index_image(nt, p0, p1)
    a = nt[960:].astype(np.uint8).reshape(8, 8)
    g = np.zeros((16, 16), np.uint8)
    g[0::2, 0::2] = a & 3
    g[0::2, 1::2] = (a >> 2) & 3
    g[1::2, 0::2] = (a >> 4) & 3
    g[1::2, 1::2] = a >> 6
    blk = np.repeat(np.repeat(g[:15], 16, 0), 16, 1)
    col = np.frombuffer(bytes(pal), np.uint8).reshape(4, 4)[blk, idx]
    return rgb[col & 0x3F]


def screen_hash(img):
    """Mesen のテスト用 Lua と同じ計算（白い点の x 座標の和を行ごとに混ぜる）"""
    r = img.astype(np.int64) @ np.arange(1, 257, dtype=np.int64)
    h = 0
    for y in range(240):
        h = (h * 1000003 + int(r[y]) + y) & 0xFFFFFFFF
    return h


def read_labels(path):
    out = {}
    with open(path, encoding="utf-8") as fp:
        for line in fp:
            parts = line.split()
            if len(parts) == 3 and parts[0] == "al":
                out[parts[2].lstrip(".")] = int(parts[1], 16)
    return out


# ---------------------------------------------------------------- Mesen 用の確認スクリプト

LUA_TEST = r"""-- 自動生成: make_video_rom.py --mesen-test（Mesen の --testrunner で動かす）
-- メニュー → Origen 再生 → 一時停止・コマ送り・コマ戻し → FPS 表示 → メニュー → 再生モード 3 種
-- → グレー・カラーの動画（PPU の中身がエンコーダの計算と一致するか）を確かめる
local H1 = {%(h1)s}          -- Origen: パケットごとの画面
local F1 = {%(f1)s}          -- Origen: k コマ終わった時点の画面（F1[k+1]）
local S2 = {%(s2)s}          -- グレー: パケットごとの PPU の中身
local S3 = {%(s3)s}          -- カラー: パケットごとの PPU の中身
local OPENING_PACKETS = %(n0)d
local A = {frames=%(frames)d, state=%(state)d, stream=%(stream)d, paused=%(paused)d, at_end=%(at_end)d,
           mode=%(mode)d, ppu_busy=%(ppu_busy)d, sel=%(sel)d}

local function rd(a) return emu.read(a, emu.memType.nesMemory) end
local function rd16(a) return rd(a) + rd(a + 1) * 256 end
local function hash()
  local buf = emu.getScreenBuffer()
  local h, i = 0, 1
  for y = 0, 239 do
    local r = 0
    for x = 1, 256 do
      if ((buf[i] >> 16) & 0xFF) > 128 then r = r + x end
      i = i + 1
    end
    h = (h * 1000003 + r + y) & 0xFFFFFFFF
  end
  return h
end
local function ppu_hash()
  local h = 0
  for i = 0, 1023 do h = (h * 31 + emu.read(i, emu.memType.nesNametableRam)) & 0xFFFFFFFF end
  for i = 0, 4095 do h = (h * 31 + emu.read(i, emu.memType.nesChrRam)) & 0xFFFFFFFF end
  for i = 0, 15 do h = (h * 31 + emu.read(i, emu.memType.nesPaletteRam)) & 0xFFFFFFFF end
  return h
end

local fails = 0
local function check(name, ok, extra)
  print(string.format("%%-44s %%s %%s", name, ok and "OK" or "NG", extra or ""))
  if not ok then fails = fails + 1 end
end

local pending = nil
emu.addEventCallback(function()
  if pending then
    local t = {}
    t[pending] = true
    emu.setInput(t, 0)
  end
end, emu.eventType.inputPolled)

local function wait(n) for i = 1, n do coroutine.yield() end end
local function press(b) pending = b; wait(1); pending = nil; wait(2) end
local function settle()
  wait(1)
  local guard = 0
  while rd(A.ppu_busy) ~= 0 and guard < 900 do wait(1); guard = guard + 1 end
  wait(3)
end
local function match(got, exp, maxoff)
  local best, bestoff = -1, 0
  for off = 0, maxoff do
    local m = 0
    for k = 1, #got do if exp[k - off] == got[k] then m = m + 1 end end
    if m > best then best, bestoff = m, off end
  end
  return best, bestoff
end

local script = coroutine.create(function()
  wait(30)
  check("起動するとオープニング", rd(A.state) == 2 and rd(A.stream) == 0)
  wait(OPENING_PACKETS + 10)
  check("オープニングが終わるとメニュー", rd(A.state) == 0 and rd(A.stream) == 4)
  check("メニューでスプライトが出ている", emu.getState()["ppu.mask.spritesEnabled"] == true)
  local m1 = hash(); wait(20)
  check("メニューの背景が動いている", hash() ~= m1)
  check("最初のカーソルは 1 ORIGEN", rd(A.sel) == 1)

  -- Origen を再生
  press("a")
  local got = {}
  for i = 1, 600 do wait(1); got[i] = hash() end
  local best, bestoff = match(got, H1, 90)
  check("Origen の画面がエンコーダの計算と一致", best >= 590, string.format("(%%d/600 offset %%d)", best, bestoff))
  check("再生中はスプライトなし", emu.getState()["ppu.mask.spritesEnabled"] == false)

  -- 一時停止・コマ送り
  press("b"); wait(3)
  local h0 = hash(); wait(30)
  check("一時停止で止まる", hash() == h0)
  local fr0 = rd16(A.frames)
  press("right"); wait(3)
  local fr1 = rd16(A.frames)
  check("→ で 1 コマ進む", fr1 == fr0 + 1 or fr1 == fr0, string.format("(%%d → %%d)", fr0, fr1))
  check("コマ送り後の画面が正しい", hash() == F1[fr1 + 1])
  press("right"); wait(3)
  check("→ をもう一度", rd16(A.frames) == fr1 + 1 and hash() == F1[fr1 + 2])

  -- コマ戻し（スナップショットの境目をまたぐまで）
  local bad, before = 0, rd16(A.frames)
  for i = 1, %(nback)d do
    local f = rd16(A.frames)
    press("left"); settle()
    local g = rd16(A.frames)
    if g ~= f - 1 or hash() ~= F1[g + 1] then bad = bad + 1 end
  end
  check("← %(nback)d 回: 毎回 1 コマ戻って画面も正しい", bad == 0, string.format("(%%d → %%d, ずれ %%d 回)", before, rd16(A.frames), bad))
  local hb = hash(); wait(20)
  check("コマ戻しの後も止まったまま", hash() == hb)
  press("right"); wait(3)
  check("コマ戻しの後に → で進む", hash() == F1[rd16(A.frames) + 1])

  -- 再開して FPS 表示
  press("b"); wait(10)
  press("select"); wait(130)
  check("SELECT で FPS 表示（スプライト）が出る", emu.getState()["ppu.mask.spritesEnabled"] == true)
  local d1 = emu.read(3 * 4 + 1, emu.memType.nesSpriteRam)
  local d2 = emu.read(4 * 4 + 1, emu.memType.nesSpriteRam)
  check("FPS の数字が 29 か 30", (d1 == 3 and d2 == 0) or (d1 == 2 and d2 == 9), string.format("(%%d %%d)", d1, d2))
  press("select"); wait(3)
  check("もう一度 SELECT で消える", emu.getState()["ppu.mask.spritesEnabled"] == false)

  -- メニューへ
  press("start"); wait(10)
  check("START でメニューに戻る", rd(A.state) == 0 and rd(A.stream) == 4)

  -- STOP: Opening を最後まで見ると止まる
  press("up"); press("select")
  check("SELECT で再生モードが STOP に", rd(A.mode) == 1)
  press("a")
  wait(OPENING_PACKETS + 40)
  local hs = hash(); wait(30)
  check("STOP: 最後で止まる", rd(A.paused) == 1 and rd(A.at_end) == 1 and hash() == hs)
  press("b"); wait(10)
  check("STOP: 止まった後に B で最初から", rd(A.at_end) == 0 and rd16(A.frames) < 20)

  -- SEQ: Opening の次は Origen
  press("start"); wait(5)
  press("select")
  check("再生モードが SEQ に", rd(A.mode) == 2)
  press("a")
  wait(OPENING_PACKETS + 40)
  check("SEQ: Opening の次に Origen が始まる", rd(A.state) == 1 and rd(A.stream) == 1)

  -- REPEAT: Opening を繰り返す
  press("start"); wait(5)
  press("select")
  check("再生モードが REPEAT に", rd(A.mode) == 0)
  press("a")
  wait(OPENING_PACKETS + 40)
  check("REPEAT: Opening をもう一度最初から", rd(A.state) == 1 and rd(A.stream) == 0 and rd16(A.frames) < 60)

  -- グレーとカラー: PPU の中身をパケットごとにエンコーダの計算と比べる
  for _, t in ipairs({%(extra)s}) do
    press("start"); wait(5)
    while rd(A.sel) ~= t[1] do press("down") end
    press("a")
    local g = {}
    for i = 1, 400 do wait(1); g[i] = ppu_hash() end
    local b2, o2 = match(g, t[2], 60)
    check(t[3] .. " の PPU の中身が一致", b2 >= 390, string.format("(%%d/400 offset %%d)", b2, o2))
    -- コマ戻し 3 回（一時停止 → → で 1 コマの終わりに合わせてから）
    press("b"); wait(3)
    press("right"); wait(10)
    local okb, trace = true, tostring(rd16(A.frames))
    for i = 1, 3 do
      local f = rd16(A.frames)
      press("left"); settle()
      trace = trace .. "→" .. rd16(A.frames)
      if rd16(A.frames) ~= f - 1 then okb = false end
    end
    check(t[3] .. " のコマ戻し", okb, "(" .. trace .. ")")
    press("b"); wait(30)
  end
  emu.stop(fails)
end)

emu.addEventCallback(function()
  if coroutine.status(script) ~= "dead" then
    local ok, err = coroutine.resume(script)
    if not ok then print("Lua エラー: " .. tostring(err)); emu.stop(99) end
  end
end, emu.eventType.endFrame)
"""


def write_mesen_test(path, labels, dec, opening_packets, nback):
    """dec: 番号 → Decoded"""
    o = dec[1]
    h1 = [screen_hash(render_mono(o.nt[k], o.p0[k], 1 if o.pal[k] and o.pal[k][0] == 0x30 else 0))
          for k in range(min(800, len(o.nt)))]
    f1 = [screen_hash(np.zeros((240, 256), np.uint8))]
    for k, e in enumerate(o.ends):
        if e:
            f1.append(screen_hash(render_mono(o.nt[k], o.p0[k], 1 if o.pal[k] and o.pal[k][0] == 0x30 else 0)))
        if len(f1) > 500:
            break

    def states(d):
        return [state_hash(state_bytes(d.nt[k], d.p0[k], d.p1[k], d.pal[k])) for k in range(min(500, len(d.nt)))]
    # グレー・カラーの確認は、入っている動画だけ
    extra = ", ".join(f'{{{sid}, S{sid}, "{label}"}}' for sid, label in ((2, "2 TK REMIX"), (3, "カラー（3 2026AI）"))
                      if sid in dec)
    lua = LUA_TEST % dict(h1=",".join(map(str, h1)), f1=",".join(map(str, f1)), n0=opening_packets, nback=nback,
                          s2=",".join(map(str, states(dec[2]))) if 2 in dec else "",
                          s3=",".join(map(str, states(dec[3]))) if 3 in dec else "", extra=extra,
                          **{k: labels[k] for k in ("frames", "state", "stream", "paused", "at_end",
                                                    "mode", "ppu_busy", "sel")})
    with open(path, "w", encoding="utf-8", newline="\n") as fp:
        fp.write(lua)


# ---------------------------------------------------------------- main

def file_digest(folder):
    h = hashlib.sha1()
    for f in list_frames(folder):
        st = os.stat(f)
        h.update(f"{os.path.basename(f)}:{st.st_size}:{int(st.st_mtime)}".encode())
    return h.hexdigest()[:12]


def title_fps(t, folder):
    fps = t.get("fps")
    if fps is None:
        with open(os.path.normpath(folder) + ".json", encoding="utf-8-sig") as fp:
            fps = float(json.load(fp)["fps"])
    return float(fps)


def encode_title(t, defaults, cache_dir, seconds=None):
    folder = os.path.join(ROOT, t["frames"])
    fps = title_fps(t, folder)
    step = max(1, int(t.get("step", 1)))
    tol = t.get("tol", defaults.get("tol", 0))
    kbps = t.get("kbps", defaults.get("kbps"))
    snap_every = int(round(t.get("snap_seconds", defaults.get("snap_seconds", 2)) * NES_FPS))
    max_frames = int(seconds * fps / step) if seconds else None
    tt = dict(t)
    tt.setdefault("threshold", defaults.get("threshold", 128))
    key = json.dumps([ENCODER_VERSION, file_digest(folder), {k: v for k, v in tt.items() if k not in ("name", "audio", "audio_rate")},
                      fps, tol, kbps, snap_every, max_frames, CYCLE_LIMIT, BUF_LIMIT, MERGE_GAP], sort_keys=True)
    cfile = os.path.join(cache_dir, "enc_" + hashlib.sha1(key.encode()).hexdigest()[:16] + ".pkl")
    if os.path.exists(cfile):
        with open(cfile, "rb") as fp:
            print(f"  {t['name']}: 前回の変換結果を使います")
            return pickle.load(fp)
    print(f"  {t['name']}: 画像を読み込みます（{t['frames']}、{t.get('mode', 'mono')}、{fps / step:g} fps）")
    fr = load_frames(tt, folder, fps, cache_dir, max_frames)
    print(f"  {t['name']}: {len(fr)} コマを変換します（映像の上限 {kbps or '-'} KB/秒）")
    t0 = time.time()
    enc = encode(fr, tol, snap_every, kbps)
    print(f"  {t['name']}: {len(enc['packets'])} パケット（{time.time() - t0:.0f} 秒）")
    with open(cfile, "wb") as fp:
        pickle.dump(enc, fp)
    return enc


def encode_title_audio(t, enc, cache_dir, rate, amode=("greedy", 56)):
    """titles.json の "audio"（省略時は 連番フォルダ名.mp4）→ DPCM のセグメント。"audio": false なら無音"""
    src = t.get("audio", os.path.normpath(t["frames"]) + ".mp4")
    if src is False:
        return None
    path = os.path.join(ROOT, src)
    if not os.path.exists(path):
        print(f"  {t['name']}: 音声ファイルが無いので音なし（{src}）")
        return None
    st = os.stat(path)
    key = json.dumps([ENCODER_VERSION, src, st.st_size, int(st.st_mtime), len(enc["packets"]), rate,
                      SEG_BYTES, amode[1]] + ([] if amode[0] == "greedy" else [amode[0]]))
    cfile = os.path.join(cache_dir, "dmc_" + hashlib.sha1(key.encode()).hexdigest()[:16] + ".pkl")
    if os.path.exists(cfile):
        with open(cfile, "rb") as fp:
            return pickle.load(fp)
    print(f"  {t['name']}: 音声を DPCM（{CPU_HZ / DMC_PERIODS[rate] / 1000:.1f}kHz）に変換します（{src}）")
    segs = encode_audio(path, len(enc["packets"]), rate, *amode)
    with open(cfile, "wb") as fp:
        pickle.dump(segs, fp)
    return segs


def main():
    ap = argparse.ArgumentParser(description="連番 BMP → ファミコン動画 ROM（メニュー付き）")
    ap.add_argument("--config", default=os.path.join(ROOT, "tools", "video", "titles.json"))
    ap.add_argument("--code", required=True, help="プレイヤーのコード（ld65 の出力 4KB）")
    ap.add_argument("--labels", help="プレイヤーのラベル（ld65 -Ln の出力。--mesen-test で使う）")
    ap.add_argument("-o", "--out", default=os.path.join(ROOT, "build", "video", "movies.nes"))
    ap.add_argument("--seconds", type=float, help="各動画の先頭から何秒だけ使うか（試し用）")
    ap.add_argument("--mesen-test", action="store_true", help="Mesen で動かす確認用 Lua を書き出す")
    ap.add_argument("--preview", action="store_true", help="ROM の中身から再現した動画を mp4 で書き出す（ffmpeg）")
    ap.add_argument("--cache-dir", help="変換結果の置き場所（省略時は ROM と同じフォルダの cache）")
    a = ap.parse_args()

    with open(a.config, encoding="utf-8-sig") as fp:
        cfg = json.load(fp)
    out_dir = os.path.dirname(os.path.abspath(a.out))
    cache_dir = a.cache_dir or os.path.join(out_dir, "cache")
    os.makedirs(cache_dir, exist_ok=True)

    entries = [(i, t) for i, t in enumerate(cfg["titles"])] + [(MENU_ID, cfg["menu"])]
    streams = {}
    print("動画を変換します（初回は 1 本あたり数分〜数十分。2 回目からは前回の結果を使います）")
    for sid, t in entries:
        if not t.get("enabled", True):
            print(f"  {t['name']}: 入れない（titles.json で enabled: false）")
            continue
        enc = dict(encode_title(t, cfg, cache_dir, a.seconds))
        rate = int(t.get("audio_rate", cfg.get("audio_rate", 9)))
        amode = (t.get("audio_enc", cfg.get("audio_enc", "greedy")),
                 int(t.get("audio_swing", cfg.get("audio_swing", 56))))
        enc["segs"] = encode_title_audio(t, enc, cache_dir, rate, amode)
        enc["acfg"] = choose_slots(enc, rate)
        streams[sid] = enc

    with open(a.code, "rb") as fp:
        code = fp.read()
    rom, nbanks, layout = build_rom(streams, code)
    with open(a.out, "wb") as fp:
        fp.write(rom)

    report = [f"ROM: {a.out}",
              f"  {len(rom):,} バイト（BNROM {nbanks} バンク × 32KB。上限は 256 バンク = 8MB）", ""]
    decoded, info = {}, {}
    for sid, t in entries:
        enc = streams.get(sid)
        if enc is None:
            report.append(f"[{sid}] {t['name']}: 入っていない")
            continue
        acfg, segs = enc["acfg"], enc.get("segs")
        d = walk_stream(rom, layout[sid][0], enc["palette0"])
        assert len(d.nt) == len(enc["packets"]), "ROM を読み直した結果がエンコーダと合いません"
        decoded[sid] = d
        # スナップショットがその時点の中身と一致するか
        prg = rom[16:]
        e = CATALOG_TITLES + sid * ENTRY_SIZE
        cnt, table = prg[e + 2], prg[e + 3] | (prg[e + 4] << 8)
        assert cnt == len(enc["snaps"])
        st = PPUState(enc["palette0"])
        for i, (p, done, data) in enumerate(enc["snaps"]):
            tt = table - 0x8000 + i * 16
            off = prg[tt] * 0x8000 + (prg[tt + 1] | (prg[tt + 2] << 8)) - 0x8000
            raw = prg[off: off + len(data)]
            if p:
                st.nt, st.pt0, st.pt1, st.pal = d.nt[p - 1], d.p0[p - 1], d.p1[p - 1], d.pal[p - 1]
            else:
                st = PPUState(enc["palette0"])
            assert raw == data == snap_bytes(st, enc["mode"]), f"スナップショット {i} が合いません"
            assert done == sum(d.ends[:p])
            assert (prg[tt + 8] | (prg[tt + 9] << 8) | (prg[tt + 10] << 16), prg[tt + 11]) == acfg.clock_at(p)
        audio_line = "  音声なし"
        if segs:
            # どのパケットを読んでいる間も、そのとき鳴りうる音声が同じバンクの決まった場所にあるか
            for k in range(len(enc["packets"])):
                j0, j1 = acfg.seg_window(k, k + 1)
                base = 16 + d.banks[k] * 0x8000 + acfg.off
                for j in range(j0, j1 + 1):
                    sl = j % acfg.nslots
                    assert rom[base + sl * AUDIO_SLOT: base + (sl + 1) * AUDIO_SLOT] == segs[j], \
                        f"パケット {k} のバンクに音声セグメント {j} がありません"
            with open(os.path.join(out_dir, f"audio_{sid}.bin"), "wb") as fp:
                fp.write(b"".join(segs))
            audio_line = (f"  音声 DPCM {CPU_HZ / acfg.period / 1000:.1f}kHz: {len(segs)} セグメント "
                          f"{len(segs) * SEG_BYTES:,} バイト、1 バンクに {acfg.nslots} スロット（どのバンクからも鳴る位置にあることを確認）")
        info[sid] = dict(fps=enc["fps"], period=acfg.period, nslots=acfg.nslots, addr=acfg.addr,
                         audio=bool(segs))
        fe = enc["frame_err"]
        vsize = sum(len(b) + 1 for b, _ in enc["packets"])
        ssize = sum(len(s[2]) for s in enc["snaps"])
        sec = enc["nframes"] / enc["fps"]
        report += [
            f"[{sid}] {t['name']}: {t['frames']}（{enc['mode']}）",
            f"  {enc['nframes']} コマ / {enc['fps']:g} fps / {sec:.1f} 秒 / {len(enc['packets'])} パケット / {layout[sid][1]} バンク",
            f"  映像 {vsize:,} バイト（1 秒あたり {vsize / sec / 1024:.1f} KB）+ コマ戻し用 {len(enc['snaps'])} 個 {ssize:,} バイト",
            f"  VBlank 中の書き込み 最大 {enc['max_cyc']} サイクル（上限 {CYCLE_LIMIT}）/ バッファ 最大 {enc['max_buf']} バイト",
            f"  元画像（減色後）と違う点 平均 {100 * fe.mean() / 61440:.2f}% / 最大 {100 * fe.max() / 61440:.1f}%",
            audio_line,
        ]
    with open(os.path.join(out_dir, "movies_info.json"), "w", encoding="utf-8") as fp:
        json.dump(info, fp)
    rep_path = os.path.splitext(a.out)[0] + "_report.txt"
    with open(rep_path, "w", encoding="utf-8") as fp:
        fp.write("\n".join(report) + "\n")
    print("\n".join(report))
    print(f"レポート: {rep_path}")

    if a.mesen_test:
        if not all(k in decoded for k in (0, 1)):
            sys.exit("--mesen-test には OPENING と ORIGEN（0 番と 1 番）が必要です")
        labels = read_labels(a.labels)
        lua = os.path.splitext(a.out)[0] + "_mesen_test.lua"
        nback = 40 if a.seconds else 70
        write_mesen_test(lua, labels, decoded, len(decoded[0].nt), nback)
        print(f"Mesen 確認用スクリプト: {lua}")

    if a.preview:
        import subprocess
        import wave
        for sid, d in decoded.items():
            name = cfg["titles"][sid]["name"] if sid < 4 else "menu"
            base = os.path.splitext(a.out)[0] + f"_preview_{sid}_{name.replace(' ', '_')}"
            audio_in = []
            enc = streams[sid]
            if enc.get("segs"):
                lv = dmc_levels(enc["segs"])
                pcm = ((lv.astype(np.int32) - 64) * 400).clip(-32768, 32767).astype("<i2")
                with wave.open(base + ".wav", "wb") as w:
                    w.setnchannels(1)
                    w.setsampwidth(2)
                    w.setframerate(int(round(CPU_HZ / enc["acfg"].period)))
                    w.writeframes(pcm.tobytes())
                audio_in = ["-i", base + ".wav", "-shortest"]
            cmd = ["ffmpeg", "-y", "-loglevel", "error", "-f", "rawvideo", "-pix_fmt", "rgb24",
                   "-s", "256x240", "-r", f"{NES_FPS}", "-i", "-"] + audio_in + [
                   "-vf", "scale=512:480:flags=neighbor", "-pix_fmt", "yuv420p", base + ".mp4"]
            pr = subprocess.Popen(cmd, stdin=subprocess.PIPE)
            for k in range(len(d.nt)):
                pr.stdin.write(render_rgb(d.nt[k], d.p0[k], d.p1[k], d.pal[k]).tobytes())
            pr.stdin.close()
            pr.wait()
            print(f"プレビュー動画: {base}.mp4")


if __name__ == "__main__":
    main()
