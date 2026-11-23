-- Mesen の --testrunner 用: メニュー画面を文字で書き出し（tools/video/shot_from_log.py で PNG に）、
-- そのあと FPS 表示つきで Origen を 3000 フレーム再生し、$2007 の書き込みが VBlank 内に収まっているか測る
local n, pending = 0, nil
local maxpos, bad, writes = 0, 0, 0
local measuring = false
emu.addMemoryCallback(function()
  if not measuring then return end
  local st = emu.getState()
  if not st["ppu.mask.backgroundEnabled"] then return end
  local sl = st["ppu.scanline"]
  writes = writes + 1
  if sl < 241 then bad = bad + 1 end
  local pos = (sl - 241) * 341 + st["ppu.cycle"]
  if pos > maxpos then maxpos = pos end
end, emu.callbackType.write, 0x2007, 0x2007)
emu.addEventCallback(function()
  if pending then local t = {}; t[pending] = true; emu.setInput(t, 0) end
end, emu.eventType.inputPolled)

local function dump()
  local buf = emu.getScreenBuffer()
  local i = 1
  for y = 0, 239 do
    local parts, prev, run = {}, -1, 0
    for x = 1, 256 do
      local v = buf[i] & 0xFFFFFF
      if v == prev then run = run + 1 else
        if run > 0 then parts[#parts + 1] = string.format("%06x*%d", prev, run) end
        prev, run = v, 1
      end
      i = i + 1
    end
    parts[#parts + 1] = string.format("%06x*%d", prev, run)
    print("ROW " .. y .. " " .. table.concat(parts, ","))
  end
end

emu.addEventCallback(function()
  n = n + 1
  if n == 1 then n = -320 end  -- 起動時のオープニング（約 4.7 秒）が終わるのを待つ
  if n == 90 then dump() end
  if n == 100 then pending = "a" elseif n == 101 then pending = nil end
  if n == 200 then pending = "select" elseif n == 201 then pending = nil end
  if n == 210 then measuring = true end
  if n == 400 then print("SHOT_PLAY") dump() end
  if n == 3210 then
    print(string.format("TIMING writes=%d in_render=%d last_write_cpu_cycle_after_vblank=%d", writes, bad, maxpos // 3))
    emu.stop(bad > 0 and 1 or 0)
  end
end, emu.eventType.endFrame)
