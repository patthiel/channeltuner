-- tv_epg.lua  — companion Lua script for tv_channels.py
-- Keep this file in the same directory as tv_channels.py.
--
-- Python sends:  script-message show-epg  <ch_label> <title>
-- Python sends:  script-message hide-epg

local mp      = require "mp"
local assdraw = require "mp.assdraw"

local ov    = mp.create_osd_overlay("ass-events")
local timer = nil

-- Retro cable-EPG colours (BGR order for ASS)
local COL_NAVY  = "351F0D"
local COL_GOLD  = "55BBEE"
local COL_DOT   = "AAAAAA"
local COL_NOW   = "CCCCCC"
local COL_WHITE = "FFFFFF"

local function draw_epg(ch_label, title)
    -- Read the actual OSD canvas size at draw time so the box always
    -- covers full width regardless of window size or video aspect ratio.
    local W = mp.get_property_number("osd-width",  1280)
    local H = mp.get_property_number("osd-height", 720)

    -- Clamp to sane minimums so nothing divides by zero on startup
    if W < 100 then W = 1280 end
    if H < 100 then H = 720  end

    local ass     = assdraw.ass_new()
    local box_top = math.floor(H * 0.80)
    local pad_x   = math.floor(W * 0.028)   -- ~36px at 1280 wide
    local pad_y   = math.floor(H * 0.014)   -- ~10px at 720 tall

    -- Tell the overlay what canvas size we're drawing for
    ov.res_x = W
    ov.res_y = H
    
    --- We had some pretty EPG that we've decided to comment out because it wasn't behaving..
    -- -- Background filled rectangle
    -- ass:new_event()
    -- ass:pos(0, 0)
    -- ass:append("{\\bord0}{\\shad0}{\\1a&20&}")
    -- ass:append("{\\1c&" .. COL_NAVY .. "&}{\\3c&" .. COL_NAVY .. "&}")
    -- ass:draw_start()
    -- ass:rect_cw(0, box_top, W, H)
    -- ass:draw_stop()

    -- -- Thin gold accent line at the top of the box
    -- ass:new_event()
    -- ass:pos(0, 0)
    -- ass:append("{\\bord0}{\\shad0}{\\1a&00&}")
    -- ass:append("{\\1c&" .. COL_GOLD .. "&}")
    -- ass:draw_start()
    -- ass:rect_cw(0, box_top, W, box_top + math.max(2, math.floor(H * 0.004)))
    -- ass:draw_stop()

    -- Font sizes scaled proportionally to canvas height
    local fs_label = math.floor(H * 0.031)   -- ~22px at 720
    local fs_title = math.floor(H * 0.058)   -- ~42px at 720

    -- Label line
    ass:new_event()
    ass:an(7)
    ass:pos(pad_x, box_top + pad_y)
    ass:append("{\\p0\\bord0\\shad0\\1a&00&\\fs" .. fs_label .. "\\b0}")
    ass:append("{\\1c&" .. COL_DOT  .. "&}\xe2\x96\xa0 ")
    ass:append("{\\1c&" .. COL_GOLD .. "&\\b1}" .. ch_label .. "  ")
    ass:append("{\\1c&" .. COL_NOW  .. "&\\b0}\xc2\xb7 Now Playing")

    -- Title line
    ass:new_event()
    ass:an(7)
    ass:pos(pad_x, box_top + pad_y + math.floor(fs_label * 1.35))
    ass:append("{\\p0\\bord0\\shad0\\1a&00&\\fs" .. fs_title .. "\\b1}")
    ass:append("{\\1c&" .. COL_WHITE .. "&}" .. title)

    ov.data = ass.text
    ov:update()
end

local function hide_epg()
    if timer then timer:kill(); timer = nil end
    ov:remove()
end

mp.register_script_message("show-epg", function(ch_label, title)
    if timer then timer:kill(); timer = nil end
    draw_epg(ch_label, title)
    timer = mp.add_timeout(3.5, function()
        ov:remove()
        timer = nil
    end)
end)

mp.register_script_message("hide-epg", function()
    hide_epg()
end)
