"""
nes_palette.py - ファミコン 64色パレット（RGB）の定義と .pal ファイル入出力

全ツール（お絵描き GUI / 減色ツール / RP2354 側）で同じ RGB テーブルを共有するため、
common/palette/nes.pal（64色 x RGB 3バイト = 192バイト）を基準ファイルとする。

使い方（共通パレットファイルの書き出し）:
    python tools/paint/nes_palette.py --export common/palette/nes.pal
"""

from __future__ import annotations

import argparse
from pathlib import Path

# 一般的に使われるファミコン(2C02)近似パレット。
# 実機との色味差は「近似値で妥協」の方針（PLAN.md 参照）。
# Mesen2 などで書き出した .pal を読み込めば差し替えられる。
BUILTIN_RGB_HEX = [
    # $00-$0F
    "7C7C7C", "0000FC", "0000BC", "4428BC", "940084", "A80020", "A81000", "881400",
    "503000", "007800", "006800", "005800", "004058", "000000", "000000", "000000",
    # $10-$1F
    "BCBCBC", "0078F8", "0058F8", "6844FC", "D800CC", "E40058", "F83800", "E45C10",
    "AC7C00", "00B800", "00A800", "00A844", "008888", "000000", "000000", "000000",
    # $20-$2F
    "F8F8F8", "3CBCFC", "6888FC", "9878F8", "F878F8", "F85898", "F87858", "FCA044",
    "F8B800", "B8F818", "58D854", "58F898", "00E8D8", "787878", "000000", "000000",
    # $30-$3F
    "FCFCFC", "A4E4FC", "B8B8F8", "D8B8F8", "F8B8F8", "F8A4C0", "F0D0B0", "FCE0A8",
    "F8D878", "D8F878", "B8F8B8", "B8F8D8", "00FCFC", "F8D8F8", "000000", "000000",
]

# 使わない方がよい色番号
#   $0D: 「黒より黒い」信号。一部のテレビで同期が乱れる
#   $xE, $xF: 黒（$0F を黒として使うのが慣習）
DISCOURAGED = {0x0D}
BLACK = 0x0F


def builtin_rgb() -> list[tuple[int, int, int]]:
    return [(int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)) for h in BUILTIN_RGB_HEX]


def load_pal(path: str | Path) -> list[tuple[int, int, int]]:
    """64x3 バイトの .pal を読む。エンファシス付き（512色=1536バイト）なら先頭64色を使う。"""
    data = Path(path).read_bytes()
    if len(data) < 192:
        raise ValueError(f"{path}: .pal のサイズが不正です ({len(data)} バイト, 192 以上必要)")
    return [(data[i * 3], data[i * 3 + 1], data[i * 3 + 2]) for i in range(64)]


def save_pal(path: str | Path, rgb: list[tuple[int, int, int]]) -> None:
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_bytes(bytes(c for color in rgb for c in color))


def to_hex(rgb: tuple[int, int, int]) -> str:
    return "#%02X%02X%02X" % rgb


def main() -> None:
    ap = argparse.ArgumentParser(description="ファミコンパレット .pal の書き出し")
    ap.add_argument("--export", metavar="PATH", required=True, help="書き出し先 .pal")
    args = ap.parse_args()
    save_pal(args.export, builtin_rgb())
    print(f"wrote {args.export}")


if __name__ == "__main__":
    main()
