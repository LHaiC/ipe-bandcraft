# ipe-bindcraft

Windows-first [Ipe](https://ipe.otfried.org/) MCP server for academic paper figures.

An MCP (Model Context Protocol) server that lets an agent create and iteratively edit **native Ipe vector figures** through a small set of semantic, batched, transactional operations — while the `.ipe` file stays the single authoritative source and human edits in Ipe are preserved.

Status: v0.1 development. Not a published PyPI package. See `docs/capability-report.md` and `docs/acceptance-report.md` for what is actually verified on the target machine.

## Install

```text
pip install uv          # if uv is not installed
uv sync --locked
```

## CLI

```text
ipe-bindcraft doctor --json                      # discover Ipe/TeX/SDK, report capabilities
ipe-bindcraft serve --workspace <directory>      # stdio MCP server
ipe-bindcraft build --input <figure.ipe> --output-dir <directory>
ipe-bindcraft install-ipelet --ipe-root <directory>
```

## MCP client configuration (generic stdio example)

```json
{
  "mcpServers": {
    "ipe-bindcraft": {
      "command": "C:\\path\\to\\venv\\Scripts\\ipe-bindcraft.exe",
      "args": ["serve", "--workspace", "C:\\path\\to\\paper\\figures"]
    }
  }
}
```

## Layout

- `src/ipe_bindcraft/` — application service + FileBackend/LiveBackend
- `ipelet/ipe_bindcraft.lua` — Ipe-side bridge for the Live backend
- `resources/` — style presets and figure templates
- `skills/academic-figure/` — agent skill for the figure-editing loop
- `tests/` — unit, integration (real Ipe/TeX), fixtures, windows_live
- `docs/` — capability report, acceptance report, third-party notices

## License

MIT. See `docs/third-party-notices.md` for bundled/referenced components.
