"""
paint_model.py - お絵描きデータとファミコン形式への変換（GUI に依存しない部分）

キャンバス: 128x120 ドット = 16x15 タイル（ROM 側 main.s の CANVAS_* と一致）
各ドットは「スロット番号 0〜3」を持つ。
  0 = 共通背景色, 1〜3 = そのドットが属する 16x16 ブロックのパレットの色1〜3
16x16 ブロックごとに BG パレット番号 0〜3 を持つ（ファミコンの属性テーブルの制約）。
"""

from __future__ import annotations

import json
import os
import struct
import time
from dataclasses import dataclass, field
from pathlib import Path

# ---- 画面構成（nes/src/main.s と一致させる） --------------------------------
W, H = 128, 120              # キャンバスのドット数
TILES_X, TILES_Y = 16, 15
CANVAS_COL, CANVAS_ROW = 8, 8  # 画面上のキャンバス左上タイル位置
BLOCKS_X, BLOCKS_Y = 8, 8    # 16x16 ブロック数（最下段は半分だけキャンバス）
TILE_OUTSIDE = 254
TILE_OUTSIDE_DATA = bytes([0x88, 0, 0, 0, 0x88, 0, 0, 0] + [0] * 8)

# 初期パレット（main.s の default_palette と一致させる）
DEFAULT_BG = 0x30
DEFAULT_PALS = [
    [0x0F, 0x00, 0x10],
    [0x06, 0x16, 0x27],
    [0x09, 0x19, 0x2A],
    [0x01, 0x11, 0x21],
]

# ---- state.bin（tools/emu_bridge/bridge.lua と一致させる） -------------------
STATE_MAGIC = b"NESPAINT"
STATE_VERSION = 1
CHR_SIZE, ATTR_SIZE, PAL_SIZE = 4096, 64, 32
PAYLOAD_SIZE = CHR_SIZE + ATTR_SIZE + PAL_SIZE
STATE_SIZE = 16 + PAYLOAD_SIZE + 4


def block_of(x: int, y: int) -> int:
    return (y // 16) * BLOCKS_X + (x // 16)


@dataclass
class Picture:
    pixels: bytearray = field(default_factory=lambda: bytearray(W * H))
    block_pal: bytearray = field(default_factory=lambda: bytearray(BLOCKS_X * BLOCKS_Y))
    bg: int = DEFAULT_BG
    pals: list[list[int]] = field(default_factory=lambda: [p[:] for p in DEFAULT_PALS])

    # ---- 参照 ---------------------------------------------------------------
    def color_index(self, x: int, y: int) -> int:
        """ドット (x,y) のファミコン色番号 ($00-$3F)"""
        v = self.pixels[y * W + x]
        if v == 0:
            return self.bg
        return self.pals[self.block_pal[block_of(x, y)]][v - 1]

    def slot_color(self, pal: int, slot: int) -> int:
        return self.bg if slot == 0 else self.pals[pal][slot - 1]

    def set_slot_color(self, pal: int, slot: int, color: int) -> None:
        if slot == 0:
            self.bg = color
        else:
            self.pals[pal][slot - 1] = color

    # ---- スナップショット（元に戻す用） -----------------------------------
    def snapshot(self) -> tuple:
        return (bytes(self.pixels), bytes(self.block_pal), self.bg, [p[:] for p in self.pals])

    def restore(self, snap: tuple) -> None:
        px, bp, bg, pals = snap
        self.pixels = bytearray(px)
        self.block_pal = bytearray(bp)
        self.bg = bg
        self.pals = [p[:] for p in pals]

    def clear(self) -> None:
        self.pixels = bytearray(W * H)
        self.block_pal = bytearray(BLOCKS_X * BLOCKS_Y)

    # ---- ファミコン形式へ変換 ---------------------------------------------
    def to_chr(self) -> bytes:
        """パターンテーブル $0000-$0FFF（256 タイル x 16 バイト, 2bpp プレーン形式）"""
        chr_ = bytearray(CHR_SIZE)
        px = self.pixels
        for ty in range(TILES_Y):
            for tx in range(TILES_X):
                base = (ty * TILES_X + tx) * 16
                for r in range(8):
                    row = (ty * 8 + r) * W + tx * 8
                    p0 = p1 = 0
                    for c in range(8):
                        v = px[row + c]
                        p0 = (p0 << 1) | (v & 1)
                        p1 = (p1 << 1) | (v >> 1)
                    chr_[base + r] = p0
                    chr_[base + 8 + r] = p1
        chr_[TILE_OUTSIDE * 16:TILE_OUTSIDE * 16 + 16] = TILE_OUTSIDE_DATA
        return bytes(chr_)

    def to_attr(self) -> bytes:
        """属性テーブル $23C0-$23FF（64 バイト）"""
        attr = bytearray(ATTR_SIZE)
        for by in range(BLOCKS_Y):
            for bx in range(BLOCKS_X):
                gbx = CANVAS_COL // 2 + bx     # 画面全体での 16x16 ブロック座標
                gby = CANVAS_ROW // 2 + by
                idx = (gby // 2) * 8 + (gbx // 2)
                shift = ((gby % 2) * 2 + (gbx % 2)) * 2
                attr[idx] |= (self.block_pal[by * BLOCKS_X + bx] & 3) << shift
        return bytes(attr)

    def to_ppu_palette(self) -> bytes:
        """パレット $3F00-$3F1F（32 バイト）。スプライト側も同じ値（$3F10 は $3F00 のミラー）"""
        bg16 = bytearray()
        for p in self.pals:
            bg16 += bytes([self.bg] + p)
        return bytes(bg16 * 2)

    def payload(self) -> bytes:
        return self.to_chr() + self.to_attr() + self.to_ppu_palette()

    # ---- 保存 / 読み込み（.npaint.json） ----------------------------------
    def to_json(self) -> dict:
        return {
            "format": "nespaint",
            "version": 1,
            "width": W,
            "height": H,
            "bg": self.bg,
            "palettes": self.pals,
            "block_pal": list(self.block_pal),
            "pixels": self.pixels.hex(),
        }

    @classmethod
    def from_json(cls, d: dict) -> "Picture":
        if d.get("format") != "nespaint" or d.get("width") != W or d.get("height") != H:
            raise ValueError("対応していないファイル形式です")
        p = cls()
        p.bg = int(d["bg"])
        p.pals = [[int(c) for c in pal] for pal in d["palettes"]]
        p.block_pal = bytearray(d["block_pal"])
        p.pixels = bytearray.fromhex(d["pixels"])
        if len(p.pixels) != W * H or len(p.block_pal) != BLOCKS_X * BLOCKS_Y:
            raise ValueError("データサイズが不正です")
        return p

    def save(self, path: str | Path) -> None:
        Path(path).write_text(json.dumps(self.to_json()), encoding="utf-8")

    @classmethod
    def load(cls, path: str | Path) -> "Picture":
        return cls.from_json(json.loads(Path(path).read_text(encoding="utf-8")))

    def export_nes_data(self, base: str | Path) -> list[Path]:
        """第3フェーズ以降で ROM に埋め込む用の生データを書き出す"""
        base = Path(base)
        out = [
            (base.with_name(base.name + "_chr.bin"), self.to_chr()),
            (base.with_name(base.name + "_attr.bin"), self.to_attr()),
            (base.with_name(base.name + "_pal.bin"), self.to_ppu_palette()),
        ]
        for path, data in out:
            path.write_bytes(data)
        return [p for p, _ in out]


def build_state(seq: int, payload: bytes) -> bytes:
    assert len(payload) == PAYLOAD_SIZE
    seq &= 0xFFFFFFFF
    data = STATE_MAGIC + struct.pack("<IHH", seq, STATE_VERSION, 0) + payload + struct.pack("<I", seq)
    assert len(data) == STATE_SIZE
    return data


def write_state_atomic(path: Path, data: bytes, retries: int = 10) -> bool:
    """一時ファイルに書いてから置き換える（Lua 側が書き込み途中を読まないように）。
    Windows では Lua がファイルを開いている瞬間は置き換えに失敗するので再試行する。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_bytes(data)
    for _ in range(retries):
        try:
            os.replace(tmp, path)
            return True
        except PermissionError:
            time.sleep(0.005)
    return False
