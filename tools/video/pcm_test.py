"""PCM 版 ROM の Mesen 自動確認（make_pcm_rom.py --mesen-test と build_pcm_video.ps1 -Test から使う）

Lua: 電源を入れる → オープニング → メニュー → A で ORIGEN → 一時停止・コマ送り・コマ戻し・再開 → FPS → START でメニュー → ↓↑
     → メニューの背景が 1 周（画面を消さずに先頭へ戻る）→ ↑ A で PUNINEKO
     毎フレーム（VBlank の書き込みが終わった後）に、PPU の中身のハッシュ・$4011 に書いた値・状態を出す
Python（check）:
  1. $4011 の書き込みの間隔が 96 サイクルか（ボタンで再生を抜けた所・動画の切り替えの前後は除く）
  2. 再生中の画面が、ROM のレコードを順に書いた中身と一致するか
  3. 再生中に書いた音の並びが、音声データの連続した一部と一致するか
"""
import os
import pickle
import sys
from collections import Counter

import numpy as np

# フレーム番号 → 押すボタン
SCRIPT = [(650, "a"), (1600, "b"), (1640, "right"), (1660, "right"), (1680, "left"), (1700, "left"), (1720, "left"),
          (1760, "b"), (2000, "select"), (2200, "start"), (2400, "down"), (2450, "up"),
          (3350, "up"), (3370, "a")]   # メニューの背景が 1 周して先頭に戻った後、PUNINEKO を選んで再生
FRAMES = 3500
PAUSE = (1600, 1765)               # B で止めて、B で再開するまで

LUA = r"""-- 自動生成: tools/video/pcm_test.py
local A = {stream=%(stream)d, state=%(state)d, dsave=%(dsave)d, prem=%(prem)d, vrem=%(vrem)d, bank=%(bank)d, ay=%(ay)d, py=%(py)d}
local KEYS = {%(keys)s}
local prev, n, bad = nil, 0, {}
local vals = {}
emu.addMemoryCallback(function(addr, value)
  local c = emu.getState()["cpu.cycleCount"]
  if prev and c - prev ~= 96 then bad[#bad + 1] = c - prev end
  prev = c
  n = n + 1
  vals[#vals + 1] = string.format("%%02x", value)
end, emu.callbackType.write, 0x4011)
local pending = nil
emu.addEventCallback(function()
  if pending then local t = {}; t[pending] = true; emu.setInput(t, 0) end
end, emu.eventType.inputPolled)
local function ppu_hash()
  local h = 0
  for i = 0, 1023 do h = (h * 31 + emu.read(i, emu.memType.nesNametableRam)) & 0xFFFFFFFF end
  for i = 0, 4095 do h = (h * 31 + emu.read(i, emu.memType.nesChrRam)) & 0xFFFFFFFF end
  for i = 0, 15 do h = (h * 31 + emu.read(i, emu.memType.nesPaletteRam)) & 0xFFFFFFFF end
  return h
end
local frame = 0
emu.addEventCallback(function()
  frame = frame + 1
  pending = KEYS[frame]
  local st = emu.read(A.stream, emu.memType.nesMemory)
  local sa = emu.read(A.state, emu.memType.nesMemory)
  print(string.format("F %%d %%d %%d %%d %%d %%d %%s D %%d %%d %%d %%d %%d %%d %%d", frame, n, ppu_hash(), st, sa, #bad, table.concat(vals), emu.read(A.dsave, emu.memType.nesMemory), emu.read(A.prem, emu.memType.nesMemory), emu.read(A.vrem, emu.memType.nesMemory), emu.read(0x01EA, emu.memType.nesMemory), emu.read(A.bank, emu.memType.nesMemory), emu.read(A.ay, emu.memType.nesMemory), emu.read(A.py, emu.memType.nesMemory)))
  if #bad > 0 then print("B " .. frame .. " " .. table.concat(bad, ",")) end
  vals = {}
  bad = {}
  if frame >= %(frames)d then emu.stop(0) end
end, emu.eventType.startFrame)
"""


def write_lua(path, frames, labels):
    keys = []
    for f, k in SCRIPT:
        for d in range(3):              # 3 フレーム押し続ける（再生中はパッドを 1 フレームに 1 回読む）
            keys.append(f'[{f + d}]="{k}"')
    with open(path, "w", encoding="utf-8", newline="\n") as fp:
        fp.write(LUA % dict(stream=labels["stream"], state=labels["state"], dsave=labels["dsave"], prem=labels["prem"], vrem=labels["vrem"], bank=labels["bank"], ay=labels["ay"], py=labels["py"], keys=",".join(keys),
                            frames=max(frames, FRAMES)))


def check(base, log):
    exp = pickle.load(open(base + "_expect.pkl", "rb"))
    rows, bads = [], {}
    for line in open(log, encoding="utf-8", errors="replace"):
        p = line.split()
        if line.startswith("F "):
            rows.append(dict(f=int(p[1]), n=int(p[2]), h=int(p[3]), st=int(p[4]), sa=int(p[5]),
                             v=bytes.fromhex(p[7]) if len(p) > 7 and p[7] != "D" else b""))
        elif line.startswith("B "):
            bads[int(p[1])] = [int(x) for x in p[2].split(",")]
    ok = True
    # ---- 1. 間隔: 96 でないものは、ボタンのフレーム・動画が変わったフレームの近く（±4）だけのはず
    events = {f + d for f, _ in SCRIPT for d in range(-1, 8)}
    events |= set(range(PAUSE[0], PAUSE[1] + 8))   # 一時停止中（音は止まり、サイクルを数えない処理になる）
    for i in range(1, len(rows)):
        if rows[i]["st"] != rows[i - 1]["st"] or rows[i]["sa"] != rows[i - 1]["sa"]:
            events |= {rows[i]["f"] + d for d in range(-4, 5)}
    # つなぎ目なしの繰り返しで先頭へ戻った所（音を止めて戻るので、ボタンと同じく間隔の確認から除く）
    firsts = {}
    for sid, e in exp.items():
        firsts[sid] = {}
        for i, h in enumerate(e["hashes"]):
            firsts[sid].setdefault(h, i)
    for i in range(1, len(rows)):
        a, b = rows[i - 1], rows[i]
        fa, fb = firsts.get(a["st"], {}), firsts.get(b["st"], {})
        if a["st"] == b["st"] and a["h"] in fa and b["h"] in fb and fb[b["h"]] < fa[a["h"]] - 100:
            events |= {b["f"] + d for d in range(-8, 9)}
    stray = {f: v for f, v in bads.items() if f not in events and f > 10}
    total = sum(len(v) for v in bads.values())
    print(f"1. 間隔: 96 サイクル以外 {total} 回（ボタン・切り替えの近く以外: {sum(len(v) for v in stray.values())} 回 "
          f"{list(stray.items())[:5]}）")
    ok &= not stray
    # ---- 2, 3. 動画ごとの区間（同じ stream が続く所）で、画面と音を確かめる
    segs = []
    for r in rows:
        if segs and segs[-1][0] == r["st"] and segs[-1][1] == r["sa"]:
            segs[-1][2].append(r)
        else:
            segs.append((r["st"], r["sa"], [r]))
    for st, sa, rs in segs:
        if st not in exp or len(rs) < 30:
            continue
        e = exp[st]
        hashes = e["hashes"]
        cnt = Counter(hashes)
        first = {}
        for i, h in enumerate(hashes):
            first.setdefault(h, i)
        # 画面: 区間の中で、ハッシュが 1 つのレコードにしか当てはまらないフレームを数え、レコード番号が増えていく順か
        idx = [first[r["h"]] if r["h"] in first else None for r in rs]
        known = [i for i in idx if i is not None]
        unknown = sum(1 for k, i in enumerate(idx) if k >= 5 and i is None and rs[k]["f"] not in events)
        mono = all(b >= a for a, b in zip(known, known[1:]) if a is not None and b is not None) if known else False
        name = {0: "OPENING", 1: "PUNINEKO", 2: "ORIGEN", 4: "MENU"}.get(st, str(st))
        # つなぎ目なしの繰り返し: レコード番号が大きく戻った所（先頭へ戻った所）。そこでは音も先頭に戻るので、音の区間を分ける
        loops = [rs[k]["f"] for k in range(10, len(rs)) if idx[k] is not None and idx[k - 1] is not None and idx[k] < idx[k - 1] - 100]
        loop_ev = {f + d for f in loops for d in range(-8, 9)}
        print(f"2. 画面 [{name}] フレーム {rs[0]['f']}〜{rs[-1]['f']}: レコード {known[0] if known else '-'}〜{known[-1] if known else '-'}"
              f"、一致しないフレーム {unknown}（最初の 5 フレームを除く）"
              + (f"、先頭に戻った所 {loops}（画面を消さずにつながった）" if loops else ""))
        ok &= unknown == 0
        # 音: 再生中に書いた値が、音声データの連続した一部か。ボタンの所（再生を抜けた所）で区切って調べる
        if e["audio"] is None or len(rs) < 40:
            continue
        audio = bytes(e["audio"])
        parts, cur = [], []
        for r in rs[10:]:
            if r["f"] in events or r["f"] in loop_ev:
                if cur:
                    parts.append(cur)
                cur = []
            else:
                cur.append(r)
        if cur:
            parts.append(cur)
        miss, lags = 0, []
        for part in parts:
            vals = b"".join(r["v"] for r in part)
            if len(vals) < 2000:
                continue
            base = None
            for k in range(0, len(vals) - 400, 499):     # 1 か所にしか見つからない、変化のある 400 個で位置を決める
                w = vals[k:k + 400]
                if len(set(w)) > 30:
                    pos = audio.find(w)
                    if pos >= 0 and audio.find(w, pos + 1) < 0:
                        base = pos - k
                        break
            if base is None or audio[base:base + len(vals)] != vals:
                miss += 1
                continue
            # 音と映像のずれ: そのフレームまでに書いた音の位置 - 画面に出ているレコードの g
            n = 0
            for r in part:
                n += len(r["v"])
                if r["h"] in first and cnt[r["h"]] == 1:
                    lags.append(base + n - e["gs"][first[r["h"]]])
        lg = np.array(lags) / (1789772.727 / 96) * 1000 if lags else np.zeros(1)
        print(f"3. 音 [{name}]: {len(parts)} 区間、音声データと一致しない区間 {miss}"
              f"（音 - 映像 平均 {lg.mean():+.1f} ms、最小 {lg.min():+.1f} / 最大 {lg.max():+.1f}）")
        ok &= miss == 0
    print("確認: " + ("OK" if ok else "NG"))
    return ok


if __name__ == "__main__":
    sys.exit(0 if check(sys.argv[1], sys.argv[2]) else 1)
