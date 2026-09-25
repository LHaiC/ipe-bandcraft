# Improvement backlog — dogfooding findings

Source: Devin CLI session `breezy-tiglon` (2026-09-25, transcript
`%APPDATA%\devin\cli\transcripts\breezy-tiglon.json`). An agent used
`ipe-bindcraft` CLI to add **one node + two edges** to `arch.ipe` in a
consumer repo. The task took ~70 steps; roughly 55 were lost to tooling
friction, not real design work. This list turns each observed failure into
a fixable issue.

Priority: **P0** = removes a whole class of wasted steps · **P1** = high
value, small change · **P2** = structural improvement · **P3** = polish.

Status: **all items implemented** (IMP-01 – IMP-17). Notable extra finds
during implementation: `layout --apply` was already broken
(`ObjectsTranslate` built without its `op` discriminator); managed ids are
now embedded in plaintext inside `custom` (`ibc1:<id>:<b64>`), which
survives Ipe's save — the separate `name` attr is stamped too but is
best-effort since Ipe strips unknown attributes on measure passes.

---

## IMP-01 (P0) `apply` rejects bare ops filenames with a misleading error

- **Evidence:** `ipe-bindcraft apply arch.ipe ops_v5.json --revision ... --request-id ...`
  returned `{"error":{"code":"INTERNAL","message":"Expecting value: line 1
  column 1 (char 0)"}}` three times (steps 65/68/70). The agent only found
  the `@file` prefix requirement by reading `_load_ops` source
  (`src/ipe_bindcraft/cli.py:34`).
- **Root cause:** `_load_ops` treats any non-`@`/non-`-` spec as an inline
  JSON string, so a filename hits `json.loads` → `JSONDecodeError` →
  generic `INTERNAL`.
- **Fix:** if `spec` doesn't start with `@` and `Path(spec).exists()`,
  either read it directly or fail with a usage error:
  `ops must be a JSON array, @file, or "-" for stdin (did you mean
  @ops_v5.json?)`. Catch `JSONDecodeError`/`FileNotFoundError` and map to
  exit code 3 (usage), not `INTERNAL`. Document the contract in the
  `apply --help` epilog.

## IMP-02 (P0) `inspect` gives no geometry by default → users hand-parse XML

- **Evidence:** `inspect arch.ipe` returned objects with no positions
  (steps 51–52). The agent then wrote regex + base64-decoding scripts
  against `custom="ibc1:..."` attributes to recover bboxes
  (steps 53–56, 78, 90–99) — and read *Ipe y-up* file coordinates, which
  seeded the whole coordinate confusion below.
- **Root cause:** `inspect_document(..., include_geometry=False)` is the
  default (`service.py:225`); the CLI `--geometry` flag exists
  (`cli.py:73`) but the agent never discovered it.
- **Fix:** include `bbox` unconditionally in CLI `inspect` output (it is
  already API-space via `combined_matrix`), or at minimum print a hint in
  the default output: `pass --geometry for bounding boxes`. Add the CLI
  flag to SKILL.md (it currently documents only the MCP signature
  `inspect_document(include_geometry=true)`).

## IMP-03 (P0) Coordinate-frame confusion: file y-up vs API y-down vs group matrix

- **Evidence:** ~35 steps (79–111). The agent read raw path coords
  (`homepages y:96-136`), concluded `node.create` had ignored its
  `box={y:284}` — **false**, the box was honored (`compiler.py:304`,
  `coordinates.py:114`); it then drove `objects.translate` with deltas
  computed in file space while the API expects y-down (`compiler.py:656`
  does `mat_translate(dx, -dy)`), overshot off-page (lint `y=472`),
  reversed, got zeroed matrices, and finally resorted to `preview.png`
  to derive that `dy=-372` was needed.
- **Root cause:** three frames (Ipe file y-up, API y-down, lint/API
  boxes) coexist; nothing in tool output names the frame, and raw XML
  invites misreading. SKILL.md states the contract but cannot prevent the
  XML detour that IMP-02 caused.
- **Fix:** (a) IMP-02 keeps agents out of the XML; (b) append a frame note
  to lint findings, e.g. `Box(...) in API coords: top-left origin,
  y down, bp`; (c) add `description` fields to the schema for
  `ObjectsTranslate.dx/dy`, `Box`, `TextCreate.x/y` stating the API
  frame; (d) document in SKILL.md that `objects.translate` is *relative*
  and `node.update.box` is *absolute* (see IMP-06).

## IMP-04 (P0) `academic-figure` skill is invisible to consumer projects

- **Evidence:** the session's `available_skills` listed only the consumer
  repo's own skill — `skills/academic-figure/` inside *this* repo is
  never discovered when cwd is the project that merely *uses* the binary.
  The agent had zero documentation and reverse-engineered everything.
- **Fix:** add `ipe-bindcraft install-skill <repo>` (copies
  `skills/academic-figure/` into `<repo>/.agents/skills/` and/or
  `.devin/skills/`) and document manual installation in README.

## IMP-05 (P0) Ops schema is undiscoverable; agent guessed a wrong format first

- **Evidence:** first ops file used `{"expected_revision": ..., "ops":
  [{"op":"node.add","position":...,"size":...}]}` (step 57) — invented
  op names and a wrapper object. Only after reading an old `ops_v4.json`
  did the correct shape emerge: a **bare array** of `node.create` /
  `edge.create` with `box` and `label:{mode,text}` (step 66–67).
- **Fix:** `apply --help` epilog + SKILL.md: one complete minimal example
  (`node.create` + `edge.create` + `node.update.box`), state plainly
  "ops is a bare JSON array; revision is the `--revision` flag, not a
  file field". Consider accepting the wrapper form `{"ops": [...],
  "expected_revision": ...}` as a tolerated superset.

## IMP-06 (P1) Absolute positioning exists but was never found

- **Evidence:** the agent needed "put node at x,y" and burned ~30 steps on
  relative `objects.translate`. `node.update.changes.box` already does
  absolute placement: rewrites the body path in final coords, clears the
  group matrix, preserves label offset, validates `inside_page`
  (`compiler.py:480-511`).
- **Fix:** schema `description` on `NodeUpdateChanges.box` ("absolute
  placement in API coords; prefer over objects.translate for
  positioning"); SKILL.md example. Optionally add `objects.move_to`
  {ids, x, y, anchor?} as a batch-friendly alias.

## IMP-07 (P1) Object ids are ungreppable — agent thought writes were lost

- **Evidence:** `grep homepages arch.ipe` → empty; ids live only inside
  base64url `custom="ibc1:..."` attributes. The agent concluded the apply
  "didn't persist" and spent ~10 steps (92–99) inspecting the journal and
  decoding every `custom` blob before finding the node.
- **Fix:** emit a redundant plaintext identity attribute alongside
  `custom` (e.g. `name="homepages"` or `data-ibc-id`) on managed
  group/text/path elements — `custom` is already non-standard and survives
  Ipe round-trips (A03), so a second attribute should too; verify in Ipe.
  Fallback: document that ids are base64 and `inspect` is the source of
  truth.

## IMP-08 (P1) `apply` reports `warnings: []` even when the commit collides

- **Evidence:** `node.create` placed `homepages` overlapping
  `orcid`/`s2` — correct per spec — but the apply result showed
  `"warnings": []`; the collision surfaced only via a separate `lint`
  run (step 77).
- **Fix:** run the cheap checks (`off_page`, `node_overlap`,
  `text_overflow`) on changed objects post-commit and put findings into
  the existing `warnings` field — or add `apply --lint` to embed a lint
  summary. One round-trip instead of three.

## IMP-09 (P1) `preview` works, but a coordinate overlay would make it a planning tool

- **Evidence:** `preview.png` was the ground truth that finally resolved
  the y-flip confusion (steps 110–113) — the multimodal loop is the
  strongest safety net.
- **Fix:** `preview --annotate` renders a light API-space grid (rulers or
  50 bp ticks) plus object-id tags, so an agent can plan placement from
  the image itself. Optionally have `preview` also return the object
  bboxes JSON alongside `written`/`bytes`.

## IMP-10 (P1) Revision ergonomics: no way to ask "what is current"

- **Evidence:** agent ran `ipe-bindcraft rev` (invalid choice, step 58)
  and later `--revision CURRENT` (rejected, step 106). Every `apply`
  required a fresh `inspect` purely to harvest the revision string.
- **Fix:** accept `--revision latest` (or make the flag optional for the
  file backend, defaulting to `file_revision`), and/or add a
  `ipe-bindcraft status <path>` printing `{revision, page_size_bp,
  object count, journal pending}`.

## IMP-11 (P2) lint message wording misleads on borderline fits

- **Evidence:** `homepages label box 121.8x8.0bp exceeds node
  122.0x40.0bp` — 121.8 < 122 does not "exceed"; the warn is triggered by
  the `pad=2` margin (`quality.py:63-68`). The agent (correctly)
  dismissed it, but the wording invites misjudgment.
- **Fix:** reword: `label 121.8bp + 4bp padding > node 122bp; widen node
  or shorten label` and add a `fix` hint field (see IMP-12).

## IMP-12 (P2) lint findings don't point at `polish`; `polish` can't fix what was found

- **Evidence:** the `note` footnote is 692bp wide on a 640bp page —
  `off_page` warn since before this session, and the rendered PNG
  confirms the text is visually clipped at the right edge. `polish`'s
  `SAFE_FIXES` (`quality.py:133`) has nothing for it, and lint findings
  carry no pointer to existing fixes (`text_overflow →
  grow_nodes_to_label` exists but is never suggested).
- **Fix:** add a `fix` field to findings where a SAFE_FIX applies; add a
  safe fix for over-wide texts — `wrap_text_to_page` (insert line break /
  shrink via `text.update` position) or `fit_text_box` for TextObj.

## IMP-13 (P2) `changed_ids` contains duplicates

- **Evidence:** a batch of `objects.translate` + `node.update` on the same
  id returned `"changed_ids": ["homepages", "homepages"]` (step 85).
- **Fix:** dedupe while preserving order in the result builder.

## IMP-14 (P2) `.ibc-journal` is an undocumented surprise

- **Evidence:** the agent discovered `arch.ipe.ibc-journal` while
  debugging "lost" writes (steps 93–94) and had to reverse-engineer its
  JSONL pre/post-revision format.
- **Fix:** document the journal in SKILL.md/README: location, JSONL shape,
  request-id dedup semantics (`deduplicated`, `REQUEST_ID_REUSED`),
  and that it explains why a second identical apply returns the same
  revision.

## IMP-15 (P2) One-shot edit loop to cut round-trips

- **Evidence:** the natural agent loop was `inspect → apply → lint →
  preview → read png`, each a separate process + LaTeX measure (~10 s
  each on Windows).
- **Fix:** `ipe-bindcraft apply ... --lint --preview out.png` performs the
  batch, runs lint, and writes an (optionally annotated) preview in one
  invocation, returning a merged JSON result.

## IMP-16 (P3) Edge-label placement collides in dense regions

- **Evidence:** in the final `arch-r002` render, `verified extras` and
  `deep flag` labels sit inside a small crossing region between
  `coauthors/homepages` and `enrich`; `candidate urls` floats between
  nodes and is ambiguous about which edge it labels.
- **Fix:** router improvement — prefer label anchor points away from
  unrelated edges/nodes; optionally a lint check `edge_label_overlap`.

## IMP-17 (P3) Ops-file litter in consumer repos

- **Evidence:** `ops.json`, `ops_fix.json`, `ops_fix2.json`,
  `ops_list.json`, `ops_v4..v9.json` accumulated in the consumer repo —
  each retry wrote a new file.
- **Fix:** document `apply ... -` (stdin) for one-off batches and/or accept
  ops on a `--ops-json` flag; mention cleanup convention in SKILL.md.

---

## Confirmed non-issues (do not "fix")

- `node.create` honors `box` exactly (API coords); the session's
  "box ignored" conclusion was a misread of Ipe y-up file coords.
- `node_overlap` warnings were **correct** — the requested position did
  collide with `orcid`/`s2`; the agent's intended slot had no free space.
- Revision dedup by `request_id`, journal, atomic candidate/commit, and
  `needs_route` re-routing all behaved as designed.
- `lint → preview` multimodal verification loop works; `export` produced
  pdf/svg/png + manifest cleanly.

## Session metric

~70 agent steps for one node + two edges. With IMP-01/02/03/05/06 in
place the same task should be ~10 steps: `inspect --geometry` → one
`apply` batch (`node.create` with correct API-space box, two
`edge.create`) → fix any inline warning → `preview`/`export`.
