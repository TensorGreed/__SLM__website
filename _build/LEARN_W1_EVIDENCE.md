# BrewSLM Learn, wave 1: evidence (2026-11-26 ET)

Where the numbers on the wave 1 pages (`learn/rag/*`, `learn/embeddings/*`) come from, what was decided along the
way, and how to reproduce it. Paths are relative to the website repo. Wave 0's evidence
(`LEARN_W0_EVIDENCE.md`) still covers the chunking page and the corpus download.

## Commands

```bash
python3 _build/learn_snippets.py setup                       # venv from _build/learn-requirements.txt (CPU torch)
# pages that generate text need Ollama; the runner points them at its own instance:
CUDA_VISIBLE_DEVICES="" OLLAMA_HOST=127.0.0.1:6572 OLLAMA_MODELS=~/.cache/brewslm/learn/ollama-models ollama serve &
OLLAMA_HOST=127.0.0.1:6572 ollama pull qwen2.5:1.5b-instruct
python3 _build/learn_snippets.py run learn/rag/what-is-rag.html   # one page; no argument = every learn page
python3 _build/site.py build && python3 _build/site.py check
python3 -m unittest discover -s _build -p 'test_*.py'
```

`BREWSLM_LEARN_OLLAMA` overrides the runner's Ollama address (default `127.0.0.1:6572`); the pages themselves read
`$OLLAMA_HOST` and fall back to Ollama's usual default. Every page ran twice: once to capture its output, once to
check that a fresh run matches it (the second run writes the stamp). All runs were on 2026-11-26 on a 20-core aarch64
CPU with no GPU, on a machine shared with other work (load average 17 to 45), which is why timings are nd spans.

## Environment

| What | Value |
|---|---|
| Python and packages | Python 3.12.3; torch 2.5.1 (CPU wheel), numpy 2.1.3, sentence-transformers 3.3.1, transformers 4.46.3, tokenizers 0.20.3, huggingface_hub 0.36.2, rank-bm25 0.2.2, pypdf 5.1.0, faiss-cpu 1.9.0.post1, scikit-learn 1.9.1, scipy 1.18.1 (`_build/learn-requirements.txt`) |
| Ollama | 0.32.9, serving on 127.0.0.1:6572 with `CUDA_VISIBLE_DEVICES=""`, models under `~/.cache/brewslm/learn/ollama-models`; stopped after the runs |
| Generator | `qwen2.5:1.5b-instruct` from the Ollama library, Q4_K_M, Apache-2.0. Manifest digest `65ec06548149b04c096a120e4a6da9d4017ea809c91734ea5631e89f96ddc57b` (pages print the first 12 hex digits); model blob `sha256:183715c435899236895da3869489cc30ac241476b4971a20285b1a462818a5b4`. Options on every call: temperature 0, seed 0, `num_thread` 10 (20 threads ran at under 1 token/s on this machine, 10 at about 80), `keep_alive` 0 except where the long-context page reuses the chapter prefix on purpose |
| Tokenizer for counting | `Qwen/Qwen2.5-1.5B-Instruct` revision `989aa7980e4cf806f80c7fef2b1adb7bc71aa306`, Apache-2.0 (long-context page only) |

Why 1.5B and not 3B: Qwen2.5-3B-Instruct carries a research-only licence (BrewSLM's own Pub 17 part 2 post says so),
and the brief asks for small permissive models.

### Embedding and reranking models (all pinned with `revision=`; licences from the Hub API on 2026-11-26)

| Model | Revision | Licence | Pages |
|---|---|---|---|
| sentence-transformers/all-MiniLM-L6-v2 | 1110a243fdf4706b3f48f1d95db1a4f5529b4d41 | Apache-2.0 | most |
| cross-encoder/ms-marco-MiniLM-L-6-v2 | 233902d25c440f23af6f7d6e94d2946bac0bee0a | Apache-2.0 | reranking, what is RAG, citations |
| cross-encoder/nli-deberta-v3-xsmall | a150876415327c80daeff35ca6f68f5ed8cf5c24 | Apache-2.0 | faithfulness |
| cross-encoder/nli-MiniLM2-L6-H768 | b95119ce93d3e065de6214e38cd4a97b0f2f2c6d | Apache-2.0 | faithfulness |
| sentence-transformers/msmarco-distilbert-base-tas-b | b12d9352e776979147078a8975a4885042984fd1 | Apache-2.0 | cosine, dot and L2 |
| BAAI/bge-small-en-v1.5 | 5c38ec7c405ec4b44b94cc5a9bb96e735b38267a | MIT | choosing a model |
| BAAI/bge-base-en-v1.5 | a5beb1e3e68b9ab74eb54cfd186867f64f240e1a | MIT | choosing a model |
| intfloat/e5-small-v2 | ffb93f3bd4047442299a41ebb6fa998a38507c52 | MIT | choosing a model |
| thenlper/gte-small | 17e1f347d17fe144873b1201da91788898c639cd | MIT | choosing a model |
| sentence-transformers/all-mpnet-base-v2 | e8c3b32edf5434bc2275fc9bab85f82640a19130 | Apache-2.0 | choosing a model |
| sentence-transformers/static-retrieval-mrl-en-v1 | f60985c706f192d45d218078e49e5a8b6f15283a | Apache-2.0 | dimensions |

`sentence-transformers/multi-qa-MiniLM-L6-dot-v1` was considered for the dot-product example and dropped: its Hub
entry carries no licence tag.

## Corpus, chunks and questions

- IRS Publication 17 (2025), same file and SHA-256 as wave 0 (`2d2381d62c7c77ed…`), same cleaning. Every page's
  `load` block prints `sha256: 2d2381d62c7c77ed | chunks: 2170 | questions: 80 | with a relevant chunk: 79`.
- Chunks: the chunking page's recursive splitter at 512 characters (2,170 chunks). Pages that vary the size say so.
- Questions: `learn/rag/data/pub17-chunking-questions.json` (q01 to q30, wave 0) plus
  `learn/rag/data/pub17-more-questions.json` (q31 to q80, this wave). The 50 new ones were drafted with an AI
  assistant (the agent that built these pages) while reading pages the first 30 don't use, each with a verbatim
  evidence span checked by script, and **frozen in their own commit (332feeb) before any retrieval or generation ran
  on them**. q45, q56 and q57 have spans that occur twice. No benchmark values were involved.
- Relevance: a chunk is relevant when it holds a whole occurrence of the evidence span. q09's span is cut by a
  512-character boundary, so 79 of 80 is the ceiling at that size.
- Answer keys: `learn/rag/data/pub17-answer-keys.json`, short strings for 47 questions (17 of the first 30, 30 of
  the new 50), written from the evidence spans in the same frozen commit, before any model answered. Questions with
  a yes/no or sentence answer have no key.
- Statistics: every comparison is paired on the same questions. Yes/no outcomes use a two-sided exact McNemar
  (binomial) test on the discordant pairs, printed as "b vs c, p = …"; averages use a paired bootstrap (10,000
  resamples, seed 0) on the metrics page. Anything with p above 0.05 is called suggestive or noise on the page.

## Decisions (merge, split, drop, method)

1. **Chunk size and overlap kept as its own page** rather than merged into chunking strategies: the wave 0 page
   compares methods at fixed sizes; this one holds the method fixed and measures size and overlap at an equal
   character budget, which needed the 80-question set and its own table.
2. **Normalisation merged into cosine, dot and L2** (`learn/embeddings/cosine-dot-l2.html`). On its own it is one
   paragraph; the measurement that makes it interesting (a dot-product model normalised or not) belongs with the
   similarity comparison. The embeddings track therefore has eight topic pages plus the interview page, not ten.
3. **RAG evaluation split from faithfulness**, as the brief asked: retrieval metrics on one page, answer
   faithfulness (NLI and LLM checkers) on another.
4. **Plain BM25 stays the baseline everywhere** (rank-bm25 defaults, lowercase alphanumeric tokens), even though
   dropping stop words scored higher on the BM25 page (63 against 56, p = 0.065), so numbers stay comparable across
   pages and the stop-word result isn't tuned on the same questions it is reported on.
5. **Interview pages get their own check variant** (`<section id="questions" class="learn-interview">`): 15 to 25
   questions, answers of 20+ words in `<details>`, each linking to a page on the site that covers it, a "Checked on"
   line instead of "Tested on", and no code, pitfall or practice requirement. Tests: `LearnInterviewPages` in
   `_build/test_site.py`.
6. **Ollama determinism.** A side test showed temperature 0 with a fixed seed still changing a closed-book answer
   with the previous request (prompt-prefix reuse: $1,000 after one earlier prompt, $10,000 after another). The
   shared `chat` function was changed to `keep_alive: 0` (unload after each call) before the pages' final runs.
   Even then the what-is-RAG closed-book example changed between its two runs ($1,370, then $10,000), so that one
   line is marked `data-nd="any"`; all counts matched exactly across the two runs.
7. **Retrieval-only pages use the 80 questions; generation pages use the 47 keyed ones** (fewer where a page needs
   a subset, which it says).

## Numbers on each page

Each value below is printed by the named block on the page; "matched" means the second, independent run printed the
same thing.

| Page | Headline numbers | Block |
|---|---|---|
| rag/bm25 | top 5 of 80: plain 56 (rank-bm25) / 57 (smoothed IDF); no stop words 63 / 65; + suffix strip 65 / 67; k1 × b grid 52 to 58; no stop words vs plain 9 vs 2, p = 0.065 | `measure` |
| rag/dense-retrieval | BM25 top 1/5/10/20: 39/56/64/71; dense 36/57/70/73; union at top 5: 68; 12 vs 11, p = 1.0; FAISS flat top 5 = numpy | `measure` |
| rag/hybrid-search-rrf | RRF top 5: k=1 68, k=10 70, k=60 67, k=200 67; weighted α 0.3/0.5/0.7: 67/68/68; RRF vs dense 12 vs 2, p = 0.013; vs BM25 14 vs 3, p = 0.013 | `measure` |
| rag/reranking-cross-encoders | hybrid no rerank top1/top5/MRR 40/67/0.637; pool 10 53/69/0.749; pool 20 54/72/0.769; pool 30 54/72/0.768; pool 30 vs none top 1: 19 vs 5, p = 0.0066 | `measure` |
| rag/rag-evaluation-metrics | bm25 / dense / rrf / rerank MRR 0.593 / 0.574 / 0.637 / 0.768; rerank − rrf MRR +0.131 (95% bootstrap +0.060 to +0.205); hit@5 +0.062 (−0.013 to +0.138) | `measure` |
| rag/chunk-size-and-overlap | dense at 2,048-char budget: 128 → 44, 256 → 68, 512 → 55, 1,024 → 52; answerable at 128: 57 (66 with overlap); 256 vs 1,024 20 vs 4, p = 0.002; overlap vs none p 0.58 to 1.0 at every size | `measure` |
| rag/parent-document-retrieval | at ≤ 2,048 chars: 1,024 chunks top 2: 52; 256 chunks top 8: 69; children → 2 parents: 62; windows: 64; parents vs 1,024 16 vs 6, p = 0.052 | `measure` |
| rag/metadata-filters | dense top 5: no filter 57; oracle chapter pre-filter 68 (11 vs 0, p = 0.001); router by tag labels 26 (28/80 routed right); top-10 vote 52 (64/80 right; 6 vs 11, p = 0.33); post-filter 57 with 3.33 results on average, min 0 | `measure` |
| rag/query-rewriting | BM25 / dense top 5: original 56 / 57, keyword rewrite 49 / 54, original + rewrite 59 / 61, multi-query RRF 55 / 49; dense multi-query vs original 5 vs 13, p = 0.096 | `measure` |
| rag/hyde | dense top 1/5/10: question 36/57/70, HyDE passage 19/53/63, average 31/62/70; average vs question 10 vs 5, p = 0.30 | `measure` |
| embeddings/what-is-an-embedding | own span nearest of 80: MiniLM 71 (77 in top 5), TF-IDF 64 (74); 11 vs 4 (p = 0.12 by exact McNemar, computed by hand from the printed counts); mean similarity own 0.62 vs others 0.16 | `measure` |
| embeddings/cosine-dot-l2 | MiniLM: dot = cosine = L2 = 57/80, identical top 5; tas-b: dot 66, cosine 62, L2 61, norms 10.38 to 11.87, dot vs cosine 8 vs 4, p = 0.388 | `measure` |
| embeddings/sentence-transformers-basics | 1,024-char chunks: median 214 word pieces, 93% over 128, 13% over 256; 33 of 79 answers start after word piece 128; top 5 at max_seq_length 64/128/256: 44/51/64 | `measure` |
| embeddings/choosing-an-embedding-model | top 5 / MRR: MiniLM 57/0.579, bge-small 67/0.656 (62 without instruction), e5-small 65/0.646 (67 without prefixes), gte-small 67/0.674, mpnet 65/0.624, bge-base 69/0.690; bge-base vs MiniLM 12 vs 0, p < 0.001 | `measure` |
| embeddings/embedding-dimensions | MiniLM first d (384/256/128/64/32): 57/53/50/48/33; PCA: 57/56/54/52/44; MRL model 1024/512/384/256/128/64/32: 54/55/57/53/50/42/31; int8 57; binary 54; binary + float rescore of top 50: 56 | `measure` |
| embeddings/fine-tuning-an-embedding-model | 80 chunks held out, 1,290 pairs; base 57/80 MRR 0.579; fine-tuned 61/80 MRR 0.589, 6 vs 2, p = 0.289; with test chunks included (1,352 pairs) 62/80, 3 vs 2, p = 1.0 | `train`, `leak` |
| embeddings/clustering-and-dedup | 2 exact duplicates; pairs ≥ 0.98/0.95/0.92/0.90/0.85: 69/1,661/4,604/5,724/6,702; 146 of 176 chunks in ≥ 0.95 pairs mostly digits; k-means k = 14 on 1,705 chapter chunks: ARI 0.279 (MiniLM), 0.273 (TF-IDF), shuffled 0.000 | `dedup`, `cluster` |

Pages that generate text (qwen2.5:1.5b-instruct on CPU through Ollama, temperature 0, seed 0):

| Page | Headline numbers | Block |
|---|---|---|
| rag/what-is-rag | 47 keyed questions: model alone 8, + dense top 5 30, + reranked top 5 41; answer passage retrieved 35 (dense) / 41 (reranked); dense vs alone 23 vs 1, p < 0.00001; reranked vs dense 12 vs 1, p = 0.0034 | `measure` |
| rag/citations-and-quoting | 80 questions, hybrid + rerank top 5: has a citation 79, every cited id exists 79, marked quote 3, quote in cited passage 3; gold passage retrieved 72, retrieved and cited 46; all 6 quotes: 4 in cited passage, 1 in another, 1 nowhere | `measure` |
| rag/faithfulness | 23 claims × 3 conditions: own passage supported deberta 8, MiniLM2 13, judge 23; number changed accepted 4 / 5 / 3; other passage accepted 0 / 0 / 4; real answers (47) × MiniLM2: right+supported 20, right+unsupported 10, wrong+supported 11, wrong+unsupported 6 | `measure` |
| rag/long-context-vs-rag | Pub 17 285,953 Qwen2.5 tokens (8.7 × 32,768); chapter 1 26,240; five chunks about 663; 14 chapter-1 questions, 11 keyed: RAG 6, long context 7 (4 both, 2 only RAG, 3 only long context; p = 1.0); prompt tokens 601 vs 26,312; first long-context question 1,295 s in the capture run, later ones a few seconds (shared-prefix cache) | `tokens`, `measure` |
| rag/rag-vs-fine-tuning | 23 questions with a numeric key, one digit changed in the 26 chunks holding their evidence, those rows re-embedded: before the edit 15 answers gave the publication's number; after it 11 gave the new number and 7 still the old one. Fine-tuning figures are quoted from BrewSLM's blog posts, not rerun | `measure` |

### Facts quoted from elsewhere

| Claim | Source |
|---|---|
| Part 1: 3B alone 0.17, + passages 0.73, run #148 fine-tune 0.02; token F1 0.13 → 0.27 while correctness 0.20 → 0.02; 4 of 9 wrong answers were retrieval misses, 5 misreads; 173 of 1,103 passages carried page furniture; best of nine retrieval settings on 41 questions | `blog-irs-pub17-fine-tune-lost-retrieval-won.html` |
| Part 2: 1.5B alone 0.073, + passages 0.570, reader fine-tune + passages 0.688 (run #162), reader without passages 0.185, 157 test questions; 3B's research-only licence | `blog-irs-pub17-teaching-a-small-model-to-read.html` |
| BrewSLM BM25 (k1 1.5, b 0.75, keeps stop words), reranker default ms-marco-MiniLM-L-6-v2 with a pool of 4 × k in [10, 40], MiniLM near-duplicate threshold 0.92 with a hashing fallback, `chunk_text(1000, overlap 100)`, Ollama `num_ctx` sized per request | BrewSLM repo at main 7be6149: `backend/app/services/auto_rag_service.py`, `retrieval_reranker.py`, `dataset_intelligence_service.py`, `cleaning_service.py`, `synth_backends/ollama.py` |
| all-MiniLM-L6-v2 truncates past 256 word pieces and was trained on inputs cut at 128 tokens; position table 512 | model card and `config.json` in the HF cache |

## Hand check of the what-is-RAG answers

HAND_CHECK

## Hub and track pages

Page counts on the hub cards are counted from the published pages. The RAG track index lists every RAG page in
seven groups; the embeddings index lists its nine pages in four groups. The A-Z list on the hub names every learn
page, and `check` fails if a learn page is missing from the hub or its track.
