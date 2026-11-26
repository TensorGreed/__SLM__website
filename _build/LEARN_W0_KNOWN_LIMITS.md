# BrewSLM Learn, wave 0: known limits (2026-11-26 ET)

What wave 0 leaves as it is, so wave 1 doesn't rediscover it.

- **The snippet runner is not in CI.** `_build/learn_snippets.py run` needs the pinned venv and about 90 MB of model
  download, so it runs by hand before publishing. `site.py check` (which CI runs) only compares each page with the
  stamp a passing run left in `_build/learn_stamps.json`: it catches code, shown output (outside nd spans) or data
  files edited without a rerun, a Tested-on version the run did not have, and a Tested-on date later than the last
  passing run; not drift in the libraries since then.
- **One measurement, small.** 30 single-fact questions on one PDF, one small embedding model (all-MiniLM-L6-v2,
  trained on inputs of at most 128 tokens), retrieval only. The headline gap (25 vs 18 at a similar budget) is
  p = 0.065. The relevance rule was tightened after the first review (half span to whole span); both results are on
  the page and in `LEARN_W0_EVIDENCE.md`. A larger question set, a second embedding model and an answer-quality
  check are wave 1 material.
- **The questions were drafted with an AI assistant** while reading the publication (the page says so). They were
  frozen before the first run; one was reworded after review (q15, "this year" to "for 2025").
- **Cleaning is minimal.** Only proof-sheet lines are removed; running footers ("28 Chapter 3 Dependents Publication 17
  (2025)") and extraction artefacts ("Y ou", "T ax") stay in the text. The page mentions the footers; the
  artefacts are noted only in the questions file.
- **Track index is thin.** `learn/rag/index.html` lists one page and a plain list of planned topics. It is indexable;
  if it lands in "discovered, not indexed", consider noindex until the track has three or more pages.
- **Interview-questions pages** (one per track, owner decision 3) are not in wave 0; the hub and track index mention
  them as planned.
- **Word budget.** `check` warns over 1,500 words of prose (numbers, code and output don't count). The sample page is
  just under it; a page with a long measurement will hit it.
- **Nav width.** "Learn" makes eight nav items. Without a fix the header overflowed by about 45 px at 660 px wide,
  so `styles.css` tightens the nav gap under 760 px and 700 px; measured at 641 to 880 px there is no horizontal
  scroll. Under 700 px "Creation Paths" wraps to two lines, as it already did on main. The nav is hidden under 640 px.
- **`_build/` is public** (Pages serves it, as noted in S2_KNOWN_LIMITS). The wave 0 files under `_build/` use no
  absolute local paths; the runner's cache path is described as `~/.cache/brewslm/learn`.
- **Old warnings unchanged:** 57 titles over 60 characters and 96 descriptions over 160 (pre-existing).
