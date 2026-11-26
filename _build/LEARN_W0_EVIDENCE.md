# BrewSLM Learn, wave 0: evidence (2026-11-26 ET)

Where every number on `learn/rag/chunking-strategies.html` (and the hub and track pages) comes from, and how to
reproduce it. Paths are relative to the website repo unless they say otherwise.

## Commands

```bash
python3 _build/learn_snippets.py setup                      # venv from _build/learn-requirements.txt (CPU torch wheel)
python3 _build/learn_snippets.py run                        # rerun every learn page's blocks, compare with the page
python3 _build/learn_snippets.py run --fresh --show learn/rag/chunking-strategies.html
python3 _build/site.py build && python3 _build/site.py check
python3 -m unittest discover -s _build -p 'test_*.py'
```

The runner sets `CUDA_VISIBLE_DEVICES=""` and keeps the venv, the Hugging Face cache and one working folder per
page under `~/.cache/brewslm/learn/` (override with `BREWSLM_LEARN_CACHE`). The working folder keeps the downloaded
PDF between runs; `--fresh` empties it so the PDF is downloaded again.

Last run: 2026-11-26, 7 of 7 blocks matched, about three minutes wall clock (measure about 170 s) on a 20-core
aarch64 CPU, no GPU. A passing run writes `_build/learn_stamps.json` (a hash of the code, the shown output with nd spans masked and the
data files, plus the date and versions), which `site.py check` compares with the page. The model was already in the cache; the first run also downloads it (about 90 MB).

## Environment

| What | Value | Source |
|---|---|---|
| Python | 3.12.3 | `sys.version` in the venv (the runner checks the Tested-on line against it) |
| Packages | torch 2.5.1 (CPU wheel), numpy 2.1.3, sentence-transformers 3.3.1, transformers 4.46.3, tokenizers 0.20.3, huggingface_hub 0.36.2, rank-bm25 0.2.2, pypdf 5.1.0 (+ faiss-cpu 1.9.0.post1 and four transitive pins, unused by this page) | `_build/learn-requirements.txt`; the runner checks each `data-pkg` span against `importlib.metadata` |
| Embedding model | `sentence-transformers/all-MiniLM-L6-v2`, Hub revision `1110a243fdf4706b3f48f1d95db1a4f5529b4d41`, `max_seq_length` 256 | the HF cache's `refs/main`; `model.max_seq_length` printed 256; pinned with `revision=` in the page's code |
| CPU | 20 cores, aarch64 ("20-core Arm" on the page) | `nproc`, `uname -m` |

## Corpus

- IRS Publication 17 (2025), `https://www.irs.gov/pub/irs-prior/p17--2025.pdf` (the page's URL; the current-year
  `irs-pdf/p17.pdf` served the same bytes on 2026-11-26), downloaded 2026-11-26: 3,088,632 bytes,
  SHA-256 `2d2381d62c7c77ed4b64b83ac23c28ed85b7c8304eb580c09bd9fd660be0840b` (the page prints the first 16 hex
  digits, so a new IRS edition shows up as a snippet mismatch after `--fresh`). Cover: "Publication 17 (2025)
  Catalog Number 10311G, Jan 13, 2026".
- "All material in this publication may be reprinted freely" is on PDF page 2.
- 142 pages, 157 blank-line blocks in pypdf's raw text, 952,548 characters after cleaning: printed by the `load`
  block.

## The questions

- `learn/rag/data/pub17-chunking-questions.json`: 30 single-fact questions, each with a verbatim `evidence` span from
  the cleaned text and the PDF page of its first occurrence (pages 4 to 105). 28 spans occur once; q09 occurs twice
  and q15 three times, and a chunk holding any whole occurrence counts.
- How they were made: drafted by the AI agent that built this page (the page says "drafted with an AI assistant")
  while reading the cleaned text, phrased as a person would ask (paraphrased, not copied from the span), and checked
  by script that every span occurs in the text. The set was frozen before the first retrieval run. No model generated
  them from a prompt, and no benchmark values are involved. The reviewer read the context of all 30 spans and
  confirmed each answers its question.

## Changes after the first run (all disclosed here; the rule change is also on the page)

1. `recursive_split` was rewritten once (the first version left 1- and 7-character fragments on the sample).
   First version, half-span rule: dense top 5 24/22/23, ~2k chars 27/21/18, BM25 top 5 16/20/20 (256/512/1,024).
2. After the review (round 1): the relevance rule changed from "the chunk holds at least half of the span" to "the
   chunk holds the whole span", because half a span can omit the value that answers the question (q23, q24, q26).
   Under the old rule the page's headline was 27 vs 18 at the same budget, exact McNemar p = 0.012; under the new
   rule it is 25 vs 18, p = 0.065, and the page now calls it suggestive, not significant.
3. Also after review (round 1): q15 reworded from "this year" to "for 2025" (its evidence is the 2025 limit); fixed-size,
   fixed-size with overlap and sentence packing at 256 were added to the measurement; the budget column now prints
   the characters actually returned; the PDF URL moved to the IRS prior-year archive (`irs-prior/p17--2025.pdf`,
   same bytes and SHA-256 as the file first downloaded); the model revision is pinned in the code.
4. Round 2 (wording and tooling only, no number changed): the stamp now hashes code, shown output (nd spans
   masked) and the questions file and checks the Tested-on versions; the sentence-packing and boundaries claims were
   corrected and hedged; the questions file's source URL now matches the page.

## Numbers on the sample page

| Claim | Value | Source |
|---|---|---|
| Sample text length, chunk lengths and boundaries | 659 chars; outputs as shown | blocks `fixed`, `overlap`, `sentence`, `paragraph`, `recursive` |
| Overlap costs 25% more chunks | step 160 for size 200 / overlap 40: 200/160 = 1.25 | arithmetic on the code |
| Chunks, over 256 word pieces | recursive 4,678/0, 2,170/89, 1,057/138; fixed 3,721/0; fixed+40 4,410/0; sentence 3,974/23 | `measure` block |
| Dense top 5; dense at budget (k, mean chars returned); BM25 top 5 | recursive 256: 22, 25 (8, 1,604), 14; 512: 21, 20 (4, 1,754), 19; 1,024: 23, 18 (2, 1,812), 19; fixed 256: 21, 21 (8, 2,048), 14; fixed+40: 19, 21 (8, 2,048), 15; sentence 256: 22, 25 (8, 1,663), 14 | `measure` block |
| 9 vs 2, p = 0.065 (dense, recursive 256 vs 1,024 at budget); 6 vs 1, p = 0.125 (BM25 top 5, 512 vs 256) | two-sided exact binomial on the discordant pairs | `measure` block |
| Example miss | "My refund hasn't shown up yet. Can I send in a second original return…": found with 256, not with 1,024, whose top hit was the direct-deposit passage | `measure` block |
| "86 of them are on the tax table, worksheet and rate-schedule pages (113 to 127)"; "at 512, all 89 are on tax-table pages (114 to 124)" | PDF pages of the over-limit chunks: at 1,024, 86 of 138 on pages 113 to 127 (tax table 113-124, computation worksheet 125-126, rate schedules 127), the other 52 on prose pages 2 to 112, none in the index (132-141); at 512, all 89 on pages 114 to 124 | the one-off script below (not on the page); confirmed by the reviewer |
| "Sentence packing also left 23 chunks over the model's limit, because the tax tables and the index have no sentence ends" | of the 23, 13 on tax-table / rate-schedule pages 113-127 (up to 3,923 tokens), 9 on index pages 132-140, 1 on page 108 | round-2 reviewer's one-off check (pack(sentences(text), 256) with the pinned tokenizer) |
| "Boundaries looked more useful than overlap" | recursive 25, sentence 25, fixed 21, fixed+40 21 of 30 at the same budget; no significance test, and the page says so | `measure` block |
| Model trained on inputs of at most 128 tokens; truncates over 256 word pieces | "The sequence length was limited to 128 tokens"; "input text longer than 256 word pieces is truncated" | all-MiniLM-L6-v2 model card (README in the HF cache) |
| 403 of 467 legal passages began mid-word | docstring of `chunk_text` | BrewSLM repo `backend/app/services/cleaning_service.py` (main at 7be6149, line 159) |
| 173 of 1,103 passages carried page furniture | first clean of Pub 17 in BrewSLM | `blog-irs-pub17-fine-tune-lost-retrieval-won.html`, Step 2 |
| In BrewSLM: whole sentences, at most 1,000 chars by default, up to 100 carried over; the documents → Q&A sweep covers passage size | `chunk_text(text, chunk_size=1000, overlap=100)`; `docs/documents-qa-flow.html` ("number of passages, reranker, candidate pool, passage size") | BrewSLM repo at 7be6149; docs page |
| Practice answer 3 (overlap reconstruction) | `chunks[0] + "".join(c[overlap:] for c in chunks[1:]) == text` held on 2,000 random texts, sizes and overlaps (reviewer: 3,000) | one-off checks |

One-off script for the page numbers of over-limit chunks (run in the snippet venv, after the page's blocks have
defined `recursive_split` and `text`):

```python
from transformers import AutoTokenizer
tok = AutoTokenizer.from_pretrained("sentence-transformers/all-MiniLM-L6-v2")
for size in (512, 1024):
    pos, pages = 0, []
    for c in recursive_split(text, size):
        if len(tok(c.strip())["input_ids"]) > 256:
            pages.append(text[:pos].count("\n\n") + 1)
        pos += len(c)
    print(size, len(pages), sum(113 <= p <= 127 for p in pages), min(pages), max(pages))
```

## Hub and track pages

No numbers beyond the page counts ("1 page"). The track list and planned RAG topics come from the wave plan; the hub
names only pages that exist in its A-Z list, and `check` fails if a learn page is missing from the hub or its track.
