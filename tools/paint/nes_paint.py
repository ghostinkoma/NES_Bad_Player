"""
nes_paint.py - 第1フェーズ ファミコン制約付きお絵描きツール（Tkinter）

描いた内容を state.bin としてブリッジフォルダへ書き出し、
エミュレータ上の bridge.lua がそれをポーリングして ROM 画面へ反映する。

起動:
    python tools/paint/nes_paint.py
    python tools/paint/nes_paint.py --bridge D:/hobby/nes/project/build/bridge --zoom 4

操作:
    左ドラッグ      : 現在のツール（ペン / 消しゴム / スポイト / パレット割当）
    右クリック      : スポイト（色とパレットを拾う）
    パレットの色枠  : クリックで描画色を選択
    64色表          : クリックで「選択中の色枠」の色を変更
    Ctrl+Z          : 元に戻す
    P / E / I / A   : ツール切り替え
    1〜3, 0         : 選択中パレットの色1〜3 / 背景色を選択
    G               : グリッド表示切り替え
"""

from __future__ import annotations

import argparse
import sys
import time
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox

sys.path.insert(0, str(Path(__file__).resolve().parent))

import nes_palette  # noqa: E402
from paint_model import (  # noqa: E402
    BLOCKS_X, H, W, Picture, block_of, build_state, write_state_atomic,
)

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_BRIDGE = PROJECT_ROOT / "build" / "bridge"
COMMON_PAL = PROJECT_ROOT / "common" / "palette" / "nes.pal"

TOOLS = [("pen", "ペン (P)"), ("eraser", "消しゴム (E)"),
         ("picker", "スポイト (I)"), ("assign", "パレット割当 (A)")]
UNDO_MAX = 50
SEND_DELAY_MS = 40       # 変更から state.bin 書き出しまでの待ち（連続描画をまとめる）
PAD_POLL_MS = 100


class NesPaintApp:
    def __init__(self, root: tk.Tk, bridge_dir: Path, zoom: int):
        self.root = root
        self.zoom = zoom
        self.bridge_dir = bridge_dir
        self.pic = Picture()
        self.rgb = self._load_rgb()
        self.cur_pal = 0
        self.cur_slot = 1
        self.tool = tk.StringVar(value="pen")
        self.show_tile_grid = tk.BooleanVar(value=True)
        self.show_attr_grid = tk.BooleanVar(value=True)
        self.auto_send = tk.BooleanVar(value=True)
        self.undo_stack: list[tuple] = []
        self.file_path: Path | None = None
        # 起動のたびに違う値から始める（再起動直後の seq がブリッジの前回値と偶然一致して無視されないように）
        self.seq = int(time.time() * 1000) & 0x7FFFFFFF
        self.send_job = None
        self.last_xy = None
        self.pad_mtime = 0.0

        self._build_ui()
        self.redraw_all()
        self.schedule_send()
        self.root.after(PAD_POLL_MS, self.poll_pad)

    # ------------------------------------------------------------------ 準備
    def _load_rgb(self):
        if COMMON_PAL.exists():
            try:
                return nes_palette.load_pal(COMMON_PAL)
            except ValueError:
                pass
        return nes_palette.builtin_rgb()

    def hex_of(self, color_index: int) -> str:
        return nes_palette.to_hex(self.rgb[color_index & 0x3F])

    def _build_ui(self):
        self.root.title("NES Paint - 第1フェーズ")
        self._build_menu()

        main = tk.Frame(self.root)
        main.pack(fill="both", expand=True)

        # ---- キャンバス
        cw, ch = W * self.zoom, H * self.zoom
        self.canvas = tk.Canvas(main, width=cw, height=ch, bg="black",
                                highlightthickness=0, cursor="crosshair")
        self.canvas.grid(row=0, column=0, padx=8, pady=8, sticky="n")
        self.img = tk.PhotoImage(width=cw, height=ch)
        self.img_id = self.canvas.create_image(0, 0, image=self.img, anchor="nw")
        self.canvas.bind("<ButtonPress-1>", self.on_press)
        self.canvas.bind("<B1-Motion>", self.on_drag)
        self.canvas.bind("<ButtonRelease-1>", self.on_release)
        self.canvas.bind("<ButtonPress-3>", self.on_right)
        self.canvas.bind("<Motion>", self.on_motion)

        # ---- 右パネル
        side = tk.Frame(main)
        side.grid(row=0, column=1, padx=8, pady=8, sticky="n")

        tf = tk.LabelFrame(side, text="ツール")
        tf.pack(fill="x", pady=4)
        for key, label in TOOLS:
            tk.Radiobutton(tf, text=label, value=key, variable=self.tool,
                           anchor="w").pack(fill="x")

        pf = tk.LabelFrame(side, text="BG パレット（左端は全パレット共通の背景色）")
        pf.pack(fill="x", pady=4)
        self.swatches: list[list[tk.Canvas]] = []
        for p in range(4):
            row = tk.Frame(pf)
            row.pack(anchor="w", pady=1)
            tk.Label(row, text=f"P{p}", width=3).pack(side="left")
            cells = []
            for s in range(4):
                c = tk.Canvas(row, width=34, height=26, highlightthickness=0, bd=0)
                c.pack(side="left", padx=2)
                c.bind("<Button-1>", lambda e, p=p, s=s: self.select_slot(p, s))
                cells.append(c)
            self.swatches.append(cells)

        nf = tk.LabelFrame(side, text="ファミコン 64色（クリックで選択中の色枠を変更）")
        nf.pack(fill="x", pady=4)
        self.nes_cells = []
        grid = tk.Frame(nf)
        grid.pack()
        for i in range(64):
            c = tk.Canvas(grid, width=20, height=20, highlightthickness=1,
                          highlightbackground="#444", bd=0)
            c.grid(row=i // 16, column=i % 16)
            c.bind("<Button-1>", lambda e, i=i: self.assign_color(i))
            c.bind("<Enter>", lambda e, i=i: self.status_var.set(self._color_info(i)))
            self.nes_cells.append(c)

        of = tk.LabelFrame(side, text="表示 / 送信")
        of.pack(fill="x", pady=4)
        tk.Checkbutton(of, text="タイルグリッド (8x8)", variable=self.show_tile_grid,
                       command=self.draw_grid).pack(anchor="w")
        tk.Checkbutton(of, text="属性グリッド (16x16)", variable=self.show_attr_grid,
                       command=self.draw_grid).pack(anchor="w")
        tk.Checkbutton(of, text="エミュレータへ自動送信", variable=self.auto_send).pack(anchor="w")
        bf = tk.Frame(of)
        bf.pack(fill="x", pady=2)
        tk.Button(bf, text="今すぐ送信", command=self.send_now).pack(side="left", padx=2)
        tk.Button(bf, text="全消去", command=self.clear_all).pack(side="left", padx=2)

        self.bridge_var = tk.StringVar(value=str(self.bridge_dir))
        tk.Label(of, text="ブリッジフォルダ:").pack(anchor="w")
        tk.Label(of, textvariable=self.bridge_var, fg="#0050a0", wraplength=330,
                 justify="left").pack(anchor="w")

        padf = tk.LabelFrame(side, text="ファミコンのパッド入力")
        padf.pack(fill="x", pady=4)
        self.pad_var = tk.StringVar(value="（未接続）")
        tk.Label(padf, textvariable=self.pad_var, font=("Consolas", 11)).pack(anchor="w")

        self.status_var = tk.StringVar(value="")
        tk.Label(self.root, textvariable=self.status_var, anchor="w",
                 relief="sunken").pack(fill="x", side="bottom")

        # ---- ショートカット
        r = self.root
        r.bind("<Control-z>", lambda e: self.undo())
        r.bind("<Control-s>", lambda e: self.save())
        r.bind("<Control-o>", lambda e: self.open())
        for k, t in (("p", "pen"), ("e", "eraser"), ("i", "picker"), ("a", "assign")):
            r.bind(k, lambda e, t=t: self.tool.set(t))
        for k in "0123":
            r.bind(k, lambda e, s=int(k): self.select_slot(self.cur_pal, s))
        r.bind("g", lambda e: self._toggle_grid())

    def _build_menu(self):
        m = tk.Menu(self.root)
        fm = tk.Menu(m, tearoff=0)
        fm.add_command(label="新規", command=self.new)
        fm.add_command(label="開く... (Ctrl+O)", command=self.open)
        fm.add_command(label="保存 (Ctrl+S)", command=self.save)
        fm.add_command(label="名前を付けて保存...", command=self.save_as)
        fm.add_separator()
        fm.add_command(label="ファミコン用データ書き出し (CHR/属性/パレット)...",
                       command=self.export_nes)
        fm.add_separator()
        fm.add_command(label="表示用パレット(.pal)読み込み...", command=self.load_pal_file)
        fm.add_command(label="表示用パレットを common/palette/nes.pal に保存",
                       command=self.save_common_pal)
        fm.add_separator()
        fm.add_command(label="ブリッジフォルダ変更...", command=self.change_bridge)
        fm.add_separator()
        fm.add_command(label="終了", command=self.root.destroy)
        m.add_cascade(label="ファイル", menu=fm)
        em = tk.Menu(m, tearoff=0)
        em.add_command(label="元に戻す (Ctrl+Z)", command=self.undo)
        em.add_command(label="全消去", command=self.clear_all)
        m.add_cascade(label="編集", menu=em)
        self.root.config(menu=m)

    # ------------------------------------------------------------------ 描画
    def _row_data(self, y: int, x0: int, x1: int) -> str:
        z = self.zoom
        cols = []
        for x in range(x0, x1):
            cols.extend([self.hex_of(self.pic.color_index(x, y))] * z)
        return "{" + " ".join(cols) + "}"

    def redraw_all(self):
        # 1倍で作ってから zoom で拡大（Tk 内部処理なので速い）
        small = tk.PhotoImage(width=W, height=H)
        rows = []
        for y in range(H):
            rows.append("{" + " ".join(self.hex_of(self.pic.color_index(x, y))
                                       for x in range(W)) + "}")
        small.put(" ".join(rows))
        self.img = small.zoom(self.zoom)
        self.canvas.itemconfig(self.img_id, image=self.img)
        self.draw_grid()
        self.refresh_palette_ui()

    def redraw_block(self, b: int):
        bx, by = b % BLOCKS_X, b // BLOCKS_X
        x0, y0 = bx * 16, by * 16
        x1, y1 = min(x0 + 16, W), min(y0 + 16, H)
        z = self.zoom
        rows = []
        for y in range(y0, y1):
            rows.extend([self._row_data(y, x0, x1)] * z)
        self.img.put(" ".join(rows), to=(x0 * z, y0 * z))

    def redraw_pixel(self, x: int, y: int):
        z = self.zoom
        self.img.put(self.hex_of(self.pic.color_index(x, y)),
                     to=(x * z, y * z, x * z + z, y * z + z))

    def draw_grid(self):
        self.canvas.delete("grid")
        z = self.zoom
        if self.show_tile_grid.get():
            for x in range(0, W + 1, 8):
                self.canvas.create_line(x * z, 0, x * z, H * z, fill="#606060", tags="grid")
            for y in range(0, H + 1, 8):
                self.canvas.create_line(0, y * z, W * z, y * z, fill="#606060", tags="grid")
        if self.show_attr_grid.get():
            for x in range(0, W + 1, 16):
                self.canvas.create_line(x * z, 0, x * z, H * z, fill="#E08000", tags="grid")
            for y in range(0, H + 1, 16):
                self.canvas.create_line(0, y * z, W * z, y * z, fill="#E08000", tags="grid")

    def _toggle_grid(self):
        v = not (self.show_tile_grid.get() or self.show_attr_grid.get())
        self.show_tile_grid.set(v)
        self.show_attr_grid.set(v)
        self.draw_grid()

    def refresh_palette_ui(self):
        for p in range(4):
            for s in range(4):
                c = self.swatches[p][s]
                c.delete("all")
                ci = self.pic.slot_color(p, s)
                c.create_rectangle(0, 0, 34, 26, fill=self.hex_of(ci), outline="")
                if (p, s) == (self.cur_pal, self.cur_slot):
                    c.create_rectangle(1, 1, 33, 25, outline="#FF2020", width=3)
                c.create_text(17, 13, text=f"{ci:02X}", font=("Consolas", 8),
                              fill=self._text_color(ci))
        cur = self.pic.slot_color(self.cur_pal, self.cur_slot)
        for i, c in enumerate(self.nes_cells):
            c.delete("all")
            c.create_rectangle(0, 0, 22, 22, fill=self.hex_of(i), outline="")
            if i in nes_palette.DISCOURAGED:
                c.create_line(0, 0, 22, 22, fill="red")
            c.configure(highlightbackground="#FF2020" if i == cur else "#444")

    def _text_color(self, ci: int) -> str:
        r, g, b = self.rgb[ci]
        return "black" if (r * 3 + g * 6 + b) > 1200 else "white"

    def _color_info(self, i: int) -> str:
        r, g, b = self.rgb[i]
        note = "  ※$0D は使用非推奨（テレビによって同期が乱れる）" if i in nes_palette.DISCOURAGED else ""
        return f"色 ${i:02X}  RGB({r},{g},{b}){note}"

    # ------------------------------------------------------------------ 入力
    def _xy(self, e):
        x, y = e.x // self.zoom, e.y // self.zoom
        if 0 <= x < W and 0 <= y < H:
            return x, y
        return None

    def push_undo(self):
        self.undo_stack.append(self.pic.snapshot())
        if len(self.undo_stack) > UNDO_MAX:
            self.undo_stack.pop(0)

    def on_press(self, e):
        xy = self._xy(e)
        if xy is None:
            return
        if self.tool.get() == "picker":
            self.pick(*xy)
            return
        self.push_undo()
        self.last_xy = xy
        self.apply_tool(*xy)

    def on_drag(self, e):
        xy = self._xy(e)
        self.on_motion(e)
        if xy is None or self.last_xy is None or self.tool.get() == "picker":
            return
        for p in self._line(self.last_xy, xy):
            self.apply_tool(*p)
        self.last_xy = xy

    def on_release(self, e):
        self.last_xy = None

    def on_right(self, e):
        xy = self._xy(e)
        if xy:
            self.pick(*xy)

    def on_motion(self, e):
        xy = self._xy(e)
        if xy is None:
            return
        x, y = xy
        b = block_of(x, y)
        self.status_var.set(
            f"x={x:3d} y={y:3d}  タイル#{(y // 8) * 16 + x // 8:3d}  "
            f"ブロック({x // 16},{y // 16}) パレットP{self.pic.block_pal[b]}  "
            f"色${self.pic.color_index(x, y):02X}")

    @staticmethod
    def _line(a, b):
        """ブレゼンハムの直線（ドラッグが速くても途切れないように）"""
        (x0, y0), (x1, y1) = a, b
        dx, dy = abs(x1 - x0), -abs(y1 - y0)
        sx, sy = (1 if x0 < x1 else -1), (1 if y0 < y1 else -1)
        err = dx + dy
        while True:
            yield x0, y0
            if x0 == x1 and y0 == y1:
                return
            e2 = 2 * err
            if e2 >= dy:
                err += dy
                x0 += sx
            if e2 <= dx:
                err += dx
                y0 += sy

    def apply_tool(self, x: int, y: int):
        t = self.tool.get()
        pic = self.pic
        b = block_of(x, y)
        i = y * W + x
        changed = False
        if t == "pen":
            if self.cur_slot != 0 and pic.block_pal[b] != self.cur_pal:
                # ファミコンの制約: 16x16 ブロックは1つのパレットしか使えない
                pic.block_pal[b] = self.cur_pal
                pic.pixels[i] = self.cur_slot
                self.redraw_block(b)
                changed = True
            elif pic.pixels[i] != self.cur_slot:
                pic.pixels[i] = self.cur_slot
                self.redraw_pixel(x, y)
                changed = True
        elif t == "eraser":
            if pic.pixels[i] != 0:
                pic.pixels[i] = 0
                self.redraw_pixel(x, y)
                changed = True
        elif t == "assign":
            if pic.block_pal[b] != self.cur_pal:
                pic.block_pal[b] = self.cur_pal
                self.redraw_block(b)
                changed = True
        if changed:
            self.schedule_send()

    def pick(self, x: int, y: int):
        self.cur_pal = self.pic.block_pal[block_of(x, y)]
        self.cur_slot = self.pic.pixels[y * W + x]
        if self.tool.get() == "picker":
            self.tool.set("pen")
        self.refresh_palette_ui()

    def select_slot(self, p: int, s: int):
        self.cur_pal, self.cur_slot = p, s
        self.refresh_palette_ui()

    def assign_color(self, ci: int):
        if self.pic.slot_color(self.cur_pal, self.cur_slot) == ci:
            return
        self.push_undo()
        self.pic.set_slot_color(self.cur_pal, self.cur_slot, ci)
        self.redraw_all()
        self.schedule_send()

    def undo(self):
        if not self.undo_stack:
            return
        self.pic.restore(self.undo_stack.pop())
        self.redraw_all()
        self.schedule_send()

    def clear_all(self):
        self.push_undo()
        self.pic.clear()
        self.redraw_all()
        self.schedule_send()

    # ------------------------------------------------------------------ 送信
    def schedule_send(self):
        if not self.auto_send.get():
            return
        if self.send_job is not None:
            self.root.after_cancel(self.send_job)
        self.send_job = self.root.after(SEND_DELAY_MS, self.send_now)

    def send_now(self):
        self.send_job = None
        self.seq += 1
        data = build_state(self.seq, self.pic.payload())
        ok = write_state_atomic(self.bridge_dir / "state.bin", data)
        if not ok:
            # Lua が読んでいる最中だった → 少し後にもう一度
            self.seq -= 1
            self.send_job = self.root.after(SEND_DELAY_MS, self.send_now)

    def poll_pad(self):
        path = self.bridge_dir / "pad.txt"
        try:
            st = path.stat()
            if st.st_mtime != self.pad_mtime:
                self.pad_mtime = st.st_mtime
                text = path.read_text(encoding="ascii", errors="replace").strip()
                parts = text.split(maxsplit=1)
                value = int(parts[0]) if parts else 0
                names = parts[1] if len(parts) > 1 else "-"
                self.pad_var.set(f"${value:02X}  {names}")
        except (OSError, ValueError):
            pass
        self.root.after(PAD_POLL_MS, self.poll_pad)

    # ------------------------------------------------------------------ ファイル
    def new(self):
        if messagebox.askyesno("新規", "現在の絵を破棄して新規作成しますか？"):
            self.push_undo()
            self.pic = Picture()
            self.file_path = None
            self.redraw_all()
            self.schedule_send()

    def open(self):
        path = filedialog.askopenfilename(
            filetypes=[("NES Paint", "*.npaint.json *.json"), ("すべて", "*.*")])
        if not path:
            return
        try:
            pic = Picture.load(path)
        except (OSError, ValueError, KeyError) as ex:
            messagebox.showerror("開く", f"読み込めませんでした:\n{ex}")
            return
        self.push_undo()
        self.pic = pic
        self.file_path = Path(path)
        self.redraw_all()
        self.schedule_send()

    def save(self):
        if self.file_path is None:
            self.save_as()
        else:
            self.pic.save(self.file_path)
            self.status_var.set(f"保存しました: {self.file_path}")

    def save_as(self):
        path = filedialog.asksaveasfilename(
            defaultextension=".npaint.json", filetypes=[("NES Paint", "*.npaint.json")])
        if path:
            self.file_path = Path(path)
            self.save()

    def export_nes(self):
        path = filedialog.asksaveasfilename(
            title="書き出し先（_chr.bin / _attr.bin / _pal.bin が作られます）",
            filetypes=[("ベース名", "*")])
        if path:
            files = self.pic.export_nes_data(path)
            messagebox.showinfo("書き出し", "\n".join(str(f) for f in files))

    def load_pal_file(self):
        path = filedialog.askopenfilename(filetypes=[("パレット", "*.pal"), ("すべて", "*.*")])
        if not path:
            return
        try:
            self.rgb = nes_palette.load_pal(path)
        except (OSError, ValueError) as ex:
            messagebox.showerror("パレット", str(ex))
            return
        self.redraw_all()

    def save_common_pal(self):
        nes_palette.save_pal(COMMON_PAL, self.rgb)
        self.status_var.set(f"保存しました: {COMMON_PAL}")

    def change_bridge(self):
        d = filedialog.askdirectory(initialdir=str(self.bridge_dir))
        if d:
            self.bridge_dir = Path(d)
            self.bridge_var.set(d)
            self.send_now()


def main():
    ap = argparse.ArgumentParser(description="ファミコン制約付きお絵描きツール（第1フェーズ）")
    ap.add_argument("--bridge", default=str(DEFAULT_BRIDGE),
                    help=f"state.bin / pad.txt を置くフォルダ（既定: {DEFAULT_BRIDGE}）")
    ap.add_argument("--zoom", type=int, default=4, help="表示倍率（既定: 4）")
    args = ap.parse_args()

    root = tk.Tk()
    NesPaintApp(root, Path(args.bridge), max(1, args.zoom))
    root.mainloop()


if __name__ == "__main__":
    main()
