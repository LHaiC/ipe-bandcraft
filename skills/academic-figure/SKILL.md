---
name: academic-figure
description: Create and iteratively edit Ipe (.ipe) vector figures for academic papers via the ipe-bindcraft MCP tools — semantic nodes/edges, LaTeX-measured text, native PDF/SVG/PNG export, revision-safe batches.
---

# Academic figure editing with ipe-bindcraft

Use the `ipe-bindcraft` MCP server (or the `ipe-bindcraft` CLI) to build and
revise Ipe figures. The `.ipe` file (file backend) or the bound Ipe window's
document (live backend) is authoritative — never hand-edit managed geometry
around it.

## Workflow

1. **Inspect first.** For an existing figure call `inspect_document` with
   `include_geometry=true`, note `page_size_bp`, existing object ids, and the
   target column width (e.g. 252 bp single column, 504 bp double). For a new
   figure call `create_document` with a style preset (`list_presets`) and
   decide node roles, information flow, and grouping up front.
   - Never clear/rebuild an existing figure without an explicit reason;
     apply local edits to stable ids.

2. **Preserve user content.** Keep user-specified terminology, formulas,
   edge directions and numbers verbatim. Do not add technical claims or
   decorations that were not requested. Unknown Ipe objects and manual edits
   are preserved by the tool — do not delete `unmanaged` objects.

3. **Batch-create semantically.** Use one `apply_operations` batch per
   logical change: `node.create` (shape + label + role), `edge.create` with
   `source`/`target` node ids and port sides — never floating arrow
   endpoints. Set `request_id` to a fresh unique string and pin
   `expected_revision` to the revision from the last response.

4. **Measure text, then fit the box.** After text-bearing ops, labels are
   LaTeX-measured by the pipeline. If lint reports `TEXT_OVERFLOW` risk,
   grow the node box or rephrase the label — never shrink font size to fit.

5. **Lint and preview.** `lint_figure` → `render_preview` → look at the
   preview at final size. Fix only verifiable problems (overflow,
   overlapping selection, stale routes). Repeat at most twice, then report
   remaining warnings instead of looping forever.

6. **Export and report.** `export_figure` produces pdf/svg/png under a
   generation directory with a manifest. Report `remaining warnings`, items
   lint could not check (`not_checked`), and anything skipped — do not claim
   untested behavior as ready.

## Error handling

- `REVISION_CONFLICT` — the source changed (user edit or another writer).
  Re-`inspect_document`, take the new `revision`, and retry the batch.
- `COMMIT_STATUS_UNKNOWN` — a commit may or may not have landed. Call
  `get_request_status`; if it says `started`, inspect the document before
  deciding whether to retry with a NEW `request_id`.
- `DANGLING_EDGE` / `REFERENCED_OBJECT` — fix references inside the same
  batch; deleting a node that edges reference needs an explicit cascade.
- `LATEX_FAILED` — check the LaTeX source in the label (`mode="latex"`) or
  the document preamble; do not silently fall back to unmeasured text.
- `GUI_BUSY` / `BRIDGE_UNAVAILABLE` (live) — the bound Ipe window is busy or
  gone; retry later or reopen the session.

## Live mode

`open_document(path, backend="live")` launches a bound Ipe window.
Prerequisite: `ipe-bindcraft install-ipelet` once per user (installs
`ipebindcraft.lua` into `%USERPROFILE%\Ipelets`). Each batch becomes a
single native Ctrl-Z undo item in that window; manual edits in the GUI are
adopted as the authoritative state and conflicting batches return
`REVISION_CONFLICT`.

## Constraints

- API coordinates are top-left origin, y down, units bp. Ipe-native bottom
  -left y-up conversion is handled internally — never mix the two.
- Single page, single view documents only.
- Paths must stay ASCII when the figure will be processed by Ipe CLI tools
  on this Windows build (7.2.29 cannot open non-ASCII argv paths).
