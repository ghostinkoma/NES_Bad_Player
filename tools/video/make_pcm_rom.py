"""$4011 PCM で音を鳴らす動画 ROM を作る（配布用サンプル: オープニング・メニュー・白黒の動画）。

プレイヤー: nes/src/pcm_video.s。CPU が 96 サイクルごとに 1 回 $4011 へ書く（18,643Hz）。
再生中のすべての処理を 96 サイクルの「枠」に分け、1 枠で音 1 つ + 決まった量の仕事をする。

1 フレームの流れ（プレイヤー）
  描画中の枠 … 次のレコードを ROM から RAM（$0100-）へ 1 バイトずつ写す → VBlank 中の音を先読み → スプライト 0 ヒットを待つ
  ヒット（239 ライン）→ 待ち 1 枠 → VBlank 中の書き込み（count 枠）→ 仕上げ 1 枠 → 後処理 9 枠

VBlank 中の書き込み枠（v2）: 1 枠 = 音 1 つ + PPU への書き込み 10 回 + rts（次の枠の処理へ飛ぶ）
  10 回を「アドレス 2 回 + データ k 回」の組に分ける。組の大きさの並びで 5 種類:
  T10 = [10] / T37 = [3, 7] / T46 = [4, 6] / T55 = [5, 5] / T334 = [3, 3, 4]
  白黒のタイル 1 枚（8 バイト）は T10 1 枠。ネームテーブルの離れた 3 マスは T334 1 枠

レコード（1 フレーム = 1/60 秒に 1 つ）
  count, flags, g0, g1, g2, 本体を逆順に
    本体 = { 枠の処理の場所-1（2 バイト）, データ 10 バイト } × count, 仕上げの場所-1（2 バイト）
    flags: bit0 = このレコードで 1 コマが終わる / bit1 = 動画の終わり / bit2 = 次のレコードは次のバンクの $8001
    g    : このレコードを書く時点の音声の位置（サンプル番号。音声の先頭に無音 G_SHIFT 個を足した番号）

バンク（32KB）: $8000 = K（音声のページ番号 → CPU のページ: (g >> 8) + K）、$8001- レコード、
                後ろの方（ページ単位）に音声、$F400-$FFFF プレイヤー（全バンク共通 3KB）
バンク 0: $8010 + 16*番号 に目録、$8100- スプライト文字、$8400- スナップショット表

画面のしかけ
  タイル 255 = plane1 が $FF（色番号 2/3）。白黒では plane0 に右下のマスの本当の絵を書く（パレットの 2/3 = 0/1 と同じ色なので
  ふつうのマスと同じに見える）。4 色（オープニング）は plane0 = 0（色番号 2 = 黒）。右下のマス（959）に固定し、スプライト 0 をその上の
  背景の後ろ側（239 ライン）に置く → 毎フレーム必ず同じ所でヒットする（右下 8x8 が黒いだけ。ふつうのテレビでは見えない所）
  白黒: BG パレット 0 = 0F 30 0F 30（白黒反転のときは 30 0F 30 0F）
  4 色（オープニング）: 黒が色番号 2 になるように並べ替える
"""
import argparse
import hashlib
import json
import os
import pickle
import sys
import time

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import make_video_rom as m  # noqa: E402
import make_pcm_proto as pp  # noqa: E402

CYC = 96
HZ = m.CPU_HZ / CYC                 # 18,643.5Hz
SPF = HZ / m.NES_FPS                # 1 フレームのサンプル数（約 310.2）
NV = 19                             # VBlank 中の書き込み枠の最大（$0100 のページに入る数）
CODE_SIZE = 0x0C00                  # プレイヤー（$F400-$FFFF）
DATA_SIZE = 0x8000 - CODE_SIZE
RES_TILE, RES_CELL = 255, 959
PAL_N = [0x0F, 0x30, 0x0F, 0x30]           # 色番号 2/3 も 0/1 と同じ色（右下のタイル 255 は plane1 が $FF でも本当の絵が出せる）
PAL_I = [0x30, 0x0F, 0x30, 0x0F]
F_FRAME_END, F_END, F_NEXT_BANK = 1, 2, 4
G_SHIFT = 2048                      # 音声の先頭に足す無音
AUDIO_LEAD = 334                    # 再生を始めるとき、音声はレコードの g より何サンプル前から（プレイヤーと合わせる）
M_BEFORE, M_AFTER = 400, 96         # バンクに入れる音声の余裕（サンプル）
CATALOG, FONT_AT, SNAP_TABLE = 0x10, 0x100, 0x800
MENU_ID = 4
ENC_VERSION = 3
SLOT_TYPES = {"T10": (10,), "T37": (3, 7), "T46": (4, 6), "T55": (5, 5), "T334": (3, 3, 4)}
MAX_RUN = 8


def pal16(inv):
    return bytes((PAL_I if inv else PAL_N) + [0x0F] * 12)


def reserve(st):
    st.pt0[RES_TILE] = np.uint64(0)
    st.pt1[RES_TILE] = m.ALL_ONES
    st.nt[RES_CELL] = RES_TILE
    st.ref[0] -= 1
    st.ref[RES_TILE] = 1
    st.lookup[m.tkey(np.uint64(0), m.ALL_ONES)] = RES_TILE


def set_costs():
    """plan_packet の費用を「枠」の単位にする（1 枠 = 30、書き込み 1 回 = 3）"""
    m.UPLOAD_CYCLES = m.UPLOAD_CYCLES2 = 30
    m.UPLOAD_BUF = m.UPLOAD_BUF2 = 12
    m.BYTE_CYCLES = 3
    m.RUN_CYCLES = 6
    m.UPLOAD_FIXED_CYCLES = 0
    m.END_CYCLES = 0


# ---------------------------------------------------------------- 枠に詰める

def pack_items(items):
    """items: [(大きさ, 中身)] → [(種類, [(部分の大きさ, 中身), ...])]。大きい物から、すき間を小さい物で埋める"""
    rest = sorted(items, key=lambda it: -it[0])
    out = []

    def take(limit):
        for i, it in enumerate(rest):
            if it[0] <= limit:
                return rest.pop(i)
        return None

    while rest:
        a = rest.pop(0)
        s = a[0]
        if s >= 8:
            out.append(("T10", [(10, a)]))
        elif s == 7:
            b = take(3)
            out.append(("T37", [(3, b), (7, a)]) if b else ("T10", [(10, a)]))
        elif s == 6:
            b = take(4)
            out.append(("T46", [(4, b), (6, a)]) if b else ("T10", [(10, a)]))
        elif s == 5:
            b = take(5)
            out.append(("T55", [(5, a), (5, b)]) if b else ("T10", [(10, a)]))
        else:
            b = take(3) if s == 4 else take(4)
            if b is None:
                out.append(("T10", [(10, a)]))
                continue
            c = take(3)
            if c is not None:
                ps = sorted([a, b, c], key=lambda it: it[0])
                out.append(("T334", [(3, ps[0]), (3, ps[1]), (4, ps[2])]))
            else:
                big, small = (a, b) if a[0] >= b[0] else (b, a)
                out.append(("T46", [(4, small), (6, big)]))
    return out


def make_items(st_new, uploads, runs, pal_item, two):
    items = []
    for sl, a, b in uploads:
        items.append((10, ("t", sl * 16, int(a).to_bytes(8, "big"))))
        if two:
            items.append((10, ("t", sl * 16 + 8, int(b).to_bytes(8, "big"))))
    if pal_item:
        items.append((6, ("p", 0x3F00, bytes(st_new.pal[:4]))))
    for addr, data in runs:
        for k in range(0, len(data), MAX_RUN):
            n = min(MAX_RUN, len(data) - k)
            items.append((2 + n, ("n", addr + k, None)))
    return items


def part_bytes(st_new, size, item):
    """部分（大きさ size）の書き込み 10 回のうちの分: アドレス 2 + データ size-2（足りない分は今の値で埋める）"""
    kind, addr, data = item[1]
    k = size - 2
    if kind == "t":
        assert len(data) == k
        body = data
    elif kind == "p":
        body = bytes(st_new.pal[:k])
    else:
        c = addr - 0x2000
        body = bytes(int(st_new.nt[c + i]) if c + i < 1024 else 0 for i in range(k))
    return bytes([addr >> 8, addr & 0xFF]) + body


# ---------------------------------------------------------------- エンコード

def remap_black_to_2(fr, cols):
    """4 色の動画: 黒（0F）が色番号 2 になるように並べ替える → 新しい色の並び"""
    bi = cols.index(0x0F) if 0x0F in cols else 0
    perm = list(range(4))
    perm[bi], perm[2] = 2, bi                 # 古い番号 → 新しい番号
    new_cols = [0] * 4
    for old, new in enumerate(perm):
        new_cols[new] = cols[old]
    t0, t1 = [], []
    for p0, p1 in zip(fr.t0, fr.t1):
        masks = [~p1 & ~p0, ~p1 & p0, p1 & ~p0, p1 & p0]
        n0 = np.zeros_like(p0)
        n1 = np.zeros_like(p1)
        for old in range(4):
            if perm[old] & 1:
                n0 |= masks[old]
            if perm[old] & 2:
                n1 |= masks[old]
        t0.append(n0)
        t1.append(n1)
    fr.t0, fr.t1 = t0, t1
    return new_cols


def encode_stream(fr, mode, snap_every, kbps=None, cols=None, log_every=2000, init=None):
    """→ dict(recs=[(count, flags, [(種類, [(大きさ, item)])], st_after)], snaps, frame_err, npk, pal0)
    recs の各要素は書き込みの中身（バイト列）まで決めたもの: (count, flags, slots) slots = [(種類, bytes(10))]
    init: 始めの PPU の中身（白黒のみ。省くと空っぽから）"""
    set_costs()
    two = mode == "pal4"
    nframes, fps = len(fr), fr.fps
    npk = int(np.ceil(nframes * m.NES_FPS / fps))
    if two:
        pal_real = bytes(cols + [0x0F] * 12)
        st = m.PPUState(bytes([0x0F, cols[1], 0x0F, cols[3]] + [0x0F] * 12))   # 最初のレコードで本当の色にする
        reserve(st)
    elif init is not None:
        st = init.copy()
    else:
        st = m.PPUState(pal16(False))
        reserve(st)
    pal0 = bytes(st.pal)
    rate = kbps * 1024 / m.NES_FPS if kbps else None
    bucket = rate * 60 if rate else 0
    recs, snaps = [], []
    frame_err = np.zeros(nframes, dtype=np.int64)
    done_frames = 0
    t0 = time.time()
    zeros = np.zeros(960, dtype=np.uint64)
    for p in range(npk):
        if not two and (p == 0 or (p - snaps[-1][0] >= snap_every and recs[-1][1] & F_FRAME_END)):
            snaps.append((p, done_frames, snap_bytes(st)))
        f = min(int(p * fps / m.NES_FPS), nframes - 1)
        f_next = int((p + 1) * fps / m.NES_FPS)
        g0 = fr.t0[f].copy()
        g1 = fr.t1[f].copy() if two else zeros.copy()
        pal_change = False
        if two:
            if p == 0:
                st.pal = pal_real
                pal_change = True
        else:
            inv = st.pal[0] == 0x30
            mk = m.ALL_ONES if inv else np.uint64(0)
            g0[RES_CELL] = st.pt0[RES_TILE] ^ mk
            g1[RES_CELL] = st.pt1[RES_TILE]
            nt = st.nt[:960]
            s0, s1 = st.pt0[nt] ^ mk, st.pt1[nt]
            e_now = int(m.tile_err(s0, s1, g0, g1).sum())
            if e_now > 3000 and int(m.tile_err(~s0, s1, g0, g1).sum()) < e_now * 0.6:
                inv = not inv
                pal_change = True
                st.pal = pal16(inv)
            if inv:
                g0 = g0 ^ m.ALL_ONES
        res_item = None
        if two:
            g0[RES_CELL] = np.uint64(0)
        else:
            # 右下のタイル 255: plane1 = $FF のまま、plane0 に本当の絵を書く（色番号 2/3 = 0/1 と同じ色のパレット）
            want = (fr.t0[f][RES_CELL] ^ m.ALL_ONES) if inv else fr.t0[f][RES_CELL]
            if want != st.pt0[RES_TILE]:
                st.lookup.pop(m.tkey(st.pt0[RES_TILE], m.ALL_ONES), None)
                st.pt0[RES_TILE] = want
                st.lookup[m.tkey(want, m.ALL_ONES)] = RES_TILE
                res_item = (10, ("t", RES_TILE * 16, int(want).to_bytes(8, "big")))
            g0[RES_CELL] = st.pt0[RES_TILE]
        g1[RES_CELL] = m.ALL_ONES
        lim = NV if rate is None else max(1, min(NV, int((bucket - 7) // 12)))
        budget = 30 * lim - (12 if pal_change else 0) - (30 if res_item else 0)
        while True:
            trial = st.copy()
            writes, uploads = m.plan_packet(trial, g0, g1, p, max(budget, 0), 10 ** 9, 10 ** 9, 0, two, {})
            runs = m.build_runs(st, writes)
            packed = pack_items(make_items(trial, uploads, runs, pal_change, two) + ([res_item] if res_item else []))
            if len(packed) <= lim or budget <= 0:
                break
            budget -= 6
        assert len(packed) <= NV, f"レコード {p} の枠が多すぎます"
        st = trial
        slots = [(ty, b"".join(part_bytes(st, size, it) for size, it in parts)) for ty, parts in packed]
        is_end = (f_next != f) or p == npk - 1
        recs.append((len(slots), F_FRAME_END if is_end else 0, slots))
        if rate is not None:
            bucket = min(bucket - (7 + 12 * len(slots)) + rate, rate * 120)
        if is_end:
            done_frames += 1
            nt = st.nt[:960]
            frame_err[f] = int(m.popcount((st.pt0[nt] ^ g0) | (st.pt1[nt] ^ g1)).sum())
        if log_every and p % log_every == 0 and p:
            print(f"    エンコード {p}/{npk}  ({time.time() - t0:.0f} 秒)", flush=True)
    return dict(recs=recs, snaps=snaps, frame_err=frame_err, nframes=nframes, fps=fps, npk=npk, pal0=pal0, mode=mode,
                st_end=st)


def ppu_arrays(st):
    """PPUState → (ネームテーブル 1024, パターン 4096, パレット 16) のバイト列"""
    chr_ = bytearray(4096)
    for i in range(256):
        chr_[i * 16:i * 16 + 8] = int(st.pt0[i]).to_bytes(8, "big")
        chr_[i * 16 + 8:i * 16 + 16] = int(st.pt1[i]).to_bytes(8, "big")
    return bytes(st.nt.astype(np.uint8)), bytes(chr_), bytes(st.pal)


def encode_loop(fr, mode, snap_every, kbps):
    """つなぎ目で画面を消さずに繰り返せる動画（メニューの背景）。
    1 回目: 空っぽから最後まで → その終わりの中身 S を「始めの中身」にして 2 回目を作る（ROM に入るのは 2 回目）。
    2 回目の終わりの中身を S にそろえる書き込み（つなぎのレコード）を最後に足す → 最後から先頭へそのまま戻れる。
    先頭から再生するときは、S をスナップショット 0 番として PPU に書いてから始める"""
    print("    つなぎ目なしで繰り返す: 1 回目（始めの中身を決める）", flush=True)
    s = encode_stream(fr, mode, snap_every, kbps)["st_end"]
    init = s.copy()
    init.last = init.last - (int(init.last.max()) + 1)     # 追い出しの順は保ったまま、パケット番号を 0 より前に
    print("    つなぎ目なしで繰り返す: 2 回目", flush=True)
    enc = encode_stream(fr, mode, snap_every, kbps, init=init)
    enc["loop_s"] = s
    enc["init"] = ppu_arrays(s)
    enc["loop"] = True
    return enc


class _NtPal:
    def __init__(self, nt, pal):
        self.nt, self.pal = nt, pal


def add_loop_fixup(enc):
    """つなぎのレコードを足す: 2 回目の終わりの中身 E を始めの中身 S にそろえる。
    E と S は同じ絵を出しているので、次の 2 つだけを繰り返せば、途中のどのフレームでも見える絵は変わらない
      ・いま画面に使われていないタイルを S の絵に書き換える（見えない）
      ・マスを S のタイル番号に書き換える。ただし、そのタイルがもう S の絵になっているときだけ（同じ絵のまま）
    1 回分（ラウンド）の書き込みはどの順・どこで区切っても絵が変わらないので、ラウンドの順だけ守ってレコードに分ける"""
    s, e = enc["loop_s"], enc["st_end"]
    assert np.array_equal(e.pt1, s.pt1), "白黒なのに plane1 が変わっている"
    nt = e.nt.copy()
    pt = e.pt0.copy()
    slots_all, rounds, forced = [], 0, 0
    while True:
        tiles_left = [i for i in range(256) if pt[i] != s.pt0[i]]
        cells_left = [c for c in range(960) if nt[c] != s.nt[c]]
        if not tiles_left and not cells_left:
            break
        shown = set(int(x) for x in nt[:960])
        tiles = [i for i in tiles_left if i not in shown]
        cells = [c for c in cells_left if pt[s.nt[c]] == s.pt0[s.nt[c]]]
        if not tiles and not cells:         # 堂々めぐり（めったにない）: 残りを全部書く（一瞬だけ絵が崩れる）
            tiles, cells = tiles_left, cells_left
            forced += 1
        rounds += 1
        items = [(10, ("t", i * 16, int(s.pt0[i]).to_bytes(8, "big"))) for i in tiles]
        for i in tiles:
            pt[i] = s.pt0[i]
        for c in cells:
            nt[c] = s.nt[c]
        k = 0
        while k < len(cells):
            c0, n = cells[k], 1
            while k + n < len(cells) and n < MAX_RUN and cells[k + n] == c0 + n:
                n += 1
            items.append((2 + n, ("n", 0x2000 + c0, None)))
            k += n
        view = _NtPal(nt.copy(), e.pal)     # 埋め草は、このラウンドの後の値（変わらないマスはそのまま）
        slots_all += [(ty, b"".join(part_bytes(view, size, it) for size, it in parts)) for ty, parts in pack_items(items)]
    if e.pal[:2] != s.pal[:2]:
        slots_all += [(ty, b"".join(part_bytes(_NtPal(s.nt, s.pal), size, it) for size, it in parts))
                      for ty, parts in pack_items([(6, ("p", 0x3F00, None))])]
        forced += 1
    nfix = 0
    for j in range(0, len(slots_all), NV):
        part = slots_all[j:j + NV]
        enc["recs"].append((len(part), F_FRAME_END, part))
        nfix += 1
    print(f"    つなぎのレコード {nfix} 個（{rounds} ラウンド、絵が崩れるラウンド {forced}）", flush=True)
    enc["npk"] = len(enc["recs"])
    enc["nfix"] = nfix
    enc["fix_forced"] = forced
    return enc

def snap_bytes(st):
    """コマ戻し用（白黒）: ネームテーブル 960 + 絵（plane0）256x8 + 白黒反転 1"""
    return (bytes(st.nt[:960].astype(np.uint8)) + b"".join(int(v).to_bytes(8, "big") for v in st.pt0)
            + bytes([1 if st.pal[0] == 0x30 else 0]))


def font_8x16():
    """スプライト文字（8x16 スプライト用）: 文字 c はタイル 2c（上）と 2c+1（下）。スプライトのタイル番号は 2c+1（$1000 側）。
    5x7 の文字を 1〜7 行目に置き、まわり（0〜8 行目）を黒ふちで囲む（8x8 では上のふちが入らず、上 1 ドットが欠けて見えた）"""
    import gen_font
    out = bytearray()
    for ch in gen_font.ORDER + " ":
        t = [[0] * 8 for _ in range(16)]
        if ch != " ":
            for y, row in enumerate(gen_font.G[ch].split()):
                for x, v in enumerate(row):
                    if v == "1":
                        t[y + 1][x + 1] = 1
        o = [r[:] for r in t]
        for y in range(16):
            for x in range(8):
                if not t[y][x] and any(0 <= y + dy < 16 and 0 <= x + dx < 8 and t[y + dy][x + dx]
                                       for dy in (-1, 0, 1) for dx in (-1, 0, 1)):
                    o[y][x] = 3
        for half in (0, 8):
            p0 = [sum(((o[half + y][x] & 1) << (7 - x)) for x in range(8)) for y in range(8)]
            p1 = [sum(((o[half + y][x] >> 1) << (7 - x)) for x in range(8)) for y in range(8)]
            out += bytes(p0 + p1)
    return bytes(out)


def g_of(p):
    return int(round(p * SPF)) + G_SHIFT


def encode_audio_pcm(path, n_packets):
    """動画の音声 → 18.6kHz・7bit（ファミコンの混ぜ方の補正あり）。先頭に無音 G_SHIFT 個"""
    pp.CYCLES, pp.HZ = CYC, HZ
    silence = int(pp.to_pcm(np.zeros(1), True)[0])
    need = g_of(n_packets) + M_AFTER + 4096
    out = np.full(need, silence, np.uint8)
    x = pp.load_audio(path)
    a = pp.to_pcm(x, True)
    n = min(len(a), need - G_SHIFT)
    out[G_SHIFT:G_SHIFT + n] = a[:n]
    return out


def silence_value():
    pp.CYCLES, pp.HZ = CYC, HZ
    return int(pp.to_pcm(np.zeros(1), True)[0])


# ---------------------------------------------------------------- ROM の組み立て

def record_bytes(count, flags, g, slots, vec):
    seq = b"".join(bytes([(vec[ty] - 1) & 0xFF, (vec[ty] - 1) >> 8]) + data for ty, data in slots)
    seq += bytes([(vec["fin"] - 1) & 0xFF, (vec["fin"] - 1) >> 8])
    assert len(seq) == 12 * count + 2
    return bytes([count, flags, g & 0xFF, (g >> 8) & 0xFF, (g >> 16) & 0xFF]) + seq[::-1]


def layout_stream(recs, audio, first_bank, vec):
    """レコードと音声をバンクに詰める → (banks, 各レコードの (バンク, アドレス))"""
    n = len(recs)
    if audio is not None:
        npages = (len(audio) + 255) // 256
        apad = np.full(npages * 256, audio[-1], np.uint8)
        apad[:len(audio)] = audio
    banks, where = [], []
    r = 0
    while r < n:
        size = 1
        q = r
        first_pg = max(0, (g_of(r) - AUDIO_LEAD - M_BEFORE) // 256)
        while q < n:
            rl = 5 + 12 * recs[q][0] + 2
            ap = 0 if audio is None else (min(npages - 1, (g_of(q) + M_AFTER) // 256) - first_pg + 1) * 256
            if size + rl + ap > DATA_SIZE:
                break
            size += rl
            q += 1
        assert q > r, "1 つのレコードもバンクに入りません"
        data = bytearray(b"\xFF" * DATA_SIZE)
        top = 0x80 + DATA_SIZE // 256
        if audio is not None:
            last_pg = min(npages - 1, (g_of(q - 1) + M_AFTER) // 256)
            apage = top - (last_pg - first_pg + 1)
            data[0] = (apage - first_pg) & 0xFF
            data[(apage - 0x80) * 256:] = apad[first_pg * 256:(last_pg + 1) * 256].tobytes()
        else:
            apage = top
            data[0] = 0
        off = 1
        for i in range(r, q):
            c, fl, slots = recs[i]
            if i == q - 1:
                fl |= F_END if q == n else F_NEXT_BANK
            rb = record_bytes(c, fl, g_of(i), slots, vec)
            where.append((first_bank + len(banks), 0x8000 + off))
            data[off:off + len(rb)] = rb
            off += len(rb)
        assert off <= (apage - 0x80) * 256
        banks.append(data)
        r = q
    return banks, where


def walk(prg, first_bank, audio, vec, pal0, mode, init=None):
    """ROM のバイト列だけを見てプレイヤーと同じように読む → 各レコードを書いた後の PPU の中身のハッシュ、g。
    あわせて、各レコードの時点で鳴りうる音声がそのバンクに正しく入っているかを確かめる"""
    inv_vec = {v - 1: k for k, v in vec.items()}

    def rd(bank, addr):
        return prg[bank * 0x8000 + addr - 0x8000]
    nt = np.zeros(1024, np.int32)
    chr_ = bytearray(4096)
    pal = bytearray(pal0)
    nt[RES_CELL] = RES_TILE
    chr_[RES_TILE * 16 + 8:RES_TILE * 16 + 16] = b"\xFF" * 8
    if init is not None:                    # つなぎ目なしの動画: スナップショット 0 番の中身から
        nt[:] = np.frombuffer(init[0], np.uint8)
        chr_[:] = init[1]
        pal[:] = init[2]
    init_hash = m.state_hash(bytes(nt.astype(np.uint8)) + bytes(chr_) + bytes(pal))
    bank, addr = first_bank, 0x8001
    hashes, gs = [], []
    while True:
        k = rd(bank, 0x8000)
        c, fl = rd(bank, addr), rd(bank, addr + 1)
        g = rd(bank, addr + 2) | (rd(bank, addr + 3) << 8) | (rd(bank, addr + 4) << 16)
        seq = bytes(rd(bank, addr + 5 + i) for i in range(12 * c + 2))[::-1]
        for j in range(c + 1):
            v = seq[12 * j] | (seq[12 * j + 1] << 8)
            ty = inv_vec[v]
            if j == c:
                assert ty == "fin"
                break
            data = seq[12 * j + 2:12 * j + 12]
            pos = 0
            for size in SLOT_TYPES[ty]:
                a = (data[pos] << 8) | data[pos + 1]
                for i in range(size - 2):
                    x, val = a + i, data[pos + 2 + i]
                    if x < 0x1000:
                        chr_[x] = val
                    elif 0x2000 <= x < 0x2400:
                        nt[x - 0x2000] = val
                    elif 0x3F00 <= x < 0x3F10:
                        pal[x - 0x3F00] = val
                pos += size
        if audio is not None:
            for s in (g - AUDIO_LEAD - 320, g, g + 40):
                pg = ((s >> 8) + k) & 0xFF
                assert 0x80 <= pg < 0x80 + DATA_SIZE // 256, f"音声がバンクにありません（g={g}）"
                assert rd(bank, pg * 256 + (s & 0xFF)) == audio[s], f"音声の位置が合いません（g={g}）"
        hashes.append(m.state_hash(bytes(nt.astype(np.uint8)) + bytes(chr_) + bytes(pal)))
        gs.append(g)
        addr += 5 + 12 * c + 2
        if fl & F_END:
            break
        if fl & F_NEXT_BANK:
            bank, addr = bank + 1, 0x8001
    if init is not None:
        assert hashes[-1] == init_hash, "つなぎ目なしの動画: 最後の中身が始めの中身と合いません"
    return hashes, gs


def load_title(t, cfg, cache_dir, seconds, kbps):
    t = dict(t)
    t.setdefault("threshold", cfg.get("threshold", 128))
    folder = os.path.join(m.ROOT, t["frames"])
    fps = m.title_fps(t, folder)
    max_frames = int(seconds * fps) if seconds else None
    snap_every = int(round(t.get("snap_seconds", cfg.get("snap_seconds", 3)) * m.NES_FPS))
    mode = t.get("mode", "mono")
    key = json.dumps([ENC_VERSION, NV, "loop2" if t.get("loop") else 0, m.file_digest(folder), {k: v for k, v in t.items() if k not in ("name", "audio", "audio_rate", "kbps")},
                      fps, snap_every, max_frames, kbps], sort_keys=True)
    cfile = os.path.join(cache_dir, "pcm2_" + hashlib.sha1(key.encode()).hexdigest()[:16] + ".pkl")
    if os.path.exists(cfile):
        print(f"  {t['name']}: 前回の変換結果を使います")
        with open(cfile, "rb") as fp:
            enc = pickle.load(fp)
        return add_loop_fixup(enc) if enc.get("loop") else enc
    fr = m.load_frames(t, folder, fps, cache_dir, max_frames)
    cols = None
    if mode == "pal4":
        cols = remap_black_to_2(fr, list(m.pal4_colors(t)))
    print(f"  {t['name']}: {len(fr)} コマを変換します（PCM 版 / {mode}）")
    if t.get("loop"):
        assert mode == "mono", "つなぎ目なしの繰り返し（loop）は白黒の動画だけ"
        enc = encode_loop(fr, mode, snap_every, kbps)
    else:
        enc = encode_stream(fr, mode, snap_every, kbps, cols)
    with open(cfile, "wb") as fp:
        pickle.dump(enc, fp)
    return add_loop_fixup(enc) if enc.get("loop") else enc


def main():
    ap = argparse.ArgumentParser(description="$4011 PCM 版の動画 ROM（配布用サンプル）")
    ap.add_argument("--config", default=os.path.join(m.ROOT, "tools", "video", "titles_pcm.json"))
    ap.add_argument("--code", required=True)
    ap.add_argument("--labels", required=True)
    ap.add_argument("-o", "--out", required=True)
    ap.add_argument("--seconds", type=float)
    ap.add_argument("--cache-dir", default=os.path.join(m.ROOT, "build", "video", "cache"))
    ap.add_argument("--mesen-test", type=int, default=0, help="Mesen の確認用 Lua を書き出す（フレーム数）")
    a = ap.parse_args()

    with open(a.config, encoding="utf-8-sig") as fp:
        cfg = json.load(fp)
    labels = m.read_labels(a.labels)
    vec = {ty: labels["slot_" + ty] for ty in SLOT_TYPES}
    vec["fin"] = labels["fin"]
    code = open(a.code, "rb").read()
    assert len(code) == CODE_SIZE, f"プレイヤーの大きさが {len(code)} バイト（{CODE_SIZE} のはず）"

    entries = [(i, t) for i, t in enumerate(cfg["titles"])] + [(MENU_ID, cfg["menu"])]
    bank0 = bytearray(b"\xFF" * DATA_SIZE)
    bank0[CATALOG:CATALOG + 16 * (MENU_ID + 1)] = bytes(16 * (MENU_ID + 1))   # 入っていない番号は 0（メニューに出さない）
    font = font_8x16()
    bank0[FONT_AT:FONT_AT + len(font)] = font
    banks = []
    info = {}
    alias = {}
    for sid, t in entries:
        if not t.get("enabled", True):
            continue
        if t.get("same_as") == "menu":     # メニューの背景と同じデータを、題名として選べるようにする
            alias[sid] = MENU_ID
            continue
        kbps = t.get("pcm_kbps", cfg.get("pcm_kbps"))
        enc = load_title(t, cfg, a.cache_dir, a.seconds, kbps)
        audio = None
        if t.get("audio", True) is not False:
            src = os.path.join(m.ROOT, t.get("audio", os.path.normpath(t["frames"]) + ".mp4"))
            audio = encode_audio_pcm(src, enc["npk"])
        first = 1 + len(banks)
        sb, where = layout_stream(enc["recs"], audio, first, vec)
        banks += sb
        info[sid] = dict(t=t, enc=enc, audio=audio, first=first, where=where, nb=len(sb))
        print(f"  [{sid}] {t['name']}: {len(sb)} バンク（ここまで {1 + len(banks)}）", flush=True)
    # スナップショット（白黒の動画のみ）: 後ろのバンクにまとめて置く
    snap_tab_off = SNAP_TABLE
    cur = None
    for sid, d in info.items():
        snaps = d["enc"]["snaps"]
        e = CATALOG + 16 * sid
        bank0[e:e + 8] = bytes([1, d["first"], 1 if d["audio"] is None else 0, len(snaps),
                                (0x8000 + snap_tab_off) & 0xFF, (0x8000 + snap_tab_off) >> 8,
                                1 if d["enc"]["mode"] == "pal4" else 0, 1 if d["enc"].get("loop") else 0])
        bank0[e + 8:e + 12] = d["enc"]["pal0"][:4]
        for a_sid, src in alias.items():
            if src == sid:
                bank0[CATALOG + 16 * a_sid:CATALOG + 16 * a_sid + 16] = bank0[e:e + 16]
        for p, done, blob in snaps:
            if cur is None or len(cur) + len(blob) > DATA_SIZE:
                cur = bytearray()
                banks.append(cur)
            sbank, saddr = len(banks), 0x8000 + len(cur)
            cur += blob
            rb, ra = d["where"][p]
            bank0[snap_tab_off:snap_tab_off + 8] = bytes([sbank, saddr & 0xFF, saddr >> 8, rb, ra & 0xFF, ra >> 8,
                                                          done & 0xFF, done >> 8])
            snap_tab_off += 8
    assert snap_tab_off <= DATA_SIZE
    prg = bytearray()
    for b in [bank0] + banks:
        prg += bytes(b) + b"\xFF" * (DATA_SIZE - len(b)) + code
    nb = len(prg) // 0x8000
    if nb > 256:
        sys.exit(f"ROM に入りません（{nb} バンク。上限 256）。titles_pcm.json の pcm_kbps（映像の量）を下げてください")
    units = len(prg) // 16384
    hdr = bytes([0x4E, 0x45, 0x53, 0x1A, units & 0xFF, 0, 0x21, 0x28, 0x20, (units >> 8) & 0x0F, 0, 0x07, 0, 0, 0, 0])
    with open(a.out, "wb") as fp:
        fp.write(hdr + prg)
    print(f"ROM: {a.out}  {len(hdr) + len(prg):,} バイト / {nb} バンク（上限 256）")
    expect = {}
    for sid, d in info.items():
        enc = d["enc"]
        hashes, gs = walk(prg, d["first"], d["audio"], vec, enc["pal0"], enc["mode"], enc.get("init"))
        expect[sid] = dict(hashes=hashes, gs=gs, audio=d["audio"])
        for a_sid, src in alias.items():
            if src == sid:
                expect[a_sid] = expect[sid]
        vsize = sum(7 + 12 * r[0] for r in enc["recs"])
        sec = enc["nframes"] / enc["fps"]
        fe = enc["frame_err"]
        print(f"  [{sid}] {d['t']['name']}: {enc['nframes']} コマ / {sec:.1f} 秒 / {d['nb']} バンク / 映像 {vsize / sec / 1024:.1f} KB/秒 / "
              f"元画像と違う点 平均 {100 * fe.mean() / 61440:.2f}% 最大 {100 * fe.max() / 61440:.1f}% / "
              f"{'音声 ' + format(HZ, '.0f') + 'Hz' if d['audio'] is not None else '音声なし'} / コマ戻し用 {len(enc['snaps'])}"
              + (f" / つなぎ目なしで繰り返す（つなぎ {enc['nfix']} レコード）" if enc.get("loop") else ""))
    base = os.path.splitext(a.out)[0]
    with open(base + "_expect.pkl", "wb") as fp:
        pickle.dump(expect, fp)
    print("ROM を読み直して、映像と音声の置き場を確認した")
    if a.mesen_test:
        import pcm_test
        pcm_test.write_lua(base + "_test.lua", a.mesen_test, labels)
        print(f"Mesen 確認用: {base}_test.lua")


if __name__ == "__main__":
    main()
