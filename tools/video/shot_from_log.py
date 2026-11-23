#!/usr/bin/env python3
"""mesen_timing_shot.lua が出力した「ROW y 色*個数,...」を PNG にする（2 枚目があれば _2 を付ける）
    python tools/video/shot_from_log.py ログ.txt 出力.png
"""
import sys
from PIL import Image

rows, shots = {}, []
for line in open(sys.argv[1], encoding="utf-8-sig", errors="replace"):
    if line.startswith("ROW "):
        _, y, data = line.strip().split(" ", 2)
        px = []
        for part in data.split(","):
            c, n = part.split("*")
            v = int(c, 16)
            px += [((v >> 16) & 255, (v >> 8) & 255, v & 255)] * int(n)
        rows[int(y)] = px
        if int(y) == 239:
            shots.append(rows)
            rows = {}
for k, sh in enumerate(shots):
    im = Image.new("RGB", (256, 240))
    im.putdata([p for y in range(240) for p in sh[y]])
    out = sys.argv[2] if k == 0 else sys.argv[2].replace(".png", f"_{k + 1}.png")
    im.resize((512, 480), Image.NEAREST).save(out)
    print(out)
