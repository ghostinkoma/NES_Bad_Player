"""
nes_convert.py - 任意の画像をファミコンの表示制約に合わせて減色する変換エンジン（第2フェーズ）

ファミコン BG の制約
  ・使える色はシステムパレット 64 色（重複・非推奨を除くと約 52 色）
  ・画面全体で「共通背景色 1 色 + パレット 4 組 × 3 色」
  ・16×16 ドットのブロックごとに、どのパレットを使うかを 1 つだけ選べる

変換の流れ
  1. 画像を 128×120（第1フェーズ ROM のキャンバス）に合わせて縮小・トリミングし、明るさなどを調整
  2. 色の近さは Lab 色空間（人の見た目に近い）で測る
  3. 背景色の候補ごとに、4 組のパレットとブロック割り当てを交互に最適化（k-means 風）
  4. 各ブロックの 4 色（背景色 + パレット 3 色）だけを使ってディザリング
  5. 第1フェーズの Picture 形式にする → そのまま state.bin でエミュレータへ送れる

GUI に依存しないので、batch_convert.py（一括変換）や将来のサーバー側変換でもそのまま使える。
"""

from __future__ import annotations

import sys
from dataclasses import dataclass, replace
from pathlib import Path

import numpy as np
from PIL import Image, ImageEnhance, ImageOps

TOOLS_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(TOOLS_DIR / "paint"))

import nes_palette  # noqa: E402
from paint_model import BLOCKS_X, H, W, Picture  # noqa: E402

FIT_MODES = {"fit": "全体を収める（余白あり）", "fill": "画面を埋める（はみ出しを切る）",
             "stretch": "引き伸ばす"}
DITHER_MODES = {"none": "なし", "ordered": "パターン（Bayer 8×8）", "fs": "誤差拡散（Floyd–Steinberg）"}
RESAMPLE_MODES = {"lanczos": Image.Resampling.LANCZOS, "box": Image.Resampling.BOX,
                  "nearest": Image.Resampling.NEAREST}


@dataclass(frozen=True)
class Params:
    fit: str = "fill"
    resample: str = "lanczos"      # 用意済みのドット絵・ディザ画像は "nearest" にすると崩れない
    brightness: float = 1.0
    contrast: float = 1.0
    saturation: float = 1.0
    gamma: float = 1.0
    sharpness: float = 1.0
    dither: str = "fs"
    dither_strength: float = 0.75  # 0〜1
    metric: str = "lab"            # "lab" / "rgb"
    bg: int | None = None          # 背景色を固定する場合はファミコン色番号。None で自動
    bg_candidates: int = 4         # 自動のとき試す背景色の数（多いほど良くなるが遅い）
    iterations: int = 8
    margin_rgb: tuple[int, int, int] = (0, 0, 0)   # fit の余白色


@dataclass
class Result:
    picture: Picture
    source: Image.Image            # 減色前（128×120 に整えた画像）
    preview: Image.Image           # 減色後（ファミコンで表示される見た目）
    delta_e: float                 # 平均色差（小さいほど元画像に近い）


# =============================================================================
# 色空間
# =============================================================================
_M_RGB2XYZ = np.array([[0.4124564, 0.3575761, 0.1804375],
                       [0.2126729, 0.7151522, 0.0721750],
                       [0.0193339, 0.1191920, 0.9503041]], dtype=np.float64)
_WHITE = np.array([0.95047, 1.0, 1.08883])


def rgb_to_lab(rgb: np.ndarray) -> np.ndarray:
    """sRGB (0〜255, [...,3]) → CIE Lab"""
    c = np.asarray(rgb, dtype=np.float64) / 255.0
    lin = np.where(c <= 0.04045, c / 12.92, ((c + 0.055) / 1.055) ** 2.4)
    xyz = lin @ _M_RGB2XYZ.T / _WHITE
    f = np.where(xyz > 0.008856, np.cbrt(xyz), 7.787 * xyz + 16.0 / 116.0)
    L = 116.0 * f[..., 1] - 16.0
    a = 500.0 * (f[..., 0] - f[..., 1])
    b = 200.0 * (f[..., 1] - f[..., 2])
    return np.stack([L, a, b], axis=-1)


def to_work(rgb: np.ndarray, metric: str) -> np.ndarray:
    return rgb_to_lab(rgb) if metric == "lab" else np.asarray(rgb, dtype=np.float64)


# =============================================================================
# 使える色の一覧
# =============================================================================
def candidate_colors(rgb_table: list[tuple[int, int, int]], fixed_bg: int | None = None) -> list[int]:
    """変換に使うファミコン色番号の一覧（同じ RGB の重複と非推奨色を除く。黒は $0F に統一）"""
    order = [nes_palette.BLACK] + [i for i in range(64) if i != nes_palette.BLACK]
    seen: set[tuple[int, int, int]] = set()
    out = []
    for i in order:
        if i in nes_palette.DISCOURAGED or (i & 0x0F) in (0x0E, 0x0F) and i != nes_palette.BLACK:
            continue
        rgb = tuple(rgb_table[i])
        if rgb in seen:
            continue
        seen.add(rgb)
        out.append(i)
    if fixed_bg is not None and fixed_bg not in out:
        out.append(fixed_bg)
    return out


# =============================================================================
# 1. 画像の下ごしらえ
# =============================================================================
def prepare_image(img: Image.Image, p: Params) -> Image.Image:
    img = ImageOps.exif_transpose(img)
    if img.mode in ("RGBA", "LA", "P"):
        img = img.convert("RGBA")
        base = Image.new("RGBA", img.size, p.margin_rgb + (255,))
        img = Image.alpha_composite(base, img)
    img = img.convert("RGB")
    rs = RESAMPLE_MODES.get(p.resample, Image.Resampling.LANCZOS)

    if p.fit == "stretch":
        img = img.resize((W, H), rs)
    elif p.fit == "fill":
        img = ImageOps.fit(img, (W, H), method=rs)
    else:
        small = ImageOps.contain(img, (W, H), method=rs)
        canvas = Image.new("RGB", (W, H), p.margin_rgb)
        canvas.paste(small, ((W - small.width) // 2, (H - small.height) // 2))
        img = canvas

    if p.brightness != 1.0:
        img = ImageEnhance.Brightness(img).enhance(p.brightness)
    if p.contrast != 1.0:
        img = ImageEnhance.Contrast(img).enhance(p.contrast)
    if p.saturation != 1.0:
        img = ImageEnhance.Color(img).enhance(p.saturation)
    if p.sharpness != 1.0:
        img = ImageEnhance.Sharpness(img).enhance(p.sharpness)
    if p.gamma != 1.0:
        g = 1.0 / p.gamma
        img = img.point([min(255, round(255 * (i / 255) ** g)) for i in range(256)] * 3)
    return img


# =============================================================================
# 2〜3. パレットとブロック割り当ての最適化
# =============================================================================
def _block_index() -> np.ndarray:
    ys, xs = np.mgrid[0:H, 0:W]
    return ((ys // 16) * BLOCKS_X + (xs // 16)).ravel()


BLOCK_IDX = _block_index()
N_BLOCKS = int(BLOCK_IDX.max()) + 1


def _greedy_palette(Dm: np.ndarray, dbg_m: np.ndarray, bg: int, refine: int = 2) -> list[int]:
    """割り当てられた画素（距離行列 Dm）に対して誤差が最小になる 3 色を選ぶ"""
    K = Dm.shape[1]
    chosen: list[int] = []
    cur = dbg_m.copy()
    for _ in range(3):
        cost = np.minimum(cur[:, None], Dm).sum(axis=0)
        cost[chosen + [bg]] = np.inf
        c = int(cost.argmin())
        chosen.append(c)
        cur = np.minimum(cur, Dm[:, c])
    # 1 色ずつ入れ替えて改善できるか試す
    for _ in range(refine):
        for s in range(3):
            others = [c for i, c in enumerate(chosen) if i != s]
            cur = np.minimum(dbg_m, Dm[:, others].min(axis=1))
            cost = np.minimum(cur[:, None], Dm).sum(axis=0)
            cost[others + [bg]] = np.inf
            chosen[s] = int(cost.argmin())
    assert len(set(chosen)) == 3 and bg not in chosen and K > 4
    return chosen


def _initial_assign(X: np.ndarray) -> np.ndarray:
    """ブロックの平均色を 4 グループに分ける（最遠点で初期化した k-means）"""
    cnt = np.bincount(BLOCK_IDX, minlength=N_BLOCKS).astype(np.float64)
    means = np.stack([np.bincount(BLOCK_IDX, weights=X[:, k], minlength=N_BLOCKS) / cnt
                      for k in range(3)], axis=1)
    centers = [means[np.argmax(((means - means.mean(0)) ** 2).sum(1))]]
    while len(centers) < 4:
        d = np.min([((means - c) ** 2).sum(1) for c in centers], axis=0)
        centers.append(means[int(d.argmax())])
    centers = np.array(centers)
    assign = np.zeros(N_BLOCKS, dtype=np.int64)
    for _ in range(10):
        assign = ((means[:, None, :] - centers[None]) ** 2).sum(2).argmin(1)
        for k in range(4):
            if np.any(assign == k):
                centers[k] = means[assign == k].mean(0)
    return assign


def _solve(D: np.ndarray, X: np.ndarray, bg: int, iterations: int):
    """背景色 bg を固定して、4 パレットとブロック割り当てを交互に最適化する"""
    dbg = D[:, bg]
    assign = _initial_assign(X)
    pals: list[list[int]] = [[] for _ in range(4)]
    fallback = None
    for _ in range(max(1, iterations)):
        pix_assign = assign[BLOCK_IDX]
        for p in range(4):
            m = pix_assign == p
            if not m.any():
                if fallback is None:   # 使われていないパレット → 画面全体向けの 3 色
                    fallback = _greedy_palette(D, dbg, bg, refine=0)
                pals[p] = fallback[:]
                continue
            pals[p] = _greedy_palette(D[m], dbg[m], bg)
        E = np.stack([np.bincount(BLOCK_IDX, weights=np.minimum(dbg, D[:, pals[p]].min(1)),
                                  minlength=N_BLOCKS) for p in range(4)])
        new_assign = E.argmin(0)
        if np.array_equal(new_assign, assign):
            break
        assign = new_assign
    total = float(E[assign, np.arange(N_BLOCKS)].sum())
    return pals, assign, total


# =============================================================================
# 4. ディザリング
# =============================================================================
_BAYER8 = np.array([
    [0, 32, 8, 40, 2, 34, 10, 42], [48, 16, 56, 24, 50, 18, 58, 26],
    [12, 44, 4, 36, 14, 46, 6, 38], [60, 28, 52, 20, 62, 30, 54, 22],
    [3, 35, 11, 43, 1, 33, 9, 41], [51, 19, 59, 27, 49, 17, 57, 25],
    [15, 47, 7, 39, 13, 45, 5, 37], [63, 31, 55, 23, 61, 29, 53, 21]], dtype=np.float64)
_BAYER8 = (_BAYER8 + 0.5) / 64.0 - 0.5         # -0.5〜+0.5


def _slot_colors(cands_work: np.ndarray, bg: int, pals: list[list[int]], assign: np.ndarray):
    """各画素が使える 4 色（背景色 + そのブロックのパレット 3 色）の作業空間座標 [N,4,3]"""
    table = np.array([[bg] + p for p in pals])            # [4 パレット, 4 スロット]
    slots = table[assign[BLOCK_IDX]]                       # [N, 4] 候補番号
    return cands_work[slots]


def _quantize(src_rgb: np.ndarray, cands_rgb: np.ndarray, bg: int, pals, assign, p: Params) -> np.ndarray:
    metric = p.metric
    cw = to_work(cands_rgb, metric)
    S = _slot_colors(cw, bg, pals, assign)                 # [N,4,3]
    strength = max(0.0, min(1.0, p.dither_strength))

    if p.dither == "ordered" and strength > 0:
        th = np.tile(_BAYER8, (H // 8 + 1, W // 8 + 1))[:H, :W].ravel()
        rgb = np.clip(src_rgb + (th * 96.0 * strength)[:, None], 0, 255)
        X = to_work(rgb, metric)
        return ((X[:, None, :] - S) ** 2).sum(2).argmin(1)

    X = to_work(src_rgb, metric)
    if p.dither != "fs" or strength <= 0:
        return ((X[:, None, :] - S) ** 2).sum(2).argmin(1)

    # Floyd–Steinberg（左右交互に走査して筋模様を減らす）
    work = X.reshape(H, W, 3).tolist()
    Sl = S.reshape(H, W, 4, 3).tolist()
    out = [0] * (W * H)
    lo, hi = ((0.0, -128.0, -128.0), (100.0, 127.0, 127.0)) if metric == "lab" else ((0.0,) * 3, (255.0,) * 3)
    ecap = 10.0 if metric == "lab" else 28.0
    k7, k3, k5, k1 =(7 / 16 * strength, 3 / 16 * strength, 5 / 16 * strength, 1 / 16 * strength)
    for y in range(H):
        xs = range(W) if y % 2 == 0 else range(W - 1, -1, -1)
        d = 1 if y % 2 == 0 else -1
        row, nrow = work[y], (work[y + 1] if y + 1 < H else None)
        for x in xs:
            v = row[x]
            v0 = min(max(v[0], lo[0]), hi[0])
            v1 = min(max(v[1], lo[1]), hi[1])
            v2 = min(max(v[2], lo[2]), hi[2])
            best, bd = 0, 1e30
            for s, c in enumerate(Sl[y][x]):
                dd = (v0 - c[0]) ** 2 + (v1 - c[1]) ** 2 + (v2 - c[2]) ** 2
                if dd < bd:
                    best, bd = s, dd
            out[y * W + x] = best
            c = Sl[y][x][best]
            # 誤差は上限で切る。ブロックのパレットに無い色の誤差が溜まって
            # 隣のブロックに飛び散る（ゴマ塩状の点になる）のを防ぐ
            e0 = min(max(v0 - c[0], -ecap), ecap)
            e1 = min(max(v1 - c[1], -ecap), ecap)
            e2 = min(max(v2 - c[2], -ecap), ecap)
            nx = x + d
            if 0 <= nx < W:
                t = row[nx]
                t[0] += e0 * k7; t[1] += e1 * k7; t[2] += e2 * k7
            if nrow is not None:
                for xx, k in ((x - d, k3), (x, k5), (x + d, k1)):
                    if 0 <= xx < W:
                        t = nrow[xx]
                        t[0] += e0 * k; t[1] += e1 * k; t[2] += e2 * k
    return np.array(out)


# =============================================================================
# 全体
# =============================================================================
def convert(img: Image.Image, p: Params = Params(),
            rgb_table: list[tuple[int, int, int]] | None = None) -> Result:
    rgb_table = rgb_table or nes_palette.builtin_rgb()
    src = prepare_image(img, p)
    src_rgb = np.asarray(src, dtype=np.float64).reshape(-1, 3)

    cands = candidate_colors(rgb_table, p.bg)
    cands_rgb = np.array([rgb_table[i] for i in cands], dtype=np.float64)
    X = to_work(src_rgb, p.metric)
    C = to_work(cands_rgb, p.metric)
    D = ((X[:, None, :] - C[None, :, :]) ** 2).sum(2)      # [画素, 候補色] 距離²

    if p.bg is not None:
        bg_list = [cands.index(p.bg)]
    else:
        freq = np.bincount(D.argmin(1), minlength=len(cands))
        bg_list = [int(i) for i in np.argsort(-freq)[:max(1, p.bg_candidates)]]

    best = None
    for bg in bg_list:
        pals, assign, total = _solve(D, X, bg, p.iterations)
        if best is None or total < best[3]:
            best = (bg, pals, assign, total)
    bg, pals, assign, _ = best

    # パレット内は暗い順に並べる（お絵描きツールで編集しやすいように）
    L = rgb_to_lab(cands_rgb)[:, 0]
    pals = [sorted(pl, key=lambda c: L[c]) for pl in pals]

    slots = _quantize(src_rgb, cands_rgb, bg, pals, assign, p)

    pic = Picture()
    pic.bg = cands[bg]
    pic.pals = [[cands[c] for c in pl] for pl in pals]
    pic.block_pal = bytearray(int(a) for a in assign)
    pic.pixels = bytearray(int(s) for s in slots)

    preview = render(pic, rgb_table)
    out_lab = rgb_to_lab(np.asarray(preview, dtype=np.float64).reshape(-1, 3))
    delta_e = float(np.sqrt(((rgb_to_lab(src_rgb) - out_lab) ** 2).sum(1)).mean())
    return Result(pic, src, preview, delta_e)


def render(pic: Picture, rgb_table: list[tuple[int, int, int]]) -> Image.Image:
    """Picture をファミコンで表示される見た目の RGB 画像にする"""
    table = np.array([[pic.bg] + p for p in pic.pals])     # [4,4] 色番号
    px = np.frombuffer(bytes(pic.pixels), dtype=np.uint8).astype(np.int64)
    bp = np.frombuffer(bytes(pic.block_pal), dtype=np.uint8).astype(np.int64)
    idx = table[bp[BLOCK_IDX], px]
    rgb = np.array(rgb_table, dtype=np.uint8)[idx & 0x3F]
    return Image.fromarray(rgb.reshape(H, W, 3), "RGB")


def with_params(p: Params, **kw) -> Params:
    return replace(p, **kw)
