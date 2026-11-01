-- tv_epg.lua  — companion Lua script for tv_channels.py
-- Keep this file in the same directory as tv_channels.py.
--
-- Python sends:  script-message show-epg  <ch_label> <title> <guide>
-- Python sends:  script-message cache-epg-info <ch_label> <title> <guide>
-- Python sends:  script-message hide-epg
--
-- <guide> is one row per channel separated by "\n"; fields by "\t":
--     ch_label  title  seconds_remaining(-1 = unknown/live)  duration(0 = live)
-- Rows are drawn bottom-anchored; Python currently sends just the current channel.

local mp      = require "mp"
local assdraw = require "mp.assdraw"

local ov = mp.create_osd_overlay("ass-events")

-- Cable-guide palette (BGR order for ASS)
local COL_HEADER = "6E3F1A"   -- time bar
local COL_CELL   = "4F3A1B"   -- program cell
local COL_CHAN   = "D07860"   -- channel label cell
local COL_NOW    = "A5E5D2"   -- highlighted current program
local COL_LINE   = "888888"   -- grid lines
local COL_TOP    = "6B3A1F"   -- banner strip above the grid
local COL_WHITE  = "FFFFFF"
local COL_DARK   = "2A1A10"

local SPAN_MIN = 90           -- minutes of schedule visible in the grid
local SHOW_SECS = 6
-- Full schedule view (time ticks + repeating programs). Off by default:
-- a channel is a single looping video, so we just show the time of day.
-- Enabled with --script-opts=epg_schedule=yes (config: "epg_show_schedule").
local SCHEDULE = mp.get_opt("epg_schedule") == "yes"           -- how long the guide stays up

local last = nil              -- last { ch_label, title, guide } received
local hide_timer = nil

local function esc(s)
    return (s:gsub("[{}\\]", { ["{"] = "(", ["}"] = ")", ["\\"] = "/" }))
end

local function clock(t)
    local d = os.date("*t", t)
    local h = d.hour % 12
    if h == 0 then h = 12 end
    return string.format("%d:%02d %s", h, d.min, d.hour >= 12 and "PM" or "AM")
end

local function parse_guide(guide, ch_label, title)
    local rows = {}
    if guide and guide ~= "" then
        for line in (guide .. "\n"):gmatch("(.-)\n") do
            local f = {}
            for field in (line .. "\t"):gmatch("(.-)\t") do f[#f + 1] = field end
            if #f >= 4 then
                rows[#rows + 1] = {
                    label = f[1], title = f[2],
                    remaining = tonumber(f[3]) or -1,
                    duration = tonumber(f[4]) or 0,
                }
            end
        end
    end
    if #rows == 0 then
        rows[1] = { label = ch_label, title = title, remaining = -1, duration = 0 }
    end
    return rows
end

local function rect(ass, x1, y1, x2, y2, col)
    ass:new_event()
    ass:pos(0, 0)
    ass:append("{\\an7\\bord0\\shad0\\1a&H00&\\1c&" .. col .. "&}")
    ass:draw_start()
    ass:rect_cw(x1, y1, x2, y2)
    ass:draw_stop()
end

-- Left-aligned, vertically centred text clipped to its cell
local function text(ass, x1, y1, x2, y2, str, size, col, bold, pad)
    ass:new_event()
    ass:pos(x1 + pad, (y1 + y2) / 2)
    ass:append(string.format("{\\an4\\clip(%d,%d,%d,%d)\\bord0\\shad0\\1a&H00&\\fs%d\\b%d\\1c&%s&}%s",
        x1, y1, x2, y2, size, bold and 1 or 0, col, esc(str)))
end

local function draw_epg(ch_label, title, guide)
    local W = mp.get_property_number("osd-width",  1280)
    local H = mp.get_property_number("osd-height", 720)
    if W < 100 then W = 1280 end
    if H < 100 then H = 720  end

    ov.res_x = W
    ov.res_y = H

    local rows = parse_guide(guide, ch_label, title)

    local banner_h = math.floor(H * 0.05)
    local head_h   = math.floor(H * 0.065)
    local row_h    = math.floor(H * 0.09)
    local left_w   = math.floor(W * 0.20)
    local divider  = math.max(3, math.floor(W * 0.004))
    local gx0      = left_w + divider
    local pad      = math.floor(W * 0.012)
    local fs_cell  = math.floor(H * 0.036)
    local fs_head  = math.floor(H * 0.032)

    local nrows  = math.min(#rows, 4)
    local top    = H - math.floor(H * 0.02) - banner_h - head_h - row_h * nrows
    local head_y = top + banner_h
    local grid_y = head_y + head_h
    local grid_h = row_h * nrows
    local span   = SPAN_MIN * 60
    local pxs    = (W - gx0) / span          -- pixels per second

    local ass = assdraw.ass_new()

    -- Banner strip + header bar
    rect(ass, 0, top, W, head_y, COL_TOP)
    text(ass, 0, top, W, head_y, "CHANNEL GUIDE", fs_head, COL_WHITE, true, pad)
    rect(ass, 0, head_y, W, grid_y, COL_HEADER)

    local now = os.time()
    if not SCHEDULE then
        -- Simple mode: current time of day only
        text(ass, gx0, head_y, W, grid_y, clock(now), fs_head, COL_WHITE, true, pad)
    end

    -- Time ticks (now, +30, +60, +90)
    for i = 0, (SCHEDULE and 3 or -1) do
        local x = math.floor(gx0 + i * 30 * 60 * pxs)
        if i < 3 then
            rect(ass, x, head_y + math.floor(head_h * 0.2), x + 2, grid_y - math.floor(head_h * 0.2), COL_LINE)
            text(ass, x + 2, head_y, W, grid_y, clock(now + i * 30 * 60), fs_head, COL_WHITE, false, pad)
        else
            -- "more to the right" arrow
            ass:new_event()
            ass:pos(0, 0)
            ass:append("{\\an7\\bord0\\shad0\\1c&" .. COL_WHITE .. "&}")
            ass:draw_start()
            local ax, ay, s = W - pad * 2, head_y + head_h / 2, head_h * 0.25
            ass:move_to(ax, ay - s); ass:line_to(ax + s, ay); ass:line_to(ax, ay + s)
            ass:draw_stop()
        end
    end

    -- Grid background doubles as the gray cell borders
    rect(ass, 0, grid_y, W, grid_y + grid_h, COL_LINE)

    for r, row in ipairs(rows) do
        if r > 4 then break end
        local y1 = grid_y + (r - 1) * row_h
        local y2 = y1 + row_h

        -- Channel cell
        rect(ass, 0, y1 + 1, left_w, y2 - 1, COL_CHAN)
        text(ass, 0, y1, left_w, y2, row.label, fs_cell, COL_WHITE, true, pad)
        rect(ass, left_w, y1, gx0, y2, COL_WHITE)

        -- Program cells: the airing now, then the loop repeating the same video
        local t = 0
        local first = true
        while t < span do
            local len
            if not SCHEDULE then
                len = span            -- one cell for the whole row
            elseif first and row.remaining >= 0 then
                len = row.remaining
            elseif row.duration > 0 then
                len = row.duration
            else
                len = span - t        -- live / unknown: one cell fills the row
            end
            local x1 = math.floor(gx0 + t * pxs)
            local x2 = math.min(W, math.floor(gx0 + (t + len) * pxs))
            if x2 - x1 > 2 then
                local current = (r == 1 and first)
                rect(ass, x1 + 1, y1 + 1, x2 - 1, y2 - 1, current and COL_NOW or COL_CELL)
                text(ass, x1 + 1, y1, x2 - 1, y2, row.title, fs_cell,
                     current and COL_DARK or COL_WHITE, false, pad)
            end
            t = t + math.max(len, 60)
            first = false
        end
    end

    ov.data = ass.text
    ov:update()
end

local function hide_epg()
    if hide_timer then hide_timer:kill(); hide_timer = nil end
    ov:remove()
end

local function show_epg(ch_label, title, guide)
    if hide_timer then hide_timer:kill(); hide_timer = nil end
    draw_epg(ch_label, title, guide)
    hide_timer = mp.add_timeout(SHOW_SECS, function()
        ov:remove()
        hide_timer = nil
    end)
end

mp.register_script_message("show-epg", function(ch_label, title, guide)
    last = { ch_label, title, guide }
    show_epg(ch_label, title, guide)
end)

mp.register_script_message("hide-epg", hide_epg)

mp.register_script_message("cache-epg-info", function(ch_label, title, guide)
    last = { ch_label, title, guide }
end)

-- Moving the mouse brings the guide back (or extends it if already showing)
mp.observe_property("mouse-pos", "native", function(_, pos)
    if not pos or not last then return end
    if hide_timer then
        hide_timer:kill()
        hide_timer = mp.add_timeout(SHOW_SECS, function()
            ov:remove()
            hide_timer = nil
        end)
        return
    end
    show_epg(last[1], last[2], last[3])
end)
