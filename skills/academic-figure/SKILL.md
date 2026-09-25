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

1. **Inspect first.** For an existing figure call `inspect_document`
   (`include_geometry` defaults to true — you get `page_size_bp` and every
   object's `bbox` in API coords). For a new figure call `create_document`
   with a style preset (`list_presets`) —
   optionally `template=` (system_overview / algorithm_pipeline /
   parallel_workers) for a starter layout and `palette=` (paper-muted /
   paper-vivid / paper-ocean / paper-monochrome) — then decide node roles,
   information flow, and grouping up front.
   - Never clear/rebuild an existing figure without an explicit reason;
     apply local edits to stable ids.

2. **Preserve user content.** Keep user-specified terminology, formulas,
   edge directions and numbers verbatim. Do not add technical claims or
   decorations that were not requested. Unknown Ipe objects and manual edits
   are preserved by the tool — do not delete `unmanaged` objects.

3. **Batch-create semantically.** Use one `apply_operations` batch per
   logical change: `node.create` (shape + label + role), `edge.create` with
   `source`/`target` node ids and port sides — never floating arrow
   endpoints. `routing.mode="auto"` picks straight for row/column-aligned
   nodes and orthogonal otherwise; use explicit modes when you need control.
   Where two managed edges cross, the later (higher z-order) edge gets a
   hop-over arc automatically; hops are recomputed on every reroute.
   `icon.create` places curated native-path glyphs (`list_presets "icon"`;
   database, cloud, gear, server, user, lock, …) inside a bounding box —
   group it with a node via `objects.group` when it should move together.
   Set `request_id` to a fresh unique string and pin
   `expected_revision` to the revision from the last response (CLI: pass
   `--revision latest` to take the file's current revision).

   **Placing things.** `box`/`x`/`y` fields are absolute placement.
   `objects.move_to` moves an object so its bbox `top_left` (or `center`)
   lands on a target point — prefer it for repositioning.
   `objects.translate` is a *relative* delta and `node.update`'s
   `changes.box` is absolute move+resize. Never read raw `<path>`/`<text>`
   coordinates out of the `.ipe` XML: those are Ipe-native (bottom-left
   origin, y up, possibly inside a transform matrix). Everything the API
   reports or accepts — `inspect`, `lint`, ops — is API space.

   **Ops payload.** `operations` is a bare JSON array. The CLI accepts
   `@ops.json`, a plain `ops.json` path, `-` for stdin, or inline JSON
   (`{"ops": [...]}` wrapper also works). Example minimal batch:

   ```json
   [{"op": "node.create", "id": "cache", "shape": "rect",
     "box": {"x": 160, "y": 60, "width": 80, "height": 36},
     "label": {"text": "cache", "mode": "plain"}},
    {"op": "edge.create", "id": "e_a_c", "source": {"node": "a"},
     "target": {"node": "cache"}, "routing": {"mode": "orthogonal"}}]
   ```

4. **Measure text, then fit the box.** After text-bearing ops, labels are
   LaTeX-measured by the pipeline. If lint reports `TEXT_OVERFLOW` risk,
   grow the node box or rephrase the label — never shrink font size to fit.

5. **Lint and preview.** Apply results already embed `warnings` for the
   objects you touched (off-page, overlap, text overflow — with a `fix`
   hint when a `polish_figure` safe-fix exists). `lint_figure` →
   `render_preview` → look at the preview at final size. CLI:
   `apply --lint --preview out.png --annotate` does the whole loop in one
   step; `--annotate` draws the API-coord grid and object ids on the PNG so
   you can plan positions by looking, not by decoding XML.
   `polish_figure` fixes: `fit_on_page` (nudge back inside page bounds),
   `wrap_texts` (rewrap over-wide plain-mode texts),
   `grow_nodes_to_label`, `recenter_labels` (plan only),
   `clear_stale_routes` (plan only). Polish plans are computed on the
   current snapshot — lint again after applying. Fix only verifiable
   problems; repeat at most twice, then report remaining warnings instead
   of looping forever.

6. **Export and report.** `export_figure` produces pdf/svg/png under a
   generation directory with a manifest. Report `remaining warnings`, items
   lint could not check (`not_checked`), and anything skipped — do not claim
   untested behavior as ready.

## CLI quick reference

| MCP tool | CLI |
|----------|-----|
| `inspect_document` | `ipe-bindcraft inspect fig.ipe` (geometry on by default; `--no-geometry` to skip) |
| `apply_operations` | `ipe-bindcraft apply fig.ipe ops.json --revision latest --request-id r1 [--lint] [--preview out.png --annotate]` |
| `lint_figure` | `ipe-bindcraft lint fig.ipe` |
| `polish_figure` | `ipe-bindcraft polish fig.ipe fit_on_page --revision latest --request-id p1 --apply` |
| `render_preview` | `ipe-bindcraft preview fig.ipe -o fig.png [--annotate]` |
| `get_request_status` | `ipe-bindcraft status fig.ipe` (revision, objects, journal) |

Every CLI command prints one JSON object to stdout (logs to stderr);
exit codes are 0 ok / 2 business error / 3 usage error.

## Journal / request ids

Each `.ipe` has a sibling `<file>.ibc-journal` (append-only). Every
`apply`/`route`/`layout`/`polish` call must carry a unique `request_id`;
repeating a `request_id` with the same payload replays the stored result
(idempotent retry), with a different payload it fails as
`REQUEST_ID_REUSED`. A request recorded as `started` but never `committed`
means the previous attempt crashed mid-commit — surface it, inspect the
document, and retry with a NEW `request_id` (`get_request_status` /
`ipe-bindcraft status`).

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

- **API coordinates are top-left origin, +x right, +y down, units bp.**
  This applies to everything the tools accept or report: `inspect`
  bboxes, `lint` findings, all ops. Ipe-native bottom-left y-up
  coordinates exist only inside the raw `.ipe` XML and in group `matrix`
  attributes — reading those numbers as API coords (or vice versa) is the
  single most common way to misplace objects. When unsure, run
  `preview --annotate` and look at the grid.
- Single page, single view documents only.
- Paths must stay ASCII when the figure will be processed by Ipe CLI tools
  on this Windows build (7.2.29 cannot open non-ASCII argv paths).
