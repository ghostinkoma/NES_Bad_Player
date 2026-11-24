#!/usr/bin/env python3
"""DPCM 音声の確認（Mesen の --testrunner と組み合わせて使う）

  1. Lua を作る:   python tools/video/check_audio.py lua  build/video/player.lbl build/video/movies_audio_test.lua
  2. Mesen で動かして、出力をファイルに保存する
  3. 調べる:       python tools/video/check_audio.py check ログ.txt

Lua はメニュー → Origen 再生 → 一時停止・再開 → コマ戻し → 再開 → メニュー と操作しながら、毎フレーム
「DMC が次に読む場所・残り・出力レベル」「そのとき CPU から見えるその場所の 1 バイト（今のバンク）」
「プレイヤーの状態」を書き出す。

check は、音が鳴っているフレームごとに次を確かめる:
  - DMC が読もうとしている 1 バイトが、本来その時刻に鳴るべき音声データと一致する（バンクの置き方が正しい）
  - DMC の再生位置と映像の位置のずれ（音ズレ）が小さい
  - 再生中に音が飛んだり戻ったりしない
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import make_video_rom as m  # noqa: E402

LUA = r"""-- 自動生成: tools/video/check_audio.py（DPCM 音声の確認用ログを出す）
local A = {frames=%(frames)d, stream=%(stream)d, bank=%(bank)d, paused=%(paused)d, audio_on=%(audio_on)d, ppu_busy=%(ppu_busy)d, sel=%(sel)d}
local function rd(a) return emu.read(a, emu.memType.nesMemory) end
local pending = nil
emu.addEventCallback(function()
  if pending then local t = {}; t[pending] = true; emu.setInput(t, 0) end
end, emu.eventType.inputPolled)
local function wait(n) for i = 1, n do coroutine.yield() end end
local function press(b) pending = b; wait(1); pending = nil; wait(2) end
local logging = true
local frame = 0
local script = coroutine.create(function()
  while rd(A.stream) ~= 4 do wait(1) end  -- 起動時のオープニングが終わるまで
  wait(200)                    -- メニュー（背景の音）
  press("down"); wait(60)      -- カーソルを動かす（この 1 回は背景が 1/60 秒止まる）
  press("up"); wait(60)
  press("a"); wait(2400)       -- Origen を 40 秒
  press("b"); wait(60)         -- 一時停止
  press("b"); wait(600)        -- 再開
  press("b"); wait(10)
  for i = 1, 8 do press("left"); wait(1); while rd(A.ppu_busy) ~= 0 do wait(1) end; wait(2) end
  press("b"); wait(600)        -- コマ戻しの後に再開
  press("start"); wait(300)    -- メニューへ
  for _, n in ipairs({%(extra)s}) do  -- カラー・グレーも 20 秒ずつ（途中で一時停止・再開）。入っている動画だけ
    while rd(A.sel) ~= n do press("down") end
    press("a"); wait(900)
    press("b"); wait(30)
    press("b"); wait(300)
    press("start"); wait(60)
  end
  emu.stop(0)
end)
emu.addEventCallback(function()
  frame = frame + 1
  local st = emu.getState()
  local addr = st["apu.dmc.currentAddr"]
  print(string.format("L %%d %%d %%d %%d %%d %%d %%d %%d %%d %%d", frame, rd(A.stream), rd(A.bank),
    addr, st["apu.dmc.bytesRemaining"], st["apu.dmc.outputLevel"], rd(A.audio_on),
    rd(A.frames) + rd(A.frames + 1) * 256, rd(A.paused), rd(addr)))
  if coroutine.status(script) ~= "dead" then
    local ok, err = coroutine.resume(script)
    if not ok then print("Lua エラー: " .. tostring(err)); emu.stop(99) end
  end
end, emu.eventType.endFrame)
"""




def load_info(audio_dir):
    import json
    with open(os.path.join(audio_dir, "movies_info.json"), encoding="utf-8") as fp:
        return {int(k): v for k, v in json.load(fp).items()}


def make_lua(labels, out, audio_dir):
    info = load_info(audio_dir)
    extra = ", ".join(str(n) for n in (3, 2) if n in info)   # ROM に入っているグレー・カラーだけ
    with open(out, "w", encoding="utf-8", newline="\n") as fp:
        fp.write(LUA % dict(extra=extra, **{k: labels[k] for k in
                                            ("frames", "stream", "bank", "paused", "audio_on", "ppu_busy", "sel")}))


def check(log, audio_dir):
    info = load_info(audio_dir)
    audio = {}
    for sid in info:
        p = os.path.join(audio_dir, f"audio_{sid}.bin")
        if info[sid]["audio"] and os.path.exists(p):
            audio[sid] = open(p, "rb").read()
    per_stream = {}
    rows = []
    for line in open(log, encoding="utf-8", errors="replace"):
        if line.startswith("L "):
            rows.append(list(map(int, line.split()[1:])))
    bad_byte = bad_sync = jumps = checked = 0
    offsets = []
    prev = None
    first_bad = None
    for f, sid, bank, addr, rem, lvl, on, frames, paused, byte in rows:
        if not on or sid not in audio:
            prev = None
            continue
        inf = info[sid]
        seg_sec = m.SEG_BYTES * 8 * inf["period"] / m.CPU_HZ
        nslots = inf["nslots"]
        rel = addr - inf["addr"]
        if not (0 <= rel < nslots * m.AUDIO_SLOT) or rel % m.AUDIO_SLOT >= m.SEG_BYTES:
            bad_byte += 1
            first_bad = first_bad or (f, "DMC が音声の置き場の外を読もうとしている", addr)
            continue
        slot, off = rel // m.AUDIO_SLOT, rel % m.AUDIO_SLOT
        t_video = frames / inf["fps"]
        jc = t_video / seg_sec
        j = slot + nslots * round((jc - slot) / nslots)
        t_dmc = (j * m.SEG_BYTES + off) * 8 * inf["period"] / m.CPU_HZ
        checked += 1
        per_stream[sid] = per_stream.get(sid, 0) + 1
        expect = audio[sid][j * m.AUDIO_SLOT + off] if j * m.AUDIO_SLOT + off < len(audio[sid]) else None
        if byte != expect:
            bad_byte += 1
            first_bad = first_bad or (f, f"DMC が読む 1 バイトが違う（セグメント {j} の {off} バイト目）", (byte, expect))
        d = t_dmc - t_video
        offsets.append(d)
        if abs(d) > 0.1:
            bad_sync += 1
        if prev is not None and prev[0] == sid and not paused:
            step = t_dmc - prev[1]
            if not (0.005 < step < 0.03):
                jumps += 1
        prev = (sid, t_dmc)
    import statistics
    print(f"音が鳴っていたフレーム: {checked}（動画ごと {dict(sorted(per_stream.items()))}）")
    print(f"  DMC が読むデータが正しい: {checked - bad_byte}/{checked}")
    if offsets:
        print(f"  音のずれ（音 - 映像）: 平均 {1000 * statistics.mean(offsets):+.0f} ms / "
              f"最小 {1000 * min(offsets):+.0f} ms / 最大 {1000 * max(offsets):+.0f} ms（±100ms を超えた {bad_sync} フレーム）")
    print(f"  再生中の音飛び: {jumps} 回")
    if first_bad:
        print(f"  最初の問題: フレーム {first_bad[0]}: {first_bad[1]} {first_bad[2]}")
    ok = checked > 2000 and bad_byte == 0 and bad_sync == 0 and jumps == 0 and set(per_stream) == set(audio)
    print("音声の確認: " + ("OK" if ok else "NG"))
    return ok


if __name__ == "__main__":
    default_dir = os.path.join(m.ROOT, "build", "video")
    if sys.argv[1] == "lua":
        make_lua(m.read_labels(sys.argv[2]), sys.argv[3], sys.argv[4] if len(sys.argv) > 4 else default_dir)
    else:
        ok = check(sys.argv[2], sys.argv[3] if len(sys.argv) > 3 else default_dir)
        sys.exit(0 if ok else 1)
