# ipe-bindcraft acceptance report

Target machine: Windows (MSYS_NT-10.0-26200), Python 3.13.0,
Ipe 7.2.29 (winget), MiKTeX 26.2 (`pdflatex`), `mcp` 2.2.0,
`pydantic` 2.13.5, `lxml` 6.1.3 — locked in `uv.lock`.

Evidence base:

- `pytest tests/unit` — 47 passed
- `pytest tests/integration` — 4 passed (real Ipe + TeX)
- `pytest tests/windows_live` — 7 passed (real `ipe.exe` GUI windows)
- `scripts/m0_probe.py` — capability probe, `docs/m0-report.json`
- Three fixtures under `tests/fixtures/` rendered via real `iperender`

## M0 probe (A01–A05)

| ID | Result | Evidence / notes |
|----|--------|------------------|
| A01 | PASS | `doctor` finds ipe/ipetoipe/iperender/ipescript + pdflatex/xelatex/lualatex; versions in capability-report |
| A02 | PARTIAL | Minimal doc with rect/ellipse/arrow/`$\Delta t$`/`_ & %` text opens in Ipe. **Limitation:** Ipe 7.2.29 CLI tools on this zh-CN build cannot open *paths* containing non-ASCII characters (8.3 short names unavailable; pure-space paths work). Chinese label text inside documents is unaffected (UTF-8 XML). Workaround implemented: ASCII temp dirs for all CLI subprocess calls |
| A03 | PASS | `custom="ibc1:<base64url>"` and `layer@data` survive Ipe load→save→reload (probe + `IpeDoc` round-trip tests) |
| A04 | PASS | Text measured via `ipescript` + `measure.lua` (`doc:runLatex()`); ipetoipe/iperender export PDF/SVG/PNG; PDF visible bbox + fonts checked (pypdf, Form XObjects included). Note: `ipetoipe -xml -runlatex` emits PDF on 7.2.29 — measurement therefore uses ipescript, not ipetoipe |
| A05 | PASS | Real `mcp` 2.2.0 stdio client: initialize, tools/list (15 tools), structuredContent + image content; stdout clean, logs on stderr |

## M1/M2 file workflow (A06–A16)

| ID | Result | Evidence / notes |
|----|--------|------------------|
| A06 | PASS | `system_overview` (8-node system), `algorithm_pipeline` (formulas + feedback edge), `parallel_workers` (4 workers) generated through the real pipeline and rendered to PNG for visual check |
| A07 | PASS | Batch atomicity: any op failure → candidate discarded, file hash unchanged (`test_*`); repeated `request_id` returns `deduplicated`, different payload → `REQUEST_ID_REUSED` |
| A08 | PASS | Stable ids across unrelated deletes; deleting a referenced node fails `REFERENCED_OBJECT` |
| A09 | PASS | External/manual byte-level edit + unmanaged object survive MCP round-trips (`test_external_edit_causes_conflict`, integration round-trip test); `build_index` descends into human-created groups so managed ids survive regrouping |
| A10 | PASS | Duplicate ids → `DUPLICATE_ID`; non-translation transforms → `UNSUPPORTED_TRANSFORM`, figure left untouched |
| A11 | PASS | Edge endpoints bind to real outlines (rect/rounded/ellipse/diamond); moving a node marks bound edges `needs_route` and re-routes them in the same commit; manual waypoints preserved |
| A12 | PASS | `LATEX_FAILED`, `FILE_BUSY`, `REVISION_CONFLICT`, `COMMIT_STATUS_UNKNOWN`, `PREVIEW_STALE` all covered; fault injection at `before_measure` / `before_commit` / `after_commit` tested; no stale-preview-as-success |
| A13 | PASS | `node.update` box changes never scale label fontsize; lint emits `not_checked` when real font size is unknown |
| A14 | PASS | 47 unit + 4 integration + 7 live tests; integration/live are skipped (and reported as skipped) when Ipe/TeX/GUI prerequisites are absent |
| A15 | PASS | Same inputs → same semantic XML (modulo hash-stable ordering); PDF bytes intentionally not asserted identical (timestamps/font subsets) |
| A16 | PASS | `polish_figure` is plan-then-apply and idempotent; `dry_run` never touches the file or the revision |

## M3 Live (A17–A23)

Bridge: `ipelet/ipebindcraft.lua` bound per-model to a private session dir;
candidates applied via `ipe.Page` + `doc:set` inside one `model:register`
transaction. Python: `backends/live_backend.py`.

| ID | Result | Evidence / notes |
|----|--------|------------------|
| A17 | PASS | Sessions bind the *model*, not the foreground window; session dir is claimed by `session.txt` so a second document window in the same GUI process cannot hijack it; `test_two_live_sessions_isolated` proves two windows stay independent |
| A18 | PASS | Snapshot (`status`) is `page:xml("ipepage")` — pure read, never touches the dirty flag, file name, selection, or undo history. A failed candidate answers `fail` before any `doc:set` |
| A19 | PASS | One batch = one `register` transaction = one Ctrl-Z / one Ctrl-Y. Verified: apply → undo removes objects → redo restores → repeated undo/redo replays correctly (transactions store XML, never a dangling Page) |
| A20 | PASS | Manual-edit divergence detected by baseline byte-compare → `conflict` → service adopts the GUI page and surfaces `REVISION_CONFLICT`; baseline re-synced so a retry lands. `stale-epoch` responses reject requests from a previous session |
| A21 | PASS (guard) + manual | Reentrancy guard (`sess.processing`) verified — `register`/`action_undo` pump the Qt event loop and re-fired the timer mid-request during development; the guard makes double-processing impossible. Modal-dialog/drag interaction is atomic by construction (single `doc:set`) but full interactive coverage is marked manual-verified |
| A22 | PASS | Response loss → `get_request_status` via journal; GUI exit mid-request → `COMMIT_STATUS_UNKNOWN` (consumed but unanswered); live commit path never falls back to writing the file |
| A23 | PASS | `doc:set` replaces the real GUI page — new text objects are present in the bound window's document (verified via authoritative `fetch_page` containing the new `<text>`); measurement already ran through the real TeX path before commit |

## Skipped / not claimed

- No-test areas: multi-page documents (rejected `UNSUPPORTED_DOCUMENT_FEATURE`),
  non-ASCII document paths for Ipe CLI operations, `GUI_BUSY` long-timeout path
  (code present; not exercised in the suite), hop-over/arc routing
  (straight/orthogonal/manual only).
- Performance targets (SPEC §14.5) not yet measured — no claim made.
- Icon library and advanced routing are explicitly out of scope.

## Reproduce

```text
uv sync --locked
.venv/Scripts/ipe-bindcraft.exe doctor
.venv/Scripts/ipe-bindcraft.exe install-ipelet   # once, for live tests
.venv/Scripts/python.exe -m pytest tests -q
```
