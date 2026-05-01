-- tv_epg.lua  — companion Lua script for tv_channels.py
-- Keep this file in the same directory as tv_channels.py.
--
-- Python sends:  script-message show-epg  <ch_label> <title>
-- Python sends:  script-message hide-epg

local mp      = require "mp"
local assdraw = require "mp.assdraw"

local ov    = mp.create_osd_overlay("ass-events")
local timer = nil

-- Retro Comcast EPG colours (ASS uses BGR hex order)
-- e.g. RGB #0D1F35 → BGR string "351F0D"
local COL_DARK_NAVY  = "351F0D"   -- #0D1F35  very dark navy (panel bg)
local COL_TIMELINE   = "41280F"   -- #0F2841  slightly lighter navy (top bar)
local COL_CHANNEL_BG = "A85F4B"   -- #4B5FA8  blue-purple (channel column)
local COL_GRID_BG    = "48380C"   -- #0C3848  dark teal (empty grid cells)
local COL_HIGHLIGHT  = "AFE6C8"   -- #C8E6AF  lime-green (now-playing cell)
local COL_CELL_LINE  = "5A4A1A"   -- #1A4A5A  subtle grid-line tint
local COL_WHITE      = "FFFFFF"
local COL_DARK_TEXT  = "1C1C1C"   -- dark text for light (lime) background
local COL_DIM_TEXT   = "BBBBBB"   -- dimmed text on dark backgrounds

local last_ch_label = nil
local last_title    = nil
local mouse_timer   = nil

local function draw_epg(ch_label, title)
    local W = mp.get_property_number("osd-width",  1280)
    local H = mp.get_property_number("osd-height", 720)

    if W < 100 then W = 1280 end
    if H < 100 then H = 720  end

    local ass = assdraw.ass_new()
    ov.res_x  = W
    ov.res_y  = H

    -- Layout geometry
    local panel_h   = math.floor(H * 0.24)        -- total panel height
    local panel_top = H - panel_h                  -- y where panel starts
    local bar_h     = math.floor(panel_h * 0.20)  -- top timeline bar
    local ch_w      = math.floor(W * 0.115)       -- channel column width
    local now_right = math.floor(W * 0.52)        -- right edge of now-playing cell
    local pad_x     = math.floor(W * 0.012)
    local pad_y     = math.floor(H * 0.009)

    -- Font sizes
    local fs_ch    = math.floor(H * 0.030)   -- channel label
    local fs_title = math.floor(H * 0.048)   -- show title
    local fs_now   = math.floor(H * 0.021)   -- "NOW" / sub-labels

    local grid_top = panel_top + bar_h

    -- ── 1. Full panel background (dark navy) ──────────────────────────────
    ass:new_event()
    ass:pos(0, 0)
    ass:append("{\\bord0}{\\shad0}{\\1a&00&}{\\1c&" .. COL_DARK_NAVY .. "&}")
    ass:draw_start()
    ass:rect_cw(0, panel_top, W, H)
    ass:draw_stop()

    -- ── 2. Top timeline bar (slightly lighter navy) ────────────────────────
    ass:new_event()
    ass:pos(0, 0)
    ass:append("{\\bord0}{\\shad0}{\\1a&00&}{\\1c&" .. COL_TIMELINE .. "&}")
    ass:draw_start()
    ass:rect_cw(0, panel_top, W, grid_top)
    ass:draw_stop()

    -- ── 3. Channel column (blue-purple) ───────────────────────────────────
    ass:new_event()
    ass:pos(0, 0)
    ass:append("{\\bord0}{\\shad0}{\\1a&00&}{\\1c&" .. COL_CHANNEL_BG .. "&}")
    ass:draw_start()
    ass:rect_cw(0, grid_top, ch_w, H)
    ass:draw_stop()

    -- ── 4. Now-playing cell (lime-green highlight) ─────────────────────────
    ass:new_event()
    ass:pos(0, 0)
    ass:append("{\\bord0}{\\shad0}{\\1a&00&}{\\1c&" .. COL_HIGHLIGHT .. "&}")
    ass:draw_start()
    ass:rect_cw(ch_w, grid_top, now_right, H)
    ass:draw_stop()

    -- ── 5. Remaining grid cells (dark teal) ──────────────────────────────
    ass:new_event()
    ass:pos(0, 0)
    ass:append("{\\bord0}{\\shad0}{\\1a&00&}{\\1c&" .. COL_GRID_BG .. "&}")
    ass:draw_start()
    ass:rect_cw(now_right, grid_top, W, H)
    ass:draw_stop()

    -- ── 6. Subtle horizontal grid line (mid-row divider) ──────────────────
    local mid_y = grid_top + math.floor((H - grid_top) * 0.5)
    ass:new_event()
    ass:pos(0, 0)
    ass:append("{\\bord0}{\\shad0}{\\1a&00&}{\\1c&" .. COL_CELL_LINE .. "&}")
    ass:draw_start()
    ass:rect_cw(ch_w, mid_y, W, mid_y + 1)
    ass:draw_stop()

    -- ── 7. Vertical separator: channel col / grid ─────────────────────────
    ass:new_event()
    ass:pos(0, 0)
    ass:append("{\\bord0}{\\shad0}{\\1a&00&}{\\1c&" .. COL_CELL_LINE .. "&}")
    ass:draw_start()
    ass:rect_cw(ch_w, grid_top, ch_w + 1, H)
    ass:draw_stop()

    -- ── 8. Vertical separator: now-playing / future cells ─────────────────
    ass:new_event()
    ass:pos(0, 0)
    ass:append("{\\bord0}{\\shad0}{\\1a&00&}{\\1c&" .. COL_CELL_LINE .. "&}")
    ass:draw_start()
    ass:rect_cw(now_right, grid_top, now_right + 1, H)
    ass:draw_stop()

    -- ── 9. "NOW" label in timeline bar (above channel column) ─────────────
    ass:new_event()
    ass:an(5)
    ass:pos(math.floor(ch_w * 0.5), panel_top + math.floor(bar_h * 0.5))
    ass:append("{\\p0\\bord0\\shad0\\1a&00&\\fs" .. fs_now .. "\\b1}")
    ass:append("{\\1c&" .. COL_WHITE .. "&}NOW")

    -- ── 10. Channel label (centered in channel column) ────────────────────
    local col_mid_x = math.floor(ch_w * 0.5)
    local col_mid_y = grid_top + math.floor((H - grid_top) * 0.5)
    ass:new_event()
    ass:an(5)
    ass:pos(col_mid_x, col_mid_y)
    ass:append("{\\p0\\bord0\\shad1\\1a&00&\\fs" .. fs_ch .. "\\b1}")
    ass:append("{\\1c&" .. COL_WHITE .. "&}" .. ch_label)

    -- ── 11. "Now Playing" sub-label in highlight cell ─────────────────────
    local cell_mid_x = math.floor((ch_w + now_right) * 0.5)
    local cell_top_y = grid_top + pad_y + math.floor(fs_now * 0.5)
    ass:new_event()
    ass:an(5)
    ass:pos(cell_mid_x, cell_top_y)
    ass:append("{\\p0\\bord0\\shad0\\1a&00&\\fs" .. fs_now .. "\\b0}")
    ass:append("{\\1c&" .. "505050" .. "&}Now Playing")

    -- ── 12. Show title (centered in highlight cell) ───────────────────────
    local title_y = grid_top + math.floor((H - grid_top) * 0.55)
    ass:new_event()
    ass:an(5)
    ass:pos(cell_mid_x, title_y)
    ass:append("{\\p0\\bord0\\shad0\\1a&00&\\fs" .. fs_title .. "\\b1}")
    ass:append("{\\1c&" .. COL_DARK_TEXT .. "&}" .. title)

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

mp.register_script_message("cache-epg-info", function(ch_label, title)
    last_ch_label = ch_label
    last_title    = title
end)

mp.observe_property("mouse-pos", "native", function(_, pos)
    if not pos then return end
    if not last_ch_label or not last_title then return end

    if mouse_timer then
        mouse_timer:kill()
        mouse_timer = nil
    end

    -- If a channel-change EPG is already showing, just extend its timeout
    if timer then
        timer:kill()
        timer = mp.add_timeout(3.5, function()
            ov:remove()
            timer = nil
        end)
        return
    end

    draw_epg(last_ch_label, last_title)
    mouse_timer = mp.add_timeout(3.5, function()
        ov:remove()
        mouse_timer = nil
    end)
end)
