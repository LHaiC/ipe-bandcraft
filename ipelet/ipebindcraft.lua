----------------------------------------------------------------------
-- ipe-bindcraft live bridge ipelet
--
-- Binds THIS window's model (never the implicit foreground) to a private
-- session directory. The Python side drops request directories containing a
-- candidate <ipepage>; this ipelet applies them through model:register so
-- every change is a native undo item, then writes a response file.
--
-- Menu: Ipelets -> ipe-bindcraft -> {Start, Pause, Apply pending, Stop, Status}
--
-- Auto-bind: if IPE_BINDCRAFT_LIVE_DIR is set in the environment, every newly
-- created model auto-starts a session in that directory.
--
-- Protocol (session dir):
--   session.txt / status.txt     epoch, state, applied, doc
--   baseline.ipepage             last-known authoritative page bytes
--   req-<n>/meta.txt             kind=apply|undo|redo|status, epoch=<n>
--   req-<n>/candidate.ipepage    for kind=apply
--   req-<n>/resp.txt             first line: ok|fail|conflict|stale-epoch
--   req-<n>/page.ipepage         for kind=status: authoritative page bytes
----------------------------------------------------------------------

label = "ipe-bindcraft"

about = [[
Bridge for the ipe-bindcraft MCP server.
Requests are applied as native undoable edits via model:register; no
arbitrary code execution — whole-page candidate replacement only.
]]

-- the ipelet sandbox env does NOT include pcall/io/os: reach them via _G
local io = _G.io
local os = _G.os
local pcall = _G.pcall

local M = {}  -- model -> session

-- ---------------------------------------------------------------------------
-- filesystem helpers (ASCII session dirs only; see capability report)
-- ---------------------------------------------------------------------------

local function write_file(path, text)
  local f = io.open(path, "wb")
  if not f then return false end
  f:write(text)
  f:close()
  return true
end

local function read_file(path)
  local f = io.open(path, "rb")
  if not f then return nil end
  local s = f:read("*a")
  f:close()
  return s
end

local function file_exists(path)
  local f = io.open(path, "rb")
  if f then f:close() return true end
  return false
end

local function append_file(path, text)
  local f = io.open(path, "ab")
  if not f then return end
  f:write(text)
  f:close()
end

local function mkdirs(path)
  -- called only at session start; a console flash here is acceptable
  os.execute('mkdir "' .. path .. '" 2> NUL')
end

local function slist(dir)
  -- ipe.directory: pure C++ call, no console window (unlike io.popen dir /b)
  local ok, entries = pcall(ipe.directory, dir)
  if not ok or not entries then return {} end
  return entries
end

-- ---------------------------------------------------------------------------
-- session protocol
-- ---------------------------------------------------------------------------

local function find_request(sess)
  local best, bestn
  for _, name in ipairs(slist(sess.dir)) do
    local n = name:match("^req%-(%d+)$")
    if n and file_exists(sess.dir .. "\\" .. name .. "\\meta.txt")
       and not file_exists(sess.dir .. "\\" .. name .. "\\resp.txt") then
      n = tonumber(n)
      if not bestn or n < bestn then best, bestn = name, n end
    end
  end
  return best
end

local function parse_meta(text)
  local m = {}
  for line in (text or ""):gmatch("[^\r\n]+") do
    local k, v = line:match("^(%w+)%s*=%s*(.*)$")
    if k then m[k] = v end
  end
  return m
end

local function snapshot_page(model)
  -- pure read: does not touch the dirty flag
  return model:page():xml("ipepage")
end

local function write_status(sess)
  write_file(sess.dir .. "\\status.txt",
    "epoch=" .. sess.epoch ..
    "\nstate=" .. sess.state ..
    "\napplied=" .. sess.applied ..
    "\ndoc=" .. (sess.doc_name or "") ..
    "\n")
end

local function respond(sess, reqname, status, detail)
  write_file(sess.dir .. "\\" .. reqname .. "\\resp.txt",
             status .. "\n" .. (detail or "") .. "\n")
end

local function process_request(model, sess, reqname)
  local rdir = sess.dir .. "\\" .. reqname
  local meta = parse_meta(read_file(rdir .. "\\meta.txt"))

  if tonumber(meta.epoch or "-1") ~= sess.epoch then
    respond(sess, reqname, "stale-epoch", "epoch=" .. tostring(meta.epoch))
    return
  end

  local kind = meta.kind or "apply"
  if kind == "undo" then
    -- the undo stack always carries a dummy entry at position 1
    if #model.undo <= 1 then
      respond(sess, reqname, "fail", "nothing to undo")
    else
      model:action_undo()
      respond(sess, reqname, "ok", "")
    end
    return
  elseif kind == "redo" then
    if #model.redo == 0 then
      respond(sess, reqname, "fail", "nothing to redo")
    else
      model:action_redo()
      respond(sess, reqname, "ok", "")
    end
    return
  elseif kind == "status" then
    write_file(rdir .. "\\page.ipepage", snapshot_page(model))
    respond(sess, reqname, "ok", "applied=" .. sess.applied)
    return
  end

  -- kind == "apply": conflict check against baseline (manual GUI edits are
  -- authoritative — the caller re-syncs and retries on "conflict")
  local cur = snapshot_page(model)
  local baseline = read_file(sess.dir .. "\\baseline.ipepage")
  if baseline and cur ~= baseline then
    -- re-sync the baseline so the caller's retry (compiled on the adopted
    -- page) is not rejected again
    write_file(sess.dir .. "\\baseline.ipepage", cur)
    respond(sess, reqname, "conflict",
            "page changed in GUI since baseline; manual edit detected")
    return
  end

  local xml = read_file(rdir .. "\\candidate.ipepage")
  if not xml then
    respond(sess, reqname, "fail", "missing candidate.ipepage")
    return
  end
  if not ipe.Page(xml) then
    respond(sess, reqname, "fail", "candidate page did not parse")
    return
  end

  -- Store XML, not Page objects: doc:set takes ownership of the page, so a
  -- Page stored on the transaction would dangle after the next undo.
  -- Parsing fresh on each undo/redo keeps the transaction replayable.
  local t = { label = "ipe-bindcraft apply",
              pno = model.pno,
              vno = model.vno,
              original_xml = cur,
              final_xml = xml }
  t.undo = function(t, doc) doc:set(t.pno, ipe.Page(t.original_xml)) end
  t.redo = function(t, doc) doc:set(t.pno, ipe.Page(t.final_xml)) end
  local ok, err = pcall(function() model:register(t) end)
  if not ok then
    respond(sess, reqname, "fail", tostring(err))
    return
  end
  sess.applied = sess.applied + 1
  write_file(sess.dir .. "\\baseline.ipepage", snapshot_page(model))
  respond(sess, reqname, "ok", "")
  write_status(sess)
end

local function apply_one(model, sess)
  -- reentrancy guard: model:register / action_undo pump the Qt event loop
  -- (setPage / ui:explain), which fires this timer again mid-request;
  -- without the guard the same request gets processed twice.
  if sess.state ~= "active" or sess.processing then return end
  local reqname = find_request(sess)
  if not reqname then return end
  sess.processing = true
  local ok, err = pcall(process_request, model, sess, reqname)
  sess.processing = false
  if not ok then
    append_file(sess.dir .. "\\bridge-error.txt",
                reqname .. ": " .. tostring(err) .. "\n")
  end
end

-- timer target object: ipeui.Timer(target, "method") calls target:method()
local function make_poller(model, sess)
  local obj = {}
  function obj.tick()
    apply_one(model, sess)
  end
  return obj
end

local function start(model, dir)
  dir = dir or os.getenv("IPE_BINDCRAFT_LIVE_DIR")
  if not dir then
    dir = (os.getenv("TEMP") or os.getenv("TMP") or ".")
      .. "\\ipe-bindcraft-live\\s" .. os.time()
  end
  mkdirs(dir)
  local sess = {
    dir = dir,
    epoch = os.time(),
    applied = 0,
    state = "active",
    processing = false,
    doc_name = model.file_name or "<unsaved>",
  }
  M[model] = sess
  write_file(dir .. "\\baseline.ipepage", snapshot_page(model))
  write_file(dir .. "\\session.txt",
    "epoch=" .. sess.epoch ..
    "\ndoc=" .. sess.doc_name ..
    "\ndir=" .. dir .. "\n")
  write_status(sess)
  local poller = make_poller(model, sess)
  local timer = ipeui.Timer(poller, "tick")
  timer:setInterval(250)
  timer:start()
  sess.timer = timer
  model.ui:explain("ipe-bindcraft session: " .. dir)
  return sess
end

local function stop(model)
  local sess = M[model]
  if not sess then return end
  if sess.timer then sess.timer:stop() end
  sess.state = "stopped"
  write_status(sess)
  M[model] = nil
  model.ui:explain("ipe-bindcraft session stopped")
end

methods = {
  { label = "Start session" },
  { label = "Pause" },
  { label = "Apply pending" },
  { label = "Stop session" },
  { label = "Status" },
}

function run(model, num)
  local sess = M[model]
  if num == 1 then
    if sess then model.ui:explain("already bound: " .. sess.dir) return end
    start(model)
  elseif num == 2 then
    if not sess then model.ui:explain("no session") return end
    sess.state = "paused"
    write_status(sess)
    model.ui:explain("ipe-bindcraft paused")
  elseif num == 3 then
    if not sess then model.ui:explain("no session") return end
    local was = sess.state
    sess.state = "active"
    apply_one(model, sess)
    sess.state = was
    model.ui:explain("apply pending done")
  elseif num == 4 then
    stop(model)
  elseif num == 5 then
    if not sess then
      model.ui:explain("no ipe-bindcraft session")
    else
      model.ui:explain(string.format(
        "ipe-bindcraft %s | epoch=%d applied=%d dir=%s",
        sess.state, sess.epoch, sess.applied, sess.dir))
    end
  end
end

-- ---------------------------------------------------------------------------
-- auto-bind: wrap MODEL.new so a session starts on each new document window
-- when IPE_BINDCRAFT_LIVE_DIR is set. Models are created AFTER ipelets load,
-- so this hook is the reliable injection point (verified against main.lua).
-- ---------------------------------------------------------------------------

if os.getenv("IPE_BINDCRAFT_LIVE_DIR") and _G.MODEL and _G.MODEL.new then
  local live_dir = os.getenv("IPE_BINDCRAFT_LIVE_DIR")
  local orig_new = _G.MODEL.new
  _G.MODEL.new = function(...)
    local m = orig_new(...)
    -- one session dir = one bound window. If the user opens a second
    -- document in this process it must NOT hijack the bridge (A17).
    if m.ui and not M[m]
       and not file_exists(live_dir .. "\\session.txt") then
      local ok, err = pcall(start, m)
      if not ok then
        write_file(live_dir .. "\\autostart-error.txt", tostring(err))
      end
    end
    return m
  end
end
