# ipe-bindcraft

Windows-first [Ipe](https://ipe.otfried.org/) MCP server for academic paper figures.

An MCP (Model Context Protocol) server + CLI that lets an agent create and
iteratively edit **native Ipe vector figures** through semantic, batched,
transactional operations — while the `.ipe` file (file backend) or the bound
Ipe window's document (live backend) stays the single authoritative source
and human edits in Ipe are preserved.

Status: v0.2 development snapshot. Not a published PyPI package. See
`docs/capability-report.md` and `docs/acceptance-report.md` for what is
actually verified on the target machine.

## Requirements

- Windows 10/11 (live backend and the test suite are Windows-only)
- Ipe 7.2.x installed (verified: 7.2.29 via winget)
- A local TeX distribution for text measurement (verified: MiKTeX 26.2 —
  `pdflatex`/`xelatex`/`lualatex` on PATH)
- Python 3.13 + `uv`

## Install

```text
pip install uv          # if uv is not installed
uv sync --locked        # creates .venv with locked deps
```

The console script is then `.venv/Scripts/ipe-bindcraft.exe`.

Optional, for live (GUI) mode — install the bridge ipelet once per user:

```text
.venv/Scripts/ipe-bindcraft.exe install-ipelet
```

This copies `ipelet/ipebindcraft.lua` to `%USERPROFILE%\Ipelets\` (the user
ipelet directory Ipe reads on Windows). It does not modify any global Ipe or
system config.

## CLI

Every subcommand prints JSON on stdout; logs go to stderr.

```text
ipe-bindcraft doctor                 # probe Ipe/TeX/MCP, incl. live_bridge
ipe-bindcraft create fig.ipe --width 504 --height 300 --style paper-default
ipe-bindcraft inspect fig.ipe --geometry
ipe-bindcraft apply fig.ipe @ops.json --revision sha256:... --request-id r1
ipe-bindcraft route fig.ipe edge1 --revision ... --request-id r2
ipe-bindcraft layout fig.ipe n1 n2 --mode align --options '{"edge":"left"}' ...
ipe-bindcraft lint fig.ipe --target-width 252
ipe-bindcraft preview fig.ipe -o fig.png --dpi 150
ipe-bindcraft export fig.ipe --formats pdf svg png -o out/
ipe-bindcraft open fig.ipe --backend live     # launch bound GUI session
ipe-bindcraft install-ipelet
ipe-bindcraft serve                           # stdio MCP server
ipe-bindcraft schema                          # apply_operations JSON Schema
```

## MCP client configuration (generic stdio example)

```json
{
  "mcpServers": {
    "ipe-bindcraft": {
      "command": "C:\\Data\\EDA\\projects\\ipe-bandcraft\\.venv\\Scripts\\python.exe",
      "args": ["-m", "ipe_bindcraft", "serve"]
    }
  }
}
```

`serve` keeps stdout for the MCP protocol only; diagnostics go to stderr.
Business errors are returned as `isError=true` tool results with a stable
JSON `code` (e.g. `REVISION_CONFLICT`, `LATEX_FAILED`, `GUI_BUSY`).

## Live mode

`open_document(path, backend="live")` (MCP) or `open --backend live` (CLI)
launches `ipe.exe` bound to a private session directory. Each
`apply_operations` batch becomes **one native undo item** in that window;
manual edits in the GUI are adopted as authoritative and conflicting batches
return `REVISION_CONFLICT`. See `docs/acceptance-report.md` §M3.

## Layout

- `src/ipe_bindcraft/` — service layer + FileBackend/LiveBackend + compiler
- `ipelet/ipebindcraft.lua` — Ipe-side bridge for the Live backend
- `resources/` — style presets and figure templates
- `skills/academic-figure/SKILL.md` — agent skill for the editing loop
- `tests/` — unit, integration (real Ipe/TeX), fixtures, windows_live
- `docs/` — capability report, acceptance report, third-party notices

## Known limitations

- Ipe 7.2.29 CLI tools cannot open paths containing non-ASCII characters on
  this zh-CN Windows build; the service works around this internally with
  ASCII temp dirs, but a document whose *own* path is non-ASCII cannot be
  rendered/exported by `iperender`. Pure-ASCII paths are required for now.
- Single page, single view documents only.
- See `docs/acceptance-report.md` for the full pass/fail/skip matrix.

## License

MIT. See `docs/third-party-notices.md` for bundled/referenced components.
