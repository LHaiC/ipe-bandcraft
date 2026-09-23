# Third-party notices

ipe-bindcraft itself is MIT-licensed. It depends on, or interoperates with,
the following components:

## Runtime dependencies (locked in uv.lock)

- `mcp` — Model Context Protocol Python SDK (MIT)
- `pydantic` — data validation (MIT)
- `lxml` — XML processing (BSD; wraps libxml2/libxslt, MIT-style)
- `pypdf` — PDF inspection for export checks (BSD)
- `platformdirs` — platform directories (MIT)

## External tools (not bundled; discovered at runtime)

- **Ipe** 7.2.x — the extensible drawing editor (GPL). ipe-bindcraft invokes
  `ipe`/`ipetoipe`/`iperender`/`ipescript` as separate processes and installs
  a Lua ipelet into the user's Ipelets directory; it does not embed or
  modify Ipe binaries.
- **MiKTeX / TeX Live** — a TeX distribution providing `pdflatex` etc. for
  label measurement.

## Referenced designs (not vendored)

- omnigraffle-bindcraft — conceptual reference for batch operations and
  semantic connections (its macOS automation is not used).

## Bundled

- `ipelet/ipebindcraft.lua` — first-party; installed to
  `%USERPROFILE%\Ipelets\` by `ipe-bindcraft install-ipelet`.
- `src/ipe_bindcraft/lua/measure.lua` — first-party; run by `ipescript`.
