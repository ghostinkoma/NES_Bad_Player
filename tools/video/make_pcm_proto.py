"""$4011 PCM 再生の試作 ROM を作る（音だけ）。

動画ファイルの音声 → 22,096Hz（1789773 / 81）・7bit（0〜127）→ BNROM の各バンクの $8000-$EFFF に順に詰める。
プレイヤー（nes/src/pcm_proto.s）は 81 サイクルごとに 1 つずつ $4011 へ書く。

  --nl : ファミコンの音の混ぜ方（DMC の出力は値に比例しない）を打ち消すように、値をあらかじめゆがめる
  --mesen-test : Mesen で書き込みの間隔と値を確かめる Lua を書き出す

使い方: python tools/video/make_pcm_proto.py --code build/pcm/pcm_proto.bin --labels build/pcm/pcm_proto.lbl
            -i resouce/origen_nes_256x240.mp4 -o build/pcm/pcm_proto.nes [--nl] [--mesen-test]
"""
import argparse
import os
import subprocess
import sys
import wave

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import make_video_rom as m  # noqa: E402

CYCLES = 81
HZ = m.CPU_HZ / CYCLES
DATA_SIZE = 0x7000          # $8000-$EFFF


def dmc_out(d):
    """DMC の値 d（0〜127）だけが鳴っているときの出力（NESdev の非線形ミキサーの式）"""
    d = np.asarray(d, dtype=np.float64)
    with np.errstate(divide="ignore"):
        return np.where(d > 0, 159.79 / (22638.0 / np.maximum(d, 1e-9) + 100.0), 0.0)


def to_pcm(x, nl):
    """-1〜1 → 0〜127"""
    if not nl:
        return np.clip(np.round(64 + x * 63), 0, 127).astype(np.uint8)
    top = float(dmc_out(127))
    o = (x + 1) / 2 * top                                  # 出力を 0〜top で比例させたい
    d = np.where(o > 0, 22638.0 / (159.79 / np.maximum(o, 1e-12) - 100.0), 0.0)
    return np.clip(np.round(d), 0, 127).astype(np.uint8)


def load_audio(path):
    lpf = int(min(10000, HZ * 0.45))                    # 22kHz なら 10kHz、18.6kHz なら 8.4kHz
    cmd = ["ffmpeg", "-v", "error", "-i", path, "-vn", "-ac", "1", "-ar", str(int(round(HZ))),
           "-af", f"highpass=f=60,lowpass=f={lpf}", "-f", "f32le", "-"]
    x = np.frombuffer(subprocess.run(cmd, stdout=subprocess.PIPE, check=True).stdout, np.float32).astype(np.float64)
    peak = np.percentile(np.abs(x), 99.9) if len(x) else 1.0
    return np.clip(x / max(peak, 1e-6), -1, 1)


LUA = r"""-- 自動生成: make_pcm_proto.py --mesen-test
-- $4011 への書き込みの間隔（CPU サイクル）と、書いた値の並びを確かめる
local N = %(n)d
local count, bad, prev, h, minI, maxI = 0, 0, nil, 0, 1e9, 0
emu.addMemoryCallback(function(addr, value)
  local c = emu.getState()["cpu.cycleCount"]
  if prev then
    local d = c - prev
    if d < minI then minI = d end
    if d > maxI then maxI = d end
    if d ~= %(cyc)d then bad = bad + 1 end
  end
  prev = c
  if count < N then h = (h * 31 + value) & 0xFFFFFFFF end
  count = count + 1
end, emu.callbackType.write, 0x4011)
local frames = 0
emu.addEventCallback(function()
  frames = frames + 1
  if frames == %(frames)d then
    print(string.format("writes %%d bad %%d min %%d max %%d hash %%d", count, bad, minI, maxI, h))
    emu.stop(0)
  end
end, emu.eventType.endFrame)
"""


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--code", required=True)
    ap.add_argument("--labels", required=True)
    ap.add_argument("-i", "--input", default=os.path.join(m.ROOT, "resouce", "origen_nes_256x240.mp4"))
    ap.add_argument("-o", "--out", required=True)
    ap.add_argument("--nl", action="store_true")
    ap.add_argument("--mesen-test", action="store_true")
    ap.add_argument("--cycles", type=int, default=81, help="1 サンプルのサイクル数（アセンブル時の CYC と同じにする）")
    a = ap.parse_args()
    global CYCLES, HZ
    CYCLES = a.cycles
    HZ = m.CPU_HZ / CYCLES

    x = load_audio(a.input)
    pcm = to_pcm(x, a.nl)
    nb = -(-len(pcm) // DATA_SIZE)
    if nb > 256:
        sys.exit(f"ROM に入りません（{nb} バンク）")
    data = np.full(nb * DATA_SIZE, to_pcm(np.zeros(1), a.nl)[0], np.uint8)
    data[:len(pcm)] = pcm

    code = bytearray(open(a.code, "rb").read())
    labels = m.read_labels(a.labels)
    code[labels["nbanks"] - 0xF000] = nb & 0xFF        # 256 バンクなら 0（inx で 0 に戻ったとき一致）
    prg = bytearray()
    for b in range(nb):
        prg += data[b * DATA_SIZE:(b + 1) * DATA_SIZE].tobytes() + bytes(code)
    units = len(prg) // 16384
    hdr = bytes([0x4E, 0x45, 0x53, 0x1A, units & 0xFF, 0, 0x21, 0x28, 0x20, (units >> 8) & 0x0F, 0x00, 0x07,
                 0, 0, 0, 0])
    with open(a.out, "wb") as fp:
        fp.write(hdr + prg)
    sec = len(pcm) / HZ
    print(f"ROM: {a.out}  {len(hdr) + len(prg):,} バイト / {nb} バンク / {HZ:.1f}Hz / {sec:.1f} 秒"
          f" / {'非線形の補正あり' if a.nl else '補正なし'}")

    # ROM の値をそのまま鳴らした WAV（エミュレータを通さない理想の音。非線形の補正ありは式で出力に直す）
    wav = os.path.splitext(a.out)[0] + "_ideal.wav"
    out = dmc_out(pcm) if a.nl else pcm.astype(np.float64)
    lo, hi = (0.0, float(dmc_out(127))) if a.nl else (0.0, 127.0)
    y = (out - (lo + hi) / 2) / ((hi - lo) / 2) * 0.9
    with wave.open(wav, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(int(round(HZ)))
        w.writeframes((np.clip(y, -1, 1) * 32767).astype("<i2").tobytes())
    print(f"理想の音: {wav}")

    if a.mesen_test:
        frames = 900                                     # 15 秒（バンクの切り替え 11 回）
        n = 200000
        h = 0
        for v in pcm[:n].tolist():
            h = (h * 31 + v) & 0xFFFFFFFF
        lua = os.path.splitext(a.out)[0] + "_test.lua"
        with open(lua, "w", encoding="utf-8", newline="\n") as fp:
            fp.write(LUA % dict(n=n, cyc=CYCLES, frames=frames))
        with open(os.path.splitext(a.out)[0] + "_expect.txt", "w") as fp:
            fp.write(f"{n} {h}\n")
        print(f"Mesen 確認用: {lua}（期待するハッシュ {h}）")


if __name__ == "__main__":
    main()
