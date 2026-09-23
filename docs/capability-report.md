# M0 capability report — ipe-bindcraft

Date: 2026-09-23 · Host: Windows (MSYS shell), zh-CN locale (ANSI codepage GBK)
Raw evidence: `docs/m0-report.json` (regenerate: `.venv/Scripts/python.exe scripts/m0_probe.py --out docs/m0-report.json`)

## Environment (discovered, not assumed)

| Component | Version | Path | Source |
|---|---|---|---|
| Ipe (ipe/ipetoipe/iperender/ipescript) | **7.2.29** | `C:\Users\lhc\AppData\Local\Microsoft\WinGet\Packages\OtfriedCheong.Ipe_Microsoft.Winget.Source_8wekyb3d8bbwe\ipe-7.2.29\bin` | winget portable scan |
| MiKTeX pdfTeX | 4.26 (MiKTeX 26.2) | PATH | PATH |
| xelatex / lualatex | 4.18 / 1.24.0 | PATH | PATH |
| Python | 3.13.0 | `C:\softwares\python` | system |
| uv | 0.12.18 | pip-installed | pip |
| mcp (Python SDK) | 2.2.0 | venv | uv.lock |
| pydantic / lxml / pypdf | 2.13.5 / 6.1.3 / 6.19.0 | venv | uv.lock |

Ipe configuration (`ipe -show-configuration`):
- ipelet dirs: `<install>\ipelets` and **`C:\Users\lhc\Ipelets`** (user dir; `install-ipelet` targets this)
- LaTeX directory: `C:\Users\lhc\AppData\Local\ipe\`
- default styles for new documents: `basic`

## Probe results

| Check | Result | Evidence |
|---|---|---|
| A01 tool discovery | **pass** | all four Ipe executables + TeX found, versions above |
| minimal .ipe parse (rect/ellipse/arrow/`$\Delta t$`/escaped text) | **pass** | `ipetoipe -xml` round-trip, rc=0 |
| A03 `custom="ibc1:…"` survives ipelib load→save | **pass** | decoded value identical after `ipetoipe -xml` |
| A03 `layer data="ibc1:…"` survives | **pass** | same |
| text measurement | **pass with deviation** | `ipescript measure` → real `width/height/depth` in XML |
| A04 internal PDF / PNG / SVG / publication PDF | **pass** | PNG 525×396 px @150dpi for 252×190bp page; PDF MediaBox == page |
| paths with spaces | **pass** | `ipetoipe` handles them |
| non-ASCII (Chinese) argv paths | **FAIL — limitation** | `Error opening the file` (ANSI codepage mismatch) |
| workaround: relative ASCII name + non-ASCII cwd | **pass** | verified |
| workaround: ASCII staging dir copy | **pass** | verified |
| A05 MCP SDK stdio smoke | **pass** | real `Client`↔`MCPServer` over stdio: initialize, tools/list, structuredContent (`EchoResult` model), ImageContent block |

## Spec deviations discovered (real Ipe 7.2.29 vs SPEC text)

1. **`ipetoipe -xml -runlatex` does not emit XML** — it calls `topdf()` and writes PDF
   (verified in `src/ipetoipe/ipetoipe.cpp` @v7.2.29: `case FileFormat::Xml: if (runLatex) return topdf(...)`).
   - *Impact:* the SPEC §9.2 command template cannot produce measured XML.
   - *Resolution:* measure via bundled `src/ipe_bindcraft/lua/measure.lua` run by
     `ipescript` (`ipe.Document` → `doc:runLatex()` → `doc:save()`). Output XML
     contains `width/height/depth` per text object. Minimal repro: probe check
     `latex_measure` (`ipetoipe_xml_runlatex_produced_pdf: true`).
2. **`ipescript` resolves scripts via Lua `require`** — a script must be a module
   name findable on `package.path` (`./?.lua` works when cwd = script dir), not a
   filesystem path argument.
3. **Non-ASCII argv paths fail** on this zh-CN system (child CRT converts argv to
   ANSI/GBK; Ipe reads it as UTF-8). Both relative-name and staging workarounds
   verified; the service stages all Ipe CLI I/O in ASCII working directories.
4. `mcp` 2.2.0 notes: structured-output tools must return a serializable model
   (Pydantic `BaseModel` works; bare `dict` raises `InvalidSignature`);
   `CallToolResult.is_error` is snake_case.
5. Arrow symbol set in `basic.isy` is `arrow/{arc,farc,ptarc,fptarc,fnormal,
   pointed,fpointed,linear,double,fdouble,mid-*}` — verify `normal` resolves at
   runtime (built-in standard sheet) before relying on it. Probe used
   `arrow="normal/normal"`; ipetoipe accepted it (needs render verification).

## Not yet verified (deferred)

- GUI `ipe.exe` load→save→reload of `custom` metadata (ipelib path verified;
  GUI uses the same ipelib but is scheduled for M3 evidence, A03 residual).
- Timer/`model:register` GUI safety — M3 probes.
- CJK *labels* (needs XeTeX/LuaTeX profile; capability-gated, not claimed).
