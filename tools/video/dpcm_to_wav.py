"""ビルドで作った音声（build/video/audio_N.bin）を、DMC と同じ規則で鳴らした WAV にする。

エミュレータを通さない「理想の再生音」。Mesen で録音した音と聞き比べると、
雑音が元のデータ（変換）のせいか、再生（プレイヤー・エミュレータ）のせいかを切り分けられる。

使い方: python tools/video/dpcm_to_wav.py 1 24.9   （動画の番号、DPCM の kHz）
"""
import os
import sys
import wave

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import make_video_rom as m

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def main():
    sid = int(sys.argv[1])
    khz = float(sys.argv[2])
    rate = min(range(16), key=lambda r: abs(m.CPU_HZ / m.DMC_PERIODS[r] / 1000 - khz))
    hz = m.CPU_HZ / m.DMC_PERIODS[rate]
    src = os.path.join(ROOT, "build", "video", f"audio_{sid}.bin")
    data = open(src, "rb").read()
    segs = [data[i:i + m.AUDIO_SLOT] for i in range(0, len(data), m.AUDIO_SLOT)]
    lv = m.dmc_levels(segs).astype(np.float64)
    y = (lv - 64) / 64 * 0.9
    out = os.path.join(ROOT, "build", "video", f"ideal_{sid}_{hz / 1000:.1f}kHz.wav")
    with wave.open(out, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(int(round(hz)))
        w.writeframes((np.clip(y, -1, 1) * 32767).astype("<i2").tobytes())
    print(f"{out}（{len(lv) / hz:.1f} 秒、{hz:.0f}Hz）")


if __name__ == "__main__":
    main()
