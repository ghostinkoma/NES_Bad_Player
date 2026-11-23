-- LÖVE の設定（LuaNES 本体の conf.lua と同じ内容 + タイトル）
function love.conf(t)
    t.identity = "LuaNES_bridge"
    t.console = true                 -- Windows でログ用のコンソールを開く
    t.window.title = "LuaNES + bridge"
end
