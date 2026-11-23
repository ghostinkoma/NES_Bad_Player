"""
convert_gui.py - 第2フェーズ 減色変換ツール（Tkinter）

画像を開く（またはクリップボードから貼り付ける）と、ファミコンの制約に合わせて減色し、
結果を state.bin としてブリッジフォルダへ書き出す → 第1フェーズの ROM + bridge.lua で
エミュレータにリアルタイム表示される。パラメータを動かすたびに再変換・再送信する。

起動:
    python tools/converter/convert_gui.py [画像ファイル]
    python tools/converter/convert_gui.py --bridge D:/hobby/nes/project/build/bridge

※ お絵描きツール（nes_paint.py）と同時に動かすと、両方が state.bin を書くので最後に書いた方が表示される。
"""

from __future__ import annotations

import argparse
import sys
import threading
import time
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

from PIL import Image, ImageGrab, ImageTk

sys.path.insert(0, str(Path(__file__).resolve().parent))

import nes_convert as nc  # noqa: E402
from nes_convert import DITHER_MODES, FIT_MODES, Params  # noqa: E402
from paint_model import H, W, build_state, write_state_atomic  # noqa: E402

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_BRIDGE = PROJECT_ROOT / "build" / "bridge"
COMMON_PAL = PROJECT_ROOT / "common" / "palette" / "nes.pal"
ZOOM = 3
DEBOUNCE_MS = 150
WATCH_MS = 500

IMAGE_TYPES = [("画像", "*.png *.jpg *.jpeg *.bmp *.gif *.webp *.tif *.tiff"), ("すべて", "*.*")]
RESAMPLE_LABELS = {"lanczos": "なめらか（写真向け）", "box": "平均（縮小向け）",
                   "nearest": "ニアレスト（ドット絵・ディザ済み画像向け）"}


class ConvertApp:
    def __init__(self, root: tk.Tk, bridge_dir: Path):
        self.root = root
        self.bridge_dir = bridge_dir
        self.rgb_table = self._load_rgb()
        self.src_image: Image.Image | None = None
        self.src_path: Path | None = None
        self.src_mtime = 0.0
        self.result: nc.Result | None = None
        self.seq = int(time.time() * 1000) & 0x7FFFFFFF
        self.job = None
        self.generation = 0
        self.busy = False

        self.v_fit = tk.StringVar(value="fill")
        self.v_resample = tk.StringVar(value="lanczos")
        self.v_dither = tk.StringVar(value="fs")
        self.v_metric = tk.StringVar(value="lab")
        self.v_strength = tk.DoubleVar(value=0.75)
        self.v_brightness = tk.DoubleVar(value=1.0)
        self.v_contrast = tk.DoubleVar(value=1.0)
        self.v_saturation = tk.DoubleVar(value=1.0)
        self.v_gamma = tk.DoubleVar(value=1.0)
        self.v_sharpness = tk.DoubleVar(value=1.0)
        self.v_bg_auto = tk.BooleanVar(value=True)
        self.v_bg = tk.StringVar(value="0F")
        self.v_quality = tk.IntVar(value=4)
        self.v_auto_send = tk.BooleanVar(value=True)
        self.v_watch = tk.BooleanVar(value=False)
        self.v_grid = tk.BooleanVar(value=False)

        self._build_ui()
        self.root.after(WATCH_MS, self._watch_file)

    def _load_rgb(self):
        if COMMON_PAL.exists():
            try:
                return nc.nes_palette.load_pal(COMMON_PAL)
            except ValueError:
                pass
        return nc.nes_palette.builtin_rgb()

    # ------------------------------------------------------------------ UI
    def _build_ui(self):
        self.root.title("NES 減色変換 - 第2フェーズ")
        m = tk.Menu(self.root)
        fm = tk.Menu(m, tearoff=0)
        fm.add_command(label="画像を開く... (Ctrl+O)", command=self.open_image)
        fm.add_command(label="クリップボードから貼り付け (Ctrl+V)", command=self.paste_image)
        fm.add_separator()
        fm.add_command(label="お絵描きツール用に保存 (.npaint.json)... (Ctrl+S)", command=self.save_npaint)
        fm.add_command(label="プレビュー画像を保存 (.png)...", command=self.save_preview)
        fm.add_command(label="ファミコン用データ書き出し (CHR/属性/パレット)...", command=self.export_nes)
        fm.add_separator()
        fm.add_command(label="ブリッジフォルダ変更...", command=self.change_bridge)
        fm.add_separator()
        fm.add_command(label="終了", command=self.root.destroy)
        m.add_cascade(label="ファイル", menu=fm)
        self.root.config(menu=m)
        self.root.bind("<Control-o>", lambda e: self.open_image())
        self.root.bind("<Control-v>", lambda e: self.paste_image())
        self.root.bind("<Control-s>", lambda e: self.save_npaint())

        top = tk.Frame(self.root)
        top.pack(fill="both", expand=True)

        views = tk.Frame(top)
        views.grid(row=0, column=0, padx=6, pady=6, sticky="n")
        tk.Label(views, text="元画像（128×120 に整えたもの）").grid(row=0, column=0)
        tk.Label(views, text="変換後（ファミコンでの見え方）").grid(row=0, column=1)
        self.cv_src = tk.Canvas(views, width=W * ZOOM, height=H * ZOOM, bg="#202020", highlightthickness=0)
        self.cv_dst = tk.Canvas(views, width=W * ZOOM, height=H * ZOOM, bg="#202020", highlightthickness=0)
        self.cv_src.grid(row=1, column=0, padx=4)
        self.cv_dst.grid(row=1, column=1, padx=4)
        for cv in (self.cv_src, self.cv_dst):
            cv.create_text(W * ZOOM // 2, H * ZOOM // 2, fill="#808080", tags="hint",
                           text="ファイル → 画像を開く\nまたは Ctrl+V で貼り付け")
        self.pal_frame = tk.Frame(views)
        self.pal_frame.grid(row=2, column=0, columnspan=2, pady=6, sticky="w")

        side = tk.Frame(top)
        side.grid(row=0, column=1, padx=6, pady=6, sticky="n")

        f = tk.LabelFrame(side, text="サイズ合わせ")
        f.pack(fill="x", pady=3)
        self._combo(f, "配置", self.v_fit, FIT_MODES)
        self._combo(f, "縮小方法", self.v_resample, RESAMPLE_LABELS)

        f = tk.LabelFrame(side, text="画質調整")
        f.pack(fill="x", pady=3)
        self._slider(f, "明るさ", self.v_brightness, 0.3, 2.0)
        self._slider(f, "コントラスト", self.v_contrast, 0.3, 2.5)
        self._slider(f, "彩度", self.v_saturation, 0.0, 2.5)
        self._slider(f, "ガンマ", self.v_gamma, 0.4, 2.5)
        self._slider(f, "シャープ", self.v_sharpness, 0.0, 3.0)
        tk.Button(f, text="調整を元に戻す", command=self.reset_adjust).pack(anchor="e", padx=4, pady=2)

        f = tk.LabelFrame(side, text="減色")
        f.pack(fill="x", pady=3)
        self._combo(f, "ディザ", self.v_dither, DITHER_MODES)
        self._slider(f, "ディザの強さ", self.v_strength, 0.0, 1.0)
        self._combo(f, "色の比べ方", self.v_metric, {"lab": "Lab（見た目重視）", "rgb": "RGB"})
        row = tk.Frame(f)
        row.pack(fill="x", padx=4, pady=2)
        tk.Checkbutton(row, text="背景色を自動で選ぶ", variable=self.v_bg_auto,
                       command=self.schedule).pack(side="left")
        tk.Label(row, text=" 固定: $").pack(side="left")
        e = tk.Entry(row, textvariable=self.v_bg, width=4)
        e.pack(side="left")
        e.bind("<Return>", lambda ev: self.schedule())
        e.bind("<FocusOut>", lambda ev: self.schedule())
        self._slider(f, "品質（背景色の候補数）", self.v_quality, 1, 8, res=1)

        f = tk.LabelFrame(side, text="送信 / 表示")
        f.pack(fill="x", pady=3)
        tk.Checkbutton(f, text="エミュレータへ自動送信", variable=self.v_auto_send).pack(anchor="w")
        tk.Checkbutton(f, text="元画像ファイルの変更を監視して自動で再変換",
                       variable=self.v_watch).pack(anchor="w")
        tk.Checkbutton(f, text="属性グリッド (16×16) を表示", variable=self.v_grid,
                       command=self._draw_dst).pack(anchor="w")
        tk.Button(f, text="今すぐ送信", command=self.send_now).pack(anchor="w", padx=4, pady=2)
        self.v_bridge = tk.StringVar(value=str(self.bridge_dir))
        tk.Label(f, textvariable=self.v_bridge, fg="#0050a0", wraplength=300, justify="left").pack(anchor="w")

        self.status = tk.StringVar(value="画像を開いてください")
        tk.Label(self.root, textvariable=self.status, anchor="w", relief="sunken").pack(fill="x", side="bottom")

    def _combo(self, parent, label, var, choices: dict):
        row = tk.Frame(parent)
        row.pack(fill="x", padx=4, pady=2)
        tk.Label(row, text=label, width=10, anchor="w").pack(side="left")
        keys = list(choices)
        cb = ttk.Combobox(row, values=[choices[k] for k in keys], state="readonly", width=30)
        cb.current(keys.index(var.get()))
        cb.pack(side="left")

        def on_select(_e):
            var.set(keys[cb.current()])
            self.schedule()
        cb.bind("<<ComboboxSelected>>", on_select)

    def _slider(self, parent, label, var, lo, hi, res=0.05):
        row = tk.Frame(parent)
        row.pack(fill="x", padx=4)
        tk.Label(row, text=label, width=14, anchor="w").pack(side="left")
        tk.Scale(row, variable=var, from_=lo, to=hi, resolution=res, orient="horizontal",
                 length=180, command=lambda _v: self.schedule()).pack(side="left")

    def reset_adjust(self):
        for v in (self.v_brightness, self.v_contrast, self.v_saturation, self.v_gamma, self.v_sharpness):
            v.set(1.0)
        self.schedule()

    # ------------------------------------------------------------------ 入力
    def open_image(self, path: str | None = None):
        path = path or filedialog.askopenfilename(filetypes=IMAGE_TYPES)
        if not path:
            return
        try:
            img = Image.open(path)
            img.load()
        except OSError as ex:
            messagebox.showerror("画像を開く", f"読み込めませんでした:\n{ex}")
            return
        self.src_image = img
        self.src_path = Path(path)
        self.src_mtime = self.src_path.stat().st_mtime
        self.root.title(f"NES 減色変換 - {self.src_path.name}")
        self.schedule(0)

    def paste_image(self):
        data = ImageGrab.grabclipboard()
        if isinstance(data, list) and data:          # ファイルをコピーした場合はパスの一覧
            self.open_image(data[0])
            return
        if not isinstance(data, Image.Image):
            self.status.set("クリップボードに画像がありません")
            return
        self.src_image = data
        self.src_path = None
        self.root.title("NES 減色変換 - クリップボード")
        self.schedule(0)

    def _watch_file(self):
        if self.v_watch.get() and self.src_path is not None:
            try:
                mt = self.src_path.stat().st_mtime
                if mt != self.src_mtime:
                    self.src_mtime = mt
                    time.sleep(0.1)                   # 保存が終わるのを少し待つ
                    img = Image.open(self.src_path)
                    img.load()
                    self.src_image = img
                    self.schedule(0)
            except OSError:
                pass
        self.root.after(WATCH_MS, self._watch_file)

    # ------------------------------------------------------------------ 変換
    def params(self) -> Params:
        bg = None
        if not self.v_bg_auto.get():
            try:
                bg = int(self.v_bg.get().strip().lstrip("$"), 16) & 0x3F
            except ValueError:
                bg = 0x0F
        return Params(
            fit=self.v_fit.get(), resample=self.v_resample.get(),
            brightness=self.v_brightness.get(), contrast=self.v_contrast.get(),
            saturation=self.v_saturation.get(), gamma=self.v_gamma.get(),
            sharpness=self.v_sharpness.get(), dither=self.v_dither.get(),
            dither_strength=self.v_strength.get(), metric=self.v_metric.get(),
            bg=bg, bg_candidates=int(self.v_quality.get()))

    def schedule(self, delay: int = DEBOUNCE_MS):
        if self.src_image is None:
            return
        if self.job is not None:
            self.root.after_cancel(self.job)
        self.job = self.root.after(delay, self._start_convert)

    def _start_convert(self):
        self.job = None
        if self.busy:                    # 変換中なら終わってからもう一度
            self.schedule()
            return
        self.busy = True
        self.generation += 1
        gen, img, p = self.generation, self.src_image.copy(), self.params()
        self.status.set("変換中...")
        t0 = time.time()

        def work():
            try:
                res = nc.convert(img, p, self.rgb_table)
                err = None
            except Exception as ex:  # noqa: BLE001  GUI に表示するため全部受ける
                res, err = None, ex
            self.root.after(0, lambda: self._done(gen, res, err, time.time() - t0))
        threading.Thread(target=work, daemon=True).start()

    def _done(self, gen, res, err, secs):
        self.busy = False
        if err is not None:
            self.status.set(f"変換エラー: {err}")
            return
        if gen != self.generation:
            return
        self.result = res
        self._draw_src()
        self._draw_dst()
        self._draw_palettes()
        pic = res.picture
        self.status.set(f"変換 {secs:.2f} 秒  平均色差 ΔE={res.delta_e:.1f}  背景色 ${pic.bg:02X}  "
                        + "  ".join(f"P{i}:" + ",".join(f"{c:02X}" for c in p) for i, p in enumerate(pic.pals)))
        if self.v_auto_send.get():
            self.send_now()

    # ------------------------------------------------------------------ 表示
    def _draw_src(self):
        img = self.result.source.resize((W * ZOOM, H * ZOOM), Image.Resampling.NEAREST)
        self.tk_src = ImageTk.PhotoImage(img)
        self.cv_src.delete("all")
        self.cv_src.create_image(0, 0, image=self.tk_src, anchor="nw")

    def _draw_dst(self):
        if self.result is None:
            return
        img = self.result.preview.resize((W * ZOOM, H * ZOOM), Image.Resampling.NEAREST)
        self.tk_dst = ImageTk.PhotoImage(img)
        self.cv_dst.delete("all")
        self.cv_dst.create_image(0, 0, image=self.tk_dst, anchor="nw")
        if self.v_grid.get():
            for x in range(0, W + 1, 16):
                self.cv_dst.create_line(x * ZOOM, 0, x * ZOOM, H * ZOOM, fill="#E08000")
            for y in range(0, H + 1, 16):
                self.cv_dst.create_line(0, y * ZOOM, W * ZOOM, y * ZOOM, fill="#E08000")

    def _draw_palettes(self):
        for w in self.pal_frame.winfo_children():
            w.destroy()
        pic = self.result.picture
        hexc = lambda i: "#%02X%02X%02X" % tuple(self.rgb_table[i])  # noqa: E731
        for p in range(4):
            tk.Label(self.pal_frame, text=f"P{p}").pack(side="left", padx=(8, 2))
            for c in [pic.bg] + pic.pals[p]:
                tk.Label(self.pal_frame, text=f"{c:02X}", bg=hexc(c), width=3,
                         fg="black" if sum(self.rgb_table[c]) > 380 else "white").pack(side="left")

    # ------------------------------------------------------------------ 出力
    def send_now(self):
        if self.result is None:
            return
        self.seq = (self.seq + 1) & 0x7FFFFFFF
        data = build_state(self.seq, self.result.picture.payload())
        if not write_state_atomic(self.bridge_dir / "state.bin", data):
            self.root.after(50, self.send_now)

    def _need_result(self) -> bool:
        if self.result is None:
            messagebox.showinfo("保存", "先に画像を開いて変換してください")
            return False
        return True

    def _default_name(self, ext: str) -> str:
        return (self.src_path.stem if self.src_path else "clipboard") + ext

    def save_npaint(self):
        if not self._need_result():
            return
        path = filedialog.asksaveasfilename(defaultextension=".npaint.json",
                                            initialfile=self._default_name(".npaint.json"),
                                            filetypes=[("NES Paint", "*.npaint.json")])
        if path:
            self.result.picture.save(path)
            self.status.set(f"保存しました: {path}（お絵描きツールで開いて手直しできます）")

    def save_preview(self):
        if not self._need_result():
            return
        path = filedialog.asksaveasfilename(defaultextension=".png",
                                            initialfile=self._default_name("_nes.png"),
                                            filetypes=[("PNG", "*.png")])
        if path:
            self.result.preview.save(path)
            self.status.set(f"保存しました: {path}")

    def export_nes(self):
        if not self._need_result():
            return
        path = filedialog.asksaveasfilename(title="書き出し先（_chr.bin / _attr.bin / _pal.bin が作られます）",
                                            initialfile=self._default_name(""))
        if path:
            files = self.result.picture.export_nes_data(path)
            messagebox.showinfo("書き出し", "\n".join(str(f) for f in files))

    def change_bridge(self):
        d = filedialog.askdirectory(initialdir=str(self.bridge_dir))
        if d:
            self.bridge_dir = Path(d)
            self.v_bridge.set(d)
            self.send_now()


def main():
    ap = argparse.ArgumentParser(description="ファミコン減色変換ツール（第2フェーズ）")
    ap.add_argument("image", nargs="?", help="最初に開く画像")
    ap.add_argument("--bridge", default=str(DEFAULT_BRIDGE), help=f"state.bin の書き出し先（既定: {DEFAULT_BRIDGE}）")
    args = ap.parse_args()
    root = tk.Tk()
    app = ConvertApp(root, Path(args.bridge))
    if args.image:
        root.after(100, lambda: app.open_image(args.image))
    root.mainloop()


if __name__ == "__main__":
    main()
