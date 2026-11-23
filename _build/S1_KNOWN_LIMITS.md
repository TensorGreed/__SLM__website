# S1 known limits (2026-11-23)

What phase S1 left as it is, so the next pass doesn't rediscover it.

- **Coverage.** 33 of the ~80 Academy lessons have a "Do this in BrewSLM" box. Not done: the most conceptual
  Foundations lessons (neural networks, attention, n-grams to Transformers, architecture taxonomy), the
  remaining SFT and Hands-on lessons, most Advanced lessons, and the With BrewSLM track and tutorials (those
  were already product-specific). The reviewer did not flag any lesson as too generic among the ones left.
- **Tutorials have no previous / next pager.** Lessons in the five course tracks do (the generator checks the
  order); the ten tutorials have breadcrumbs and related links only. Adding a pager means teaching
  `check_sequence` about the tutorials index order.
- **Pages that read as reference but stay in the Academy.** `academy/with-brewslm/training-config-reference.html`
  and `eval-pack-and-failure-cluster-reference.html` overlap the new docs pages in topic. Not merged or
  redirected; they are linked from the docs pages' related lists where relevant.
- **Noindex.** Only `academy/certificate.html` (about 200 words, a browser-local progress list) was set to
  noindex. No other page is under 400 words of real content: the next thinnest are `blog.html` (an index, ~570)
  and the roadmap (~640). The 404 page was already noindex.
- **Unverified in the UI.** Whether the Train button is disabled when a leakage signal reaches "block"
  severity (code shows a data-health signal and a Coach nudge only; docs say "signal, not a refusal").
- **Not on the pages because they don't exist in code:** QLoRA, early stopping (the schema field is unread),
  AWQ / GPTQ, an inline TensorRT converter. Each docs page that touches them says so.
- **Model names.** New text names base checkpoints only where a screenshot or published post already does
  (SmolLM2-135M on the demo, DeBERTa for the tagger), and the chat-template families BrewSLM can render-check
  (ChatML, Llama 3, Gemma) because they are the app's own labels. Images reused from the blog still show the
  base model ids they were captured with.
- **Tool-call model numbers** (run #221) come from an internal report, not a published post. They are quoted
  with the failed false-call bar (7/100 against a limit of 5).
- **Line numbers** in docs pages and `S1_EVIDENCE.md` are for BrewSLM `main` at 808833e. The pages say so;
  re-check them when main moves.
- **Warnings left:** 57 titles over 60 characters and 96 descriptions over 160 (pre-existing; the new pages are
  within both budgets).
