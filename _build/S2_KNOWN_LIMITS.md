# S2 known limits (2026-11-23)

- **with-brewslm track skipped.** Its 13 lessons are product walkthroughs already; a box would repeat them.
- **No showcase numbers for distillation, DPO/ORPO, SQL injection or code review.** Those boxes give defaults and
  click paths only and say there is no result of ours. Add numbers if a run is ever published.
- **App-side copy bugs found while checking (not fixed here; app repo is out of scope):**
  - Ship → Package → Options → "Compress first" hint says "GGUF, ONNX, pruning"; there is no pruning action
    (`frontend/src/pages/ProjectShipPage.tsx:130`). The structured-pruning box says so.
  - `backend/data/demo_samples/{sentiment-classifier,pii-detector}/manifest.json` still describe 10- and 25-row
    gold sets; the files have 200 rows since aa04beb. The Open demo tiles may show the stale text.
  - `backend/scripts/bootstrap_sqli_demo.py:35` comment says 9 benign categories; `BENIGN_TEMPLATES` has 10.
  - The app's own copy says "Settings → Advanced → Tools" for domain packs (`DataStudioQualitySafetyPanel.tsx:611`);
    the box says "the Tools drawer on Settings" to match the drawer.
- **`teacher_filter_keep: "correct"`** is API-only (default `"not_wrong"`); the legal tutorial box says so.
- **Unverified in review:** the S1-sourced "7 of 100 no-tool prompts" (tool-call report) reused in project-gallery.
- **`_build/` is served by Pages** (`.nojekyll`), so S1/S2 evidence files with local paths are public. Same as S1;
  excluding `_build/` from Pages is a separate decision for the owner.
- **Line numbers** in S1_EVIDENCE.md (main 808833e) and S2_EVIDENCE.md (c530316) drift as main moves.
- Wording: the reviewer's repetition findings were fixed once (shared disclaimers, "Two …" openers, the setup
  timings duplicated between environment-setup and Tutorial 0, 0.570→0.688 now quoted in two S2 boxes plus S1).
