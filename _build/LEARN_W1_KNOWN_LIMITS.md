# BrewSLM Learn, wave 1: known limits (2026-11-26 ET)

What wave 1 leaves as it is, so the next wave doesn't rediscover it. Wave 0's limits
(`LEARN_W0_KNOWN_LIMITS.md`) still hold unless this file says otherwise.

- **One corpus, small question set.** Every measurement uses IRS Publication 17 (2025) and the same 80 frozen
  single-fact questions (30 from wave 0, 50 added and frozen in this wave before any run). Most gaps between good
  settings are two to five questions, which a paired test on 80 can't separate; the pages say "suggestive" when
  that happens. Numbers across pages are comparable because the corpus, chunks and questions are shared; they are
  not general constants.
- **The questions and answer keys were drafted with an AI assistant**, the same one that built the pages. They are
  frozen and published next to the pages. Questions paraphrase their spans on purpose, which favours dense retrieval
  a little over keyword search compared with real user queries.
- **Answer quality is small-model and crude.** Generation pages use qwen2.5:1.5b-instruct (Q4_K_M) on CPU through
  Ollama, scored by a key-string check on 47 keyed questions (fewer on some pages). The check misses right answers
  worded differently and passes wrong ones that mention the key. A hand read of the what-is-RAG answers is in
  `LEARN_W1_EVIDENCE.md`. Absolute scores say little about larger models.
- **Ollama answers are not bit-for-bit repeatable on this machine.** At temperature 0 with a fixed seed, the same
  prompt gave different single answers between runs (prompt-prefix reuse, and on a busy 20-core CPU even with the
  model unloaded between calls). Pages that generate text unload the model after each call; single answers that
  still changed between the two runs are marked `data-nd="any"` with a title saying so, and counts are compared
  exactly. A rerun on another CPU or Ollama version can change counts by a question or two.
- **Timings are from a shared, heavily loaded machine** (load average 20 to 45 during the runs). They are in nd spans
  and should be read as ratios between rows, not as absolute speeds.
- **The snippet runner is still not in CI** (wave 0 limit). Pages that call Ollama also need a local Ollama with the
  pinned model; the runner points them at its own instance (`BREWSLM_LEARN_OLLAMA`, default `127.0.0.1:6572`).
  A full rerun of the wave takes about two hours on this machine, most of it model calls.
- **Interview pages have no code.** They are checked for structure (15 to 25 questions, real answers, each linked
  to a covering page, a Checked-on date), not for the truth of each number; the numbers were copied from the pages'
  outputs and checked by the reviewer.
- **Embedding fine-tuning is a demonstration.** One epoch of inverse-cloze pairs on CPU, one seed, no
  hyperparameter search; the page reports what it got and holds out every test chunk from training.
- **Chunk-level tags only.** The metadata page tags a chunk with the chapter of the page it starts on; a chunk that
  crosses a chapter break carries one tag.
- **Page length.** Several pages are near or over the 1,500-word warning; `check` warns, it does not fail.
- **Old warnings unchanged:** titles over 60 characters and descriptions over 160 (pre-existing, plus the new
  pages' titles, which follow the wave 0 pattern).
