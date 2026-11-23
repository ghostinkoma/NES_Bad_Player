"""
batch_convert.py - 画像をまとめてファミコン形式へ事前変換する（第2フェーズ）

方針（PLAN.md）: 「ディザ画像をあらかじめ用意し、できるだけ違和感を抑えるまでにとどめる」
→ 表示したい画像を前もってこのツールで変換しておき、出来の良い設定を選んで保存しておく。
  同じ画像をディザの種類ごとに書き出して見比べることもできる（--compare）。

使い方:
    python tools/converter/batch_convert.py 入力(ファイル or フォルダ) [...] -o 出力フォルダ
    python tools/converter/batch_convert.py assets\\photos -o build\\converted --dither fs --strength 0.7
    python tools/converter/batch_convert.py cat.jpg -o build\\converted --compare

出力（画像ごと）:
    <名前>.npaint.json   お絵描きツール（nes_paint.py）で開いて手直しできる
    <名前>_nes.png       変換結果のプレビュー（等倍）
    <名前>_x3.png        変換結果のプレビュー（3倍、見比べ用）
    <名前>_chr.bin / _attr.bin / _pal.bin   ROM に埋め込む用の生データ
    index.html           一覧ページ（元画像と変換結果を並べて表示）
"""

from __future__ import annotations

import argparse
import html
import sys
import time
from pathlib import Path

from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parent))

import nes_convert as nc  # noqa: E402

EXTS = {".png", ".jpg", ".jpeg", ".bmp", ".gif", ".webp", ".tif", ".tiff"}


def collect(inputs: list[str]) -> list[Path]:
    files: list[Path] = []
    for s in inputs:
        p = Path(s)
        if p.is_dir():
            files += sorted(f for f in p.iterdir() if f.suffix.lower() in EXTS)
        elif p.is_file():
            files.append(p)
        else:
            print(f"[skip] 見つかりません: {p}", file=sys.stderr)
    return files


def main() -> int:
    ap = argparse.ArgumentParser(description="ファミコン形式への一括減色変換")
    ap.add_argument("inputs", nargs="+", help="画像ファイルまたはフォルダ")
    ap.add_argument("-o", "--out", default="build/converted", help="出力フォルダ（既定: build/converted）")
    ap.add_argument("--fit", choices=list(nc.FIT_MODES), default="fill")
    ap.add_argument("--resample", choices=list(nc.RESAMPLE_MODES), default="lanczos",
                    help="用意済みのドット絵・ディザ画像は nearest を推奨")
    ap.add_argument("--dither", choices=list(nc.DITHER_MODES), default="fs")
    ap.add_argument("--strength", type=float, default=0.75, help="ディザの強さ 0〜1")
    ap.add_argument("--metric", choices=["lab", "rgb"], default="lab")
    ap.add_argument("--bg", help="背景色を固定（16進。例: 0F）")
    ap.add_argument("--quality", type=int, default=4, help="背景色の候補数（1〜8）")
    ap.add_argument("--brightness", type=float, default=1.0)
    ap.add_argument("--contrast", type=float, default=1.0)
    ap.add_argument("--saturation", type=float, default=1.0)
    ap.add_argument("--gamma", type=float, default=1.0)
    ap.add_argument("--compare", action="store_true", help="ディザなし/パターン/誤差拡散の3種類を書き出す")
    ap.add_argument("--pal", help="表示用パレット .pal（既定: common/palette/nes.pal）")
    args = ap.parse_args()

    root = Path(__file__).resolve().parents[2]
    pal_path = Path(args.pal) if args.pal else root / "common" / "palette" / "nes.pal"
    rgb = nc.nes_palette.load_pal(pal_path) if pal_path.exists() else nc.nes_palette.builtin_rgb()

    base = nc.Params(fit=args.fit, resample=args.resample, dither=args.dither,
                     dither_strength=args.strength, metric=args.metric,
                     bg=int(args.bg, 16) & 0x3F if args.bg else None,
                     bg_candidates=max(1, min(8, args.quality)),
                     brightness=args.brightness, contrast=args.contrast,
                     saturation=args.saturation, gamma=args.gamma)
    variants = ([("none", nc.with_params(base, dither="none")),
                 ("ordered", nc.with_params(base, dither="ordered")),
                 ("fs", nc.with_params(base, dither="fs"))] if args.compare else [("", base)])

    files = collect(args.inputs)
    if not files:
        print("変換する画像がありません", file=sys.stderr)
        return 1
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)

    rows = []
    for f in files:
        try:
            img = Image.open(f)
            img.load()
        except OSError as ex:
            print(f"[skip] {f}: {ex}", file=sys.stderr)
            continue
        cells = []
        src_name = f"{f.stem}_src.png"
        for tag, p in variants:
            t0 = time.time()
            r = nc.convert(img, p, rgb)
            name = f.stem + (f"_{tag}" if tag else "")
            r.picture.save(out / f"{name}.npaint.json")
            r.preview.save(out / f"{name}_nes.png")
            r.preview.resize((nc.W * 3, nc.H * 3), Image.Resampling.NEAREST).save(out / f"{name}_x3.png")
            r.picture.export_nes_data(out / name)
            r.source.resize((nc.W * 3, nc.H * 3), Image.Resampling.NEAREST).save(out / src_name)
            print(f"{f.name:30s} {tag or p.dither:8s} ΔE={r.delta_e:5.1f}  {time.time() - t0:4.1f}s  -> {name}")
            cells.append((tag or p.dither, f"{name}_x3.png", r.delta_e))
        rows.append((f.name, src_name, cells))

    with open(out / "index.html", "w", encoding="utf-8") as fp:
        fp.write("<!doctype html><meta charset='utf-8'><title>NES 変換結果</title>"
                 "<style>body{font-family:sans-serif;background:#222;color:#ddd}"
                 "img{image-rendering:pixelated;margin:4px}td{vertical-align:top;padding:6px}</style>"
                 "<h1>NES 変換結果</h1><table>")
        for name, src, cells in rows:
            fp.write(f"<tr><td>{html.escape(name)}<br><img src='{html.escape(src)}'><br>元画像</td>")
            for tag, img_name, de in cells:
                fp.write(f"<td><br><img src='{html.escape(img_name)}'><br>{html.escape(tag)} ΔE={de:.1f}</td>")
            fp.write("</tr>")
        fp.write("</table>")
    print(f"一覧: {out / 'index.html'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
