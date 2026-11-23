"""
gen_ui_tiles.py - スライドショー ROM の表示用タイル（タイル 240〜255）を作る（第3フェーズ）

出力: nes/src/ui_tiles.inc（slideshow.s が .include する。生成済みのものを置いてあるので、
      形を変えたいときだけ実行し直せばよい）

    python tools/slideshow/gen_ui_tiles.py

タイル番号（slideshow.s の TILE_* と一致させる）
    240〜249  数字 0〜9         （スプライト: 色1=白 の字に 色3=黒 のふち）
    250       /
    251       ▶  自動再生中
    252       □  ボタンを離している
    253       ■  ボタンを押している（色2=オレンジ）
    254       キャンバス外の点模様（BG 用。第1フェーズと同じ）
    255       ⌛  読み込み中
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools" / "paint"))

from paint_model import TILE_OUTSIDE_DATA  # noqa: E402

# 5x7 の字（1 = 字のドット）
GLYPHS_5X7 = {
    "0": ["01110", "10001", "10011", "10101", "11001", "10001", "01110"],
    "1": ["00100", "01100", "00100", "00100", "00100", "00100", "01110"],
    "2": ["01110", "10001", "00001", "00010", "00100", "01000", "11111"],
    "3": ["11111", "00010", "00100", "00010", "00001", "10001", "01110"],
    "4": ["00010", "00110", "01010", "10010", "11111", "00010", "00010"],
    "5": ["11111", "10000", "11110", "00001", "00001", "10001", "01110"],
    "6": ["00110", "01000", "10000", "11110", "10001", "10001", "01110"],
    "7": ["11111", "00001", "00010", "00100", "01000", "01000", "01000"],
    "8": ["01110", "10001", "10001", "01110", "10001", "10001", "01110"],
    "9": ["01110", "10001", "10001", "01111", "00001", "00010", "01100"],
    "/": ["00001", "00010", "00010", "00100", "01000", "01000", "10000"],
    "play": ["10000", "11000", "11100", "11110", "11100", "11000", "10000"],
    "busy": ["11111", "10001", "01010", "00100", "01010", "10001", "11111"],
}


def glyph_tile(rows: list[str], color: int = 1) -> list[list[int]]:
    """5x7 の字を 8x8 の 1〜5 列・0〜6 行に置き、周り 8 方向に黒ふち（色3）を付ける"""
    t = [[0] * 8 for _ in range(8)]
    for y, row in enumerate(rows):
        for x, ch in enumerate(row):
            if ch == "1":
                t[y][x + 1] = color
    out = [r[:] for r in t]
    for y in range(8):
        for x in range(8):
            if t[y][x]:
                continue
            if any(0 <= y + dy < 8 and 0 <= x + dx < 8 and t[y + dy][x + dx]
                   for dy in (-1, 0, 1) for dx in (-1, 0, 1)):
                out[y][x] = 3
    return out


def box_tile(filled: bool) -> list[list[int]]:
    """7x7 の四角（右・下の1列は隣との隙間）。
    □: 黒ふち・白わく・黒ふち の3重線（どんな背景でも見える）、中は透明
    ■: 黒ふちの中をオレンジ（色2）で塗る"""
    t = [[0] * 8 for _ in range(8)]
    for y in range(7):
        for x in range(7):
            ring = min(x, y, 6 - x, 6 - y)   # 外側から 0,1,2,3
            if filled:
                t[y][x] = 3 if ring == 0 else 2
            else:
                t[y][x] = {0: 3, 1: 1, 2: 3}.get(ring, 0)
    return t


def encode(t: list[list[int]]) -> bytes:
    p0 = bytearray(8)
    p1 = bytearray(8)
    for y in range(8):
        for x in range(8):
            v = t[y][x]
            p0[y] |= (v & 1) << (7 - x)
            p1[y] |= (v >> 1) << (7 - x)
    return bytes(p0 + p1)


def build() -> list[tuple[int, str, bytes]]:
    tiles = []
    for i in range(10):
        tiles.append((240 + i, f"数字 {i}", encode(glyph_tile(GLYPHS_5X7[str(i)]))))
    tiles.append((250, "/", encode(glyph_tile(GLYPHS_5X7["/"]))))
    tiles.append((251, "自動再生 ▶", encode(glyph_tile(GLYPHS_5X7["play"]))))
    tiles.append((252, "ボタン離し □", encode(box_tile(False))))
    tiles.append((253, "ボタン押し ■", encode(box_tile(True))))
    tiles.append((254, "キャンバス外の点模様", TILE_OUTSIDE_DATA))
    tiles.append((255, "読み込み中", encode(glyph_tile(GLYPHS_5X7["busy"]))))
    return tiles


def main() -> int:
    out = ROOT / "nes" / "src" / "ui_tiles.inc"
    lines = [
        "; =============================================================================",
        "; ui_tiles.inc - スライドショー ROM の表示用タイル 240〜255（16 タイル x 16 バイト）",
        "; tools/slideshow/gen_ui_tiles.py が生成。手で書き換えないこと",
        "; =============================================================================",
        "ui_tiles:",
    ]
    for num, name, data in build():
        lines.append(f"    ; {num}: {name}")
        lines.append("    .byte " + ", ".join(f"${b:02X}" for b in data[:8]) + "   ; プレーン0")
        lines.append("    .byte " + ", ".join(f"${b:02X}" for b in data[8:]) + "   ; プレーン1")
    out.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"書き出しました: {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
