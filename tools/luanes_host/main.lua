-- ============================================================================
-- tools/luanes_host/main.lua
--   LuaNES（https://github.com/nico-abram/LuaNES）を改造せずに読み込み、
--   tools/emu_bridge/bridge.lua を挟んで起動する LÖVE 用ホスト。
--
--   起動: love tools/luanes_host <ROMファイル>
--         （通常は project の run_luanes.ps1 から起動する）
--
--   仕組み:
--     1. LuaNES の main.lua をそのまま読み込む（love.load / love.draw などが定義される）
--     2. love.load を包み、LuaNES の初期化後に bridge.lua を読み込む
--     3. bridge.lua は Mesen の API（emu.read / emu.write / emu.addEventCallback）を
--        使う作りなので、同じ名前の関数を LuaNES の CPU RAM につないで用意する
--     4. Nes:run_once（1フレーム分のエミュレーション）の後に毎回ブリッジ処理を呼ぶ
--
--   環境変数（run_luanes.ps1 が設定する。未設定なら既定のパス）
--     LUANES_DIR      LuaNES を clone したフォルダ（既定: project の1つ上の LuaNES）
--     NES_BRIDGE_DIR  state.bin / pad.txt を置くフォルダ（既定: bridge.lua に書かれた場所）
--     NES_BRIDGE_LUA  bridge.lua の場所（既定: tools/emu_bridge/bridge.lua）
-- ============================================================================

local function norm(p)
    return (p:gsub("\\", "/"))
end

local function file_exists(p)
    local f = io.open(p, "rb")
    if f then f:close() return true end
    return false
end

local HOST_DIR   = norm(love.filesystem.getSource())
local LUANES_DIR = norm(os.getenv("LUANES_DIR") or (HOST_DIR .. "/../../../LuaNES"))
local BRIDGE_LUA = norm(os.getenv("NES_BRIDGE_LUA") or (HOST_DIR .. "/../emu_bridge/bridge.lua"))

if os.getenv("NES_BRIDGE_DIR") then
    NES_BRIDGE_DIR = (norm(os.getenv("NES_BRIDGE_DIR")):gsub("/*$", "/"))   -- bridge.lua が参照
end

if not file_exists(LUANES_DIR .. "/nes.lua") then
    error("LuaNES が見つかりません: " .. LUANES_DIR ..
          "\n環境変数 LUANES_DIR に LuaNES を clone したフォルダを指定してください。")
end
if not file_exists(BRIDGE_LUA) then
    error("bridge.lua が見つかりません: " .. BRIDGE_LUA)
end

-- LuaNES のモジュール（require "nes" / "libs/serpent" など）を LuaNES フォルダから探す
package.path = LUANES_DIR .. "/?.lua;" .. package.path

-- LuaNES 本体の main.lua をそのまま実行
dofile(LUANES_DIR .. "/main.lua")

-- LuaNES の UNROM（mapper 2）のバンク切り替え修正（LuaNES のファイルは書き換えない）
--   元のコードはコピーのループ範囲が 0x8001〜0x4000 になっていて 1 回も回らず、
--   バンクが切り替わらない。第3フェーズのスライドショー ROM はバンク切り替えを使うので、
--   コンストラクタ（固定バンクの読み込み）と同じ書き方で正しく切り替える関数に差し替える。
--   ROM 読み込み時（love.load の中）に bind されるので、love.load より前に差し替えること。
do
    local UxROM = ROM and ROM.MAPPER_DB and ROM.MAPPER_DB[0x02]
    if UxROM then
        function UxROM:poke_8000(_addr, data)
            local bank = self.prg_banks[(data % #self.prg_banks) + 1]
            local ref = self.prg_ref
            for i = 0x8000 + 1, 0x8000 + 0x4000 do
                ref[i] = bank[i - 0x8000]
            end
        end
        print("luanes_host: UNROM のバンク切り替えを修正しました")
    end
end

local luanes_load = love.load

function love.load(arg, ...)
    if arg == nil or arg[1] == nil then
        error("ROM ファイルを指定してください（例: love tools/luanes_host build/phase1.nes）")
    end
    luanes_load(arg, ...)

    -- bridge.lua 用に Mesen 互換の最小 API を用意（CPU RAM $0000-$07FF のみ対応）
    local frame_callback = nil
    local ram = Nes.cpu.ram          -- LuaNES の CPU RAM（0 起点のテーブル）
    emu = {
        memType   = { nesMemory = 0 },
        eventType = { endFrame = 0 },
        read  = function(addr) return ram[addr % 0x800] end,
        write = function(addr, value) ram[addr % 0x800] = value end,
        log   = function(s) print(s) end,
        addEventCallback = function(f) frame_callback = f end,
    }
    dofile(BRIDGE_LUA)
    assert(frame_callback, "bridge.lua がフレーム処理を登録しませんでした")

    -- 1フレーム実行するたびにブリッジ処理を呼ぶ
    local run_once = Nes.run_once
    Nes.run_once = function(self)
        run_once(self)
        frame_callback()
    end

    love.window.setTitle("LuaNES + bridge")
    print("luanes_host: LuaNES = " .. LUANES_DIR)
    print("luanes_host: ROM    = " .. tostring(arg[1]))
end
