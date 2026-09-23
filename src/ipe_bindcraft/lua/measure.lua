-- ipe-bindcraft helper for ipescript:
--   ipescript measure <in.ipe> <out.ipe>
--
-- Runs LaTeX on the document's text objects and saves an XML .ipe with the
-- measured width/height/depth attributes. This replaces the documented but
-- non-functional-in-7.2.29 `ipetoipe -xml -runlatex` path (which emits PDF).
--
-- Exit status: writes "ibc-measure-ok" or "ibc-measure-fail ..." to stderr.

local inname = argv[1]
local outname = argv[2]
if not inname or not outname then
  io.stderr:write("ibc-measure-fail usage: ipescript measure <in> <out>\n")
  os.exit(2)
end

local doc = ipe.Document(inname)
if not doc then
  io.stderr:write("ibc-measure-fail cannot parse " .. inname .. "\n")
  os.exit(1)
end

local ok, err = pcall(function () return doc:runLatex() end)
if not ok or err == false then
  io.stderr:write("ibc-measure-fail latex: " .. tostring(err) .. "\n")
  os.exit(3)
end

doc:save(outname)
io.stderr:write("ibc-measure-ok\n")
