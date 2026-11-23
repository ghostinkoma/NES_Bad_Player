"""
make_slides.py - スライドショー ROM に入れる画像をまとめる（第3フェーズ）

入力（ファイル・フォルダを並べた順。フォルダの中は名前順）
    *.npaint.json   変換ツール / お絵描きツールで保存したもの（そのまま使う。おすすめ）
    *.png / *.jpg …  その場で第2フェーズの変換エンジンで変換する（オプションは一括変換と同じ）
    入力を省略すると assets/slides/ フォルダを使う

最後に実機確認用のテストページを付け足す（--no-test で付けない）
    色見本 7 枚（$0D を除くファミコンの色 54 色。各色に 16 進の番号入り）
    テスト模様 1 枚（1 ドットの縞・市松・外枠。にじみや表示位置の確認用）

出力（build/slides/）
    bank0.bin〜bank6.bin   ROM のバンク 0〜6 に入れる画像データ（各 16KB）
    slides_table.inc       スライド表（slideshow.s が .include する）
    preview/NN_名前.png    エミュレータでの見え方（2 倍）。実機の写真と見比べる用
    index.html             一覧
    slides.txt             番号と名前の一覧

使い方（通常は build_slideshow.ps1 から呼ばれる）
    python tools/slideshow/make_slides.py assets\\slides
    python tools/slideshow/make_slides.py a.npaint.json b.png --dither ordered
"""

from __future__ import annotations

import argparse
import html
import sys
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools" / "paint"))
sys.path.insert(0, str(ROOT / "tools" / "converter"))

import nes_palette  # noqa: E402
from paint_model import BLOCKS_X, BLOCKS_Y, H, W, Picture  # noqa: E402

# ---- ROM の構成（nes/src/slideshow.s と一致させる） --------------------------
BANK_SIZE = 0x4000
DATA_BANKS = 7                 # バンク 0〜6。バンク 7 はプログラム
PER_BANK = 3
MAX_SLIDES = DATA_BANKS * PER_BANK   # 21
CHR_BYTES = 240 * 16           # キャンバスのタイル 0〜239
CHR_OFFSET = 0x1000            # 画像 k の CHR はバンク内 +k*$1000
META_OFFSET = 0x3000           # 画像 k の属性+パレットはバンク内 +$3000+k*$80
META_STRIDE = 0x80

IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".bmp", ".gif", ".webp", ".tif", ".tiff"}


@dataclass
class Slide:
    name: str
    picture: Picture


# =============================================================================
# 実機確認用のテストページ
# =============================================================================
# 3x5 の 16 進数字
HEX_FONT = {
    "0": ["111", "101", "101", "101", "111"], "1": ["010", "110", "010", "010", "111"],
    "2": ["111", "001", "111", "100", "111"], "3": ["111", "001", "111", "001", "111"],
    "4": ["101", "101", "111", "001", "001"], "5": ["111", "100", "111", "001", "111"],
    "6": ["111", "100", "111", "101", "111"], "7": ["111", "001", "010", "010", "010"],
    "8": ["111", "101", "111", "101", "111"], "9": ["111", "101", "111", "001", "111"],
    "A": ["010", "101", "111", "101", "101"], "B": ["110", "101", "110", "101", "110"],
    "C": ["011", "100", "100", "100", "011"], "D": ["110", "101", "101", "101", "110"],
    "E": ["111", "100", "111", "100", "111"], "F": ["111", "100", "111", "100", "100"],
}


def draw_text(pic: Picture, x: int, y: int, text: str, slot: int, scale: int = 2) -> None:
    for ch in text:
        for gy, row in enumerate(HEX_FONT[ch]):
            for gx, bit in enumerate(row):
                if bit == "1":
                    for sy in range(scale):
                        for sx in range(scale):
                            px, py = x + gx * scale + sx, y + gy * scale + sy
                            if 0 <= px < W and 0 <= py < H:
                                pic.pixels[py * W + px] = slot
        x += 4 * scale


def chart_colors() -> list[int]:
    """色見本に並べる色: $0D と、黒と同じに見える $xE/$xF/$1D を除いた 54 色"""
    out = []
    for c in range(64):
        lo = c & 0x0F
        if c == 0x0D or c == 0x1D or lo in (0x0E, 0x0F):
            continue
        out.append(c)
    return out


def luminance(rgb: tuple[int, int, int]) -> float:
    r, g, b = (v / 255 for v in rgb)
    return 0.299 * r + 0.587 * g + 0.114 * b


def palette_chart_pages(rgb_table) -> list[Slide]:
    """1 ページに 8 色。背景は黒 $0F、各パレットは [白 $30, 色A, 色B]。
    4 つの帯（16x16 ブロック 2,2,2,1.5 段 = 32,32,32,24 ドット）に左右 2 色ずつ並べ、
    色の番号を明るい色には黒、暗い色には白で書く"""
    colors = chart_colors()
    pages = []
    bands = [(0, 32), (32, 32), (64, 32), (96, 24)]
    for page_no in range(0, len(colors), 8):
        chunk = colors[page_no:page_no + 8]
        pic = Picture()
        pic.clear()
        pic.bg = 0x0F
        pic.pals = [[0x30, 0x30, 0x30] for _ in range(4)]
        for band, (y0, hgt) in enumerate(bands):
            pair = chunk[band * 2:band * 2 + 2]
            if not pair:
                continue
            pic.pals[band] = [0x30] + pair + [pair[-1]] * (2 - len(pair))
            for by in range(y0 // 16, (y0 + hgt + 15) // 16):
                for bx in range(BLOCKS_X):
                    if by < BLOCKS_Y:
                        pic.block_pal[by * BLOCKS_X + bx] = band
            for i, c in enumerate(pair):
                x0 = 1 + i * 64
                slot = 2 + i
                for y in range(y0 + 1, y0 + hgt - 1):
                    for x in range(x0, x0 + 62):
                        pic.pixels[y * W + x] = slot
                text_slot = 0 if luminance(rgb_table[c]) > 0.5 else 1
                draw_text(pic, x0 + 3, y0 + 4, f"{c:02X}", text_slot)
        first, last = chunk[0], chunk[-1]
        pages.append(Slide(f"色見本 ${first:02X}-${last:02X}", pic))
    return pages


def test_pattern_page() -> Slide:
    """1 ドットの縞・市松・外枠・色の段階。実機のにじみ・表示位置・端の欠けを見る"""
    pic = Picture()
    pic.clear()
    pic.bg = 0x0F
    pic.pals = [[0x30, 0x16, 0x2A],   # 白 赤 緑
                [0x30, 0x12, 0x28],   # 白 青 黄
                [0x00, 0x10, 0x30],   # 灰の 3 段階
                [0x30, 0x24, 0x1C]]
    px = pic.pixels

    def fill(x0, y0, x1, y1, f):
        for y in range(y0, y1):
            for x in range(x0, x1):
                px[y * W + x] = f(x, y)

    # 上段（ブロック行 0〜1, パレット0）: 縦縞 1 ドット / 2 ドット / 横縞 / 市松
    fill(0, 0, 32, 32, lambda x, y: 1 if x % 2 == 0 else 0)
    fill(32, 0, 64, 32, lambda x, y: 1 if (x // 2) % 2 == 0 else 0)
    fill(64, 0, 96, 32, lambda x, y: 1 if y % 2 == 0 else 0)
    fill(96, 0, 128, 32, lambda x, y: 1 if (x + y) % 2 == 0 else 0)
    # 2 段目（ブロック行 2〜3, パレット1）: 色の縦縞（赤青の色にじみ）
    for b in range(16):
        pic.block_pal[2 * 8 + b % 8 + (b // 8) * 8] = 1
    fill(0, 32, 64, 64, lambda x, y: 2 if x % 2 == 0 else 3)
    fill(64, 32, 128, 64, lambda x, y: 2 if (x // 4) % 2 == 0 else 3)
    # 3 段目（ブロック行 4〜5, パレット2）: 灰の 4 段階
    for b in range(16):
        pic.block_pal[4 * 8 + b] = 2
    fill(0, 64, 128, 96, lambda x, y: x // 32)
    # 4 段目（ブロック行 6〜7, パレット3）
    for b in range(16):
        pic.block_pal[6 * 8 + b] = 3
    fill(0, 96, 128, H, lambda x, y: (x // 16) % 4)
    # 外枠（白, 1 ドット）: キャンバスの端が欠けずに見えるか
    for x in range(W):
        for y in (0, H - 1):
            px[y * W + x] = 1 if pic.block_pal[(y // 16) * 8 + x // 16] != 2 else 3
    for y in range(H):
        for x in (0, W - 1):
            px[y * W + x] = 1 if pic.block_pal[(y // 16) * 8 + x // 16] != 2 else 3
    return Slide("テスト模様（縞・市松・外枠）", pic)


# =============================================================================
# 入力
# =============================================================================
def collect(inputs: list[str]) -> list[Path]:
    files: list[Path] = []
    for s in inputs:
        p = Path(s)
        if p.is_dir():
            files += sorted(f for f in p.iterdir()
                            if f.name.endswith(".npaint.json") or f.suffix.lower() in IMAGE_EXTS)
        elif p.is_file():
            files.append(p)
        else:
            print(f"[skip] 見つかりません: {p}", file=sys.stderr)
    return files


def load_slides(files: list[Path], args, rgb) -> list[Slide]:
    slides = []
    params = None
    for f in files:
        if f.name.endswith(".npaint.json"):
            slides.append(Slide(f.name[:-len(".npaint.json")], Picture.load(f)))
            print(f"  読み込み  {f.name}")
            continue
        if params is None:      # 画像があるときだけ変換エンジン（Pillow / numpy）を読み込む
            import nes_convert as nc
            params = nc.Params(fit=args.fit, resample=args.resample, dither=args.dither,
                               dither_strength=args.strength,
                               bg=int(args.bg, 16) & 0x3F if args.bg else None,
                               bg_candidates=max(1, min(8, args.quality)))
        from PIL import Image
        try:
            img = Image.open(f)
            img.load()
        except OSError as ex:
            print(f"[skip] {f}: {ex}", file=sys.stderr)
            continue
        r = nc.convert(img, params, rgb)
        slides.append(Slide(f.stem, r.picture))
        print(f"  変換      {f.name}  ΔE={r.delta_e:.1f}")
    return slides


# =============================================================================
# 出力
# =============================================================================
def pack(slides: list[Slide]) -> tuple[list[bytearray], list[tuple[int, int, int]]]:
    banks = [bytearray(b"\xFF" * BANK_SIZE) for _ in range(DATA_BANKS)]
    table = []
    for i, s in enumerate(slides):
        bank, k = divmod(i, PER_BANK)
        chr_ = s.picture.to_chr()[:CHR_BYTES]
        meta = s.picture.to_attr() + s.picture.to_ppu_palette()[:16]
        assert len(meta) == 80
        co = k * CHR_OFFSET
        mo = META_OFFSET + k * META_STRIDE
        banks[bank][co:co + CHR_BYTES] = chr_
        banks[bank][mo:mo + len(meta)] = meta
        table.append((bank, 0x80 + (co >> 8), 0x8000 + mo))
    return banks, table


def write_table(path: Path, slides: list[Slide], table) -> None:
    def row(label, vals):
        return f"{label}:\n    .byte " + ", ".join(vals)

    lines = [
        "; slides_table.inc - tools/slideshow/make_slides.py が生成（手で書き換えないこと）",
        ".segment \"RODATA\"",
        f"SLIDE_COUNT = {len(slides)}",
    ]
    for i, s in enumerate(slides):
        lines.append(f";   {i + 1:2d}: {s.name}")
    lines += [
        row("slide_bank", [str(b) for b, _, _ in table]),
        row("slide_chr_hi", [f"${h:02X}" for _, h, _ in table]),
        row("slide_meta_lo", [f"${m & 0xFF:02X}" for _, _, m in table]),
        row("slide_meta_hi", [f"${m >> 8:02X}" for _, _, m in table]),
    ]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def render_preview(pic: Picture, rgb):
    from PIL import Image
    img = Image.new("RGB", (W, H))
    img.putdata([rgb[pic.color_index(x, y)] for y in range(H) for x in range(W)])
    return img.resize((W * 2, H * 2), Image.Resampling.NEAREST)


def main() -> int:
    ap = argparse.ArgumentParser(description="スライドショー ROM 用に画像をまとめる")
    ap.add_argument("inputs", nargs="*", help="画像 / .npaint.json / フォルダ（省略時 assets/slides）")
    ap.add_argument("-o", "--out", default=str(ROOT / "build" / "slides"))
    ap.add_argument("--no-test", action="store_true", help="色見本・テスト模様を付けない")
    ap.add_argument("--no-chart", action="store_true", help="色見本だけ付けない")
    ap.add_argument("--fit", default="fill")
    ap.add_argument("--resample", default="lanczos")
    ap.add_argument("--dither", default="fs")
    ap.add_argument("--strength", type=float, default=0.75)
    ap.add_argument("--bg")
    ap.add_argument("--quality", type=int, default=4)
    ap.add_argument("--pal", help="プレビュー用パレット .pal（既定: common/palette/nes.pal）")
    args = ap.parse_args()

    pal_path = Path(args.pal) if args.pal else ROOT / "common" / "palette" / "nes.pal"
    rgb = nes_palette.load_pal(pal_path) if pal_path.exists() else nes_palette.builtin_rgb()

    inputs = args.inputs
    if not inputs:
        default_dir = ROOT / "assets" / "slides"
        default_dir.mkdir(parents=True, exist_ok=True)
        inputs = [str(default_dir)]
        print(f"入力: {default_dir}（画像や .npaint.json をここに置くと ROM に入る）")

    slides = load_slides(collect(inputs), args, rgb)
    if not args.no_test:
        if not args.no_chart:
            slides += palette_chart_pages(rgb)
        slides.append(test_pattern_page())
    if not slides:
        print("[ERROR] 画像が 1 枚もありません（--no-test を外すとテストページだけの ROM を作れます）",
              file=sys.stderr)
        return 1
    if len(slides) > MAX_SLIDES:
        print(f"[注意] 入るのは {MAX_SLIDES} 枚までです。{len(slides) - MAX_SLIDES} 枚を省きます: "
              + ", ".join(s.name for s in slides[MAX_SLIDES:]), file=sys.stderr)
        slides = slides[:MAX_SLIDES]

    out = Path(args.out)
    (out / "preview").mkdir(parents=True, exist_ok=True)
    for old in (out / "preview").glob("*.png"):
        old.unlink()
    banks, table = pack(slides)
    for i, b in enumerate(banks):
        (out / f"bank{i}.bin").write_bytes(b)
    write_table(out / "slides_table.inc", slides, table)

    names = []
    can_preview = True
    try:
        import PIL  # noqa: F401
    except ImportError:
        can_preview = False
    with open(out / "slides.txt", "w", encoding="utf-8") as fp:
        for i, s in enumerate(slides):
            fp.write(f"{i + 1:2d}  {s.name}\n")
            fname = f"{i + 1:02d}_{''.join(c if c.isalnum() else '_' for c in s.name)}.png"
            if can_preview:
                render_preview(s.picture, rgb).save(out / "preview" / fname)
            names.append((i + 1, s.name, fname))
    if can_preview:
        with open(out / "index.html", "w", encoding="utf-8") as fp:
            fp.write("<!doctype html><meta charset='utf-8'><title>スライドショーの中身</title>"
                     "<style>body{font-family:sans-serif;background:#222;color:#ddd}"
                     "div{display:inline-block;margin:6px;text-align:center}"
                     "img{image-rendering:pixelated}</style><h1>スライドショーの中身</h1>")
            for no, name, fname in names:
                fp.write(f"<div><img src='preview/{html.escape(fname)}'><br>{no}. {html.escape(name)}</div>")

    print(f"{len(slides)} 枚をまとめました（最大 {MAX_SLIDES} 枚）: {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
