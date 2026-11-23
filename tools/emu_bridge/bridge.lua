-- ============================================================================
-- bridge.lua - お絵描き GUI <-> エミュレータ ブリッジ（第1フェーズ）
--
-- 対応: Mesen2 / Mesen (旧版) / FCEUX / LuaNES（tools/luanes_host 経由）
--
-- 動作:
--   1. GUI が書き出す state.bin を数フレームごとに読む（ポーリング）
--   2. 前回送った内容と比べ、変わった部分だけをキューに積む
--   3. 1フレームに1パケット（最大128バイト）を CPU RAM のメールボックスへ書く
--   4. ROM 側の NMI がメールボックスの内容を PPU へ転送する
--   5. ROM が書いたパッド状態を pad.txt に書き出す（GUI が表示）
--
-- ※ ファイル入出力に Lua の io ライブラリを使う。
--   Mesen2 ではスクリプトウィンドウの設定で
--   「I/O と OS 関数へのアクセスを許可」系の項目を ON にする必要がある。
-- ============================================================================

-- ---------------------------------------------------------------------------
-- 設定: GUI 側の「ブリッジフォルダ」と同じ場所にすること（末尾の / を忘れずに）
-- ---------------------------------------------------------------------------
-- （グローバル変数 NES_BRIDGE_DIR が設定されていればそちらを優先。LuaNES ホストが使う）
local BRIDGE_DIR  = NES_BRIDGE_DIR or "D:/hobby/nes/project/build/bridge/"
local STATE_FILE  = BRIDGE_DIR .. "state.bin"
local PAD_FILE    = BRIDGE_DIR .. "pad.txt"
local POLL_FRAMES = 2       -- 何フレームごとに state.bin を読むか

-- ---------------------------------------------------------------------------
-- メールボックス（nes/src/mailbox.inc と一致させる）
-- ---------------------------------------------------------------------------
local MB_FLAG    = 0x0300
local MB_ADDR_HI = 0x0301
local MB_ADDR_LO = 0x0302
local MB_COUNT8  = 0x0303
local MB_PAD     = 0x0320
local MB_FRAME   = 0x0321
local MB_READY   = 0x0323
local MB_ATTACH  = 0x0324
local MB_BUF     = 0x0380
local READY_MAGIC  = 0xA5
local ATTACH_MAGIC = 0x5A
local PACKET_MAX   = 128

-- ---------------------------------------------------------------------------
-- state.bin のレイアウト（tools/paint/nes_paint.py の write_state と一致させる）
--   0    "NESPAINT"
--   8    seq (uint32 LE)
--   12   version (uint16 LE) = 1, reserved (uint16)
--   16   CHR      4096 バイト → PPU $0000
--   4112 属性     64 バイト   → PPU $23C0
--   4176 パレット 32 バイト   → PPU $3F00
--   4208 seq (uint32 LE)  ※書き込み途中のファイルを検出するため先頭と同じ値
-- ---------------------------------------------------------------------------
local STATE_SIZE = 4212
local OFS_PAYLOAD = 16
local PAYLOAD_SIZE = 4096 + 64 + 32

-- 転送チャンク定義: { payload内の開始位置(0起点), 長さ, PPUアドレス }
local chunks = {}
for i = 0, 31 do
  chunks[#chunks + 1] = { i * 128, 128, i * 128 }           -- CHR 32 チャンク
end
chunks[#chunks + 1] = { 4096, 64, 0x23C0 }                  -- 属性テーブル
chunks[#chunks + 1] = { 4160, 32, 0x3F00 }                  -- パレット

-- ---------------------------------------------------------------------------
-- エミュレータごとの差を吸収
-- ---------------------------------------------------------------------------
local api = {}

if emu and emu.addEventCallback then
  -- Mesen2 / Mesen
  local mt = nil
  for _, name in ipairs({ "nesMemory", "cpu", "nesDebug", "cpuDebug" }) do
    if emu.memType[name] ~= nil then
      mt = emu.memType[name]
      emu.log("bridge: memType = " .. name)
      break
    end
  end
  if mt == nil then
    local names = {}
    for k, _ in pairs(emu.memType) do names[#names + 1] = tostring(k) end
    error("bridge: CPU メモリの memType が見つかりません。候補: " .. table.concat(names, ", "))
  end
  api.read  = function(a) return emu.read(a, mt) end
  api.write = function(a, v) emu.write(a, v, mt) end
  api.log   = function(s) emu.log(s) end
  api.run   = function(f) emu.addEventCallback(f, emu.eventType.endFrame) end
elseif memory and memory.writebyte then
  -- FCEUX
  api.read  = memory.readbyte
  api.write = memory.writebyte
  api.log   = print
  api.run   = function(f)
    while true do
      f()
      emu.frameadvance()
    end
  end
else
  error("bridge: 未対応のエミュレータです（Mesen2 / FCEUX を使ってください）")
end

if io == nil or io.open == nil then
  error("bridge: io ライブラリが使えません。スクリプト設定で I/O アクセスを許可してください。")
end

-- ---------------------------------------------------------------------------
-- 状態
-- ---------------------------------------------------------------------------
local target = {}           -- GUI から読んだ最新の内容（payload, 1起点）
local sent = {}             -- エミュレータへ送り済みの内容（-1 = 未送信）
local dirty = {}            -- チャンクがキューに入っているか
local queue = {}            -- 送信待ちチャンク番号
local last_seq = -1
local attached = false
local frame = 0
local last_pad = -1
local warned_missing = false

for i = 1, PAYLOAD_SIZE do
  target[i] = 0
  sent[i] = -1
end

local function u32(s, pos)   -- pos は 1 起点
  local a, b, c, d = string.byte(s, pos, pos + 3)
  return a + b * 256 + c * 65536 + d * 16777216
end

local function invalidate_all()
  for i = 1, PAYLOAD_SIZE do sent[i] = -1 end
  last_seq = -1             -- 次のポーリングで必ず全体を比較し直す
end

-- state.bin を読み、変化したチャンクをキューへ
local function poll_state()
  local f = io.open(STATE_FILE, "rb")
  if f == nil then
    if not warned_missing then
      api.log("bridge: " .. STATE_FILE .. " がまだありません（GUI を起動してください）")
      warned_missing = true
    end
    return
  end
  local s = f:read("*a")
  f:close()
  warned_missing = false

  if s == nil or #s ~= STATE_SIZE then return end         -- 書き込み途中
  if string.sub(s, 1, 8) ~= "NESPAINT" then return end
  local seq = u32(s, 9)
  if seq ~= u32(s, STATE_SIZE - 3) then return end         -- 書き込み途中
  if seq == last_seq then return end
  last_seq = seq

  local bytes = { string.byte(s, OFS_PAYLOAD + 1, OFS_PAYLOAD + PAYLOAD_SIZE) }
  for i = 1, PAYLOAD_SIZE do target[i] = bytes[i] end

  for ci, c in ipairs(chunks) do
    if not dirty[ci] then
      local base = c[1]
      for i = base + 1, base + c[2] do
        if target[i] ~= sent[i] then
          dirty[ci] = true
          queue[#queue + 1] = ci
          break
        end
      end
    end
  end
end

-- キュー先頭のチャンクをメールボックスへ（6502 が前のパケットを処理済みのときだけ）
local function send_one()
  if #queue == 0 then return end
  if api.read(MB_FLAG) ~= 0 then return end

  local ci = table.remove(queue, 1)
  dirty[ci] = false
  local c = chunks[ci]
  local base, len, ppu = c[1], c[2], c[3]

  for i = 0, len - 1 do
    local v = target[base + i + 1]
    api.write(MB_BUF + i, v)
    sent[base + i + 1] = v
  end
  api.write(MB_ADDR_HI, math.floor(ppu / 256))
  api.write(MB_ADDR_LO, ppu % 256)
  api.write(MB_COUNT8, math.floor(len / 8))
  api.write(MB_FLAG, 1)                                    -- 最後にフラグを立てる
end

local function write_pad(pad)
  local f = io.open(PAD_FILE, "w")
  if f == nil then return end
  local names = { "A", "B", "SELECT", "START", "UP", "DOWN", "LEFT", "RIGHT" }
  local pressed = {}
  local bit = 128
  for _, n in ipairs(names) do
    if math.floor(pad / bit) % 2 == 1 then pressed[#pressed + 1] = n end
    bit = bit / 2
  end
  f:write(string.format("%d %s\n", pad, table.concat(pressed, " ")))
  f:close()
end

-- ---------------------------------------------------------------------------
-- 毎フレーム処理
-- ---------------------------------------------------------------------------
local function on_frame()
  frame = frame + 1

  -- ROM の準備完了を待つ / リセット検出
  if api.read(MB_READY) ~= READY_MAGIC then
    attached = false
    return
  end
  if not attached or api.read(MB_ATTACH) ~= ATTACH_MAGIC then
    -- 初回接続、または ROM がリセットされて RAM が消えた → 全部送り直す
    api.write(MB_ATTACH, ATTACH_MAGIC)
    attached = true
    invalidate_all()
    queue = {}
    dirty = {}
    api.log("bridge: ROM に接続しました")
  end

  if frame % POLL_FRAMES == 0 then poll_state() end
  send_one()

  local pad = api.read(MB_PAD)
  if pad ~= last_pad then
    last_pad = pad
    write_pad(pad)
  end
end

api.log("bridge: 開始 (" .. BRIDGE_DIR .. ")")
api.run(on_frame)
