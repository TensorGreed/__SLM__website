# S1 claim → evidence (2026-11-23)

Every product claim added in phase S1 (docs/ and the "Do this in BrewSLM" boxes) and where it comes from.
Code paths are in the BrewSLM repo (github.com/TensorGreed/__SLM__, `main` at `808833e`), relative to its root.
"blog:" means a published post on this site; "report:" means the internal Model 6 showcase report (tool-call
model, not published elsewhere; numbers quoted with its failed bar). Re-check line numbers when main moves.

## Training defaults (docs/training-defaults.html; boxes in what-is-a-model, how-models-learn, tokens-and-embeddings, anatomy, learning-rate, batch-size, overfitting, full-ft-vs-lora, lora-knobs, gpu-memory-math, surviving-oom, continued-pretraining, qlora-hands-on)

| Claim | Evidence |
|---|---|
| Default mode SFT; modes sft, domain_pretrain, dpo, orpo, distillation | `backend/app/schemas/training.py:19`; `backend/scripts/train.py:2241` |
| LR 2e-4, cosine, warmup_ratio 0.03, weight decay 0.01 | `schemas/training.py:63,65,122`; `train.py:2163` |
| Batch 4 × accumulation 4 | `schemas/training.py:61-62` |
| Max seq 2,048 (128..32,768); UI label "Max Seq Length" | `schemas/training.py:100`; `frontend/src/components/training/TrainingModelEssentialsSection.tsx:698` |
| LoRA r16 / alpha 32 / dropout 0.05 | `schemas/training.py:128-130` |
| Optimizer paged_adamw_8bit, falls back to AdamW without bitsandbytes, recorded as optimizer_effective | `schemas/training.py:64`; `train.py:2918-2926` |
| Save/eval every 100 steps, save_total_limit 3, load best at end (eval_loss; eval_accuracy for classification) | `train.py:2937-2969`; UI "Save Steps"/"Eval Steps" `TrainingModelEssentialsSection.tsx:713,726` |
| early_stopping_patience exists but is unused | `schemas/training.py:281`; no reader in `backend/app`, `backend/scripts` (grep) |
| Seed 42 as seed and data_seed | `schemas/training.py:283`; `train.py:2944-2945` |
| Target modules auto: ≤2B params all-linear, else q/v; explicit never overridden | `backend/app/services/lora_target_policy.py:54` and resolve fn |
| All-linear measurement (108 rows, 216 steps; SmolLM2 0.179/0.153 vs 0.218/0.204; 1.5B 0.256/0.260 vs 0.362/0.312; 1.6× time; 20 MB vs 74 MB) | `lora_target_policy.py:1-19` (docstring) |
| Auto epochs: 200 steps, ≥8 steps/epoch, 3 comfortable epochs, ceilings 4/8/3 by <100/<1000/else rows | `backend/app/services/training_epoch_policy.py:33,37,42,46-50` |
| 108 rows → accum 1, 8 epochs, 216 steps; eval loss 3.69→3.16, F1 0.12→0.18; old 45 steps 3.9→3.6 | `training_epoch_policy.py:12-23`; `agent-notes/training.md` |
| Token classification targets ~400 steps | `backend/scripts/train_token_classification.py:866-867` |
| Tagger: 3 flat epochs = 45 steps F1 0.11; ~400 steps F1 0.91 | blog: blog-sensitive-data-detector-from-zero.html |
| Explicit num_epochs turns auto off; old configs keep behaviour | `schemas/training.py:338-341`; `train.py:2642-2644` |
| Gradient checkpointing auto: off iff ≤2B and longest row ≤1,024 tokens | `backend/app/services/gradient_checkpointing_policy.py:28,30` |
| 30 s vs 19 s same loss; 1.5B full FT ~90 GB | `gradient_checkpointing_policy.py:1-19`; `agent-notes/training.md:71-76` |
| Micro-batch: longest row >1,024 and batch unset → batch 1, accumulation × batch; 55 GB example | `train.py:1238,1241-1260,2674-2681` |
| Passage prompts raise max length to longest row (cap 4,096); 26 of 323 rows lost answers 2026-10-19 | `train.py:1180-1218,2658-2669` |
| OOM retry on, 2 retries, shrink 0.75; ladder batch → seq → packing → flash → rank (floor 4); retry_history | `schemas/training.py:~150-157`; `train.py:1855-1910,2272`; UI labels `TrainingAdvancedPeftSection.tsx:110-263` |
| bf16 base under LoRA; fp32 for full FT / classification heads; DeBERTa bf16 at chance (0.59) | `train.py:2132-2137,2684-2709` |
| CPU fp32: ~230 s vs ~5 s per step | `train.py:2702-2709` |
| Determinism on; 0.2405 vs 0.2216; ~1.5× (10 s→15 s); one retry without | `train.py:2033,2050-2075,4467-4478`; `agent-notes/training.md:222-233` |
| training_report.json reproducibility hashes; runtime_environment keys auto_epochs, micro_batch, gradient_checkpointing, lora_target_modules, optimizer_effective, loss_masking | `train.py:2114-2121,2655,2680,2807,2845,2926`; `agent-notes/training.md:176-209` |
| Completion-only loss + never-masked EOS; stock collator problem | `train.py:1269-1281,4108-4113` |
| Multi-turn assistant-only loss, oldest exchanges dropped | `train.py:1040-1080` |
| Tokenizer chat template default; presets Llama-3, ChatML, Zephyr, Phi-3 | `schemas/training.py:22-30`; `TrainingModelEssentialsSection.tsx` (Chat Template select) |
| CPT defaults r64/a128, LR 1e-4, warmup 0.05, packing; documents-only projects pick CPT | `backend/app/services/continued_pretraining_policy.py:22-45` |
| CPT ppl ~2.4 → ~1.2 (small model) | `agent-notes/training.md:238-248` (notes, not code) |
| Lift check uses perplexity for documents-only projects | `backend/app/services/post_training_eval_service.py:113-126` |
| No QLoRA / 4-bit load | grep: no `load_in_4bit`/`BitsAndBytesConfig` in `backend/`; PRODUCT_FACTS §4 |
| More options sections and Advanced list; "Auto: scaled to your dataset size unless you change it." | `frontend/src/components/training/TrainForm.tsx:238-256`; `TrainingPanel.tsx:2053-2161`; `TrainingModelEssentialsSection.tsx:545-547` |
| Training Mode option "Continued pretraining (documents)" | `frontend/src/components/training/TrainingModeSection.tsx:29-32` |
| Gap scanner warns on epochs/warmup/LR; Coach text "will check itself on validation data only ≈ 2 times across ~216 training steps" | `agent-notes/training.md:294-307`; screenshot `assets/screens/workspace/train-more-options.png` |
| Preflight VRAM risk, safe/balanced/max_quality plans | `backend/app/services/training_preflight_service.py:1634-1706` |
| Tokenization panel = token-length distribution | `frontend/src/components/training/TokenizationPanel.tsx:1-3` |

## Evaluation (docs/scoring-modes.html, docs/lift-check.html; boxes in how-language-models-work, base-vs-instruct, what-is-sft, evaluation-fundamentals, decoding-controls, catastrophic-forgetting, llm-as-a-judge, structured-outputs, tool-use, beyond-a-single-run)

| Claim | Evidence |
|---|---|
| Handler registry by task profile, generic fallback | `backend/app/services/eval_task_handler_service.py:3655-3672,3787-3822` |
| SQuAD normalisation; answer markers | `backend/app/services/evaluation_service.py:159-181`; `eval_task_handler_service.py:575-600` |
| Judge 1/0.5/0, judge_correct; applies when median reference ≥ 8 words; resolution order; cloud never auto-picked; absent when no judge | `backend/app/services/answer_judge_service.py:1-24,52-57` |
| Judge calibration: default 20, max 100; insufficient <20 or <5 per class; below floor <0.70 upper / >0.15 lower | `backend/app/services/judge_calibration_service.py:52-53`; `eval_judge_calibration_service.py:74-80`; button "Calibrate eval judge" `frontend/src/components/settings/EvalJudgeCalibrationCard.tsx:219` |
| field_match layers, typed comparison, null precision/recall, headline f1, exact_match | `eval_task_handler_service.py:736-760,1388-1400` |
| Null counts as absent, off by default, per project or run; Compare refuses mixed | `backend/app/services/null_scoring.py:1-20`; `agent-notes/evaluation.md` |
| span_set strict (type, start, end), no partial | `eval_task_handler_service.py:1850-1870,1940-1970` |
| Tool call pass, extras ignored by default, strict_args opt-in | `backend/app/services/tool_call_scoring.py:31-50,88-92,415-455` |
| SQL execution defaults (off, 2000 ms, 10,000 rows, 1e-6, ORDER BY auto, read-only SELECT) | `backend/app/services/sql_execution_scoring.py:1-58` |
| Classification metrics, confusion cap 20, unparseable rate | `eval_task_handler_service.py:230-243` |
| Alignment = closer to chosen than rejected (F1 proxy) | `eval_task_handler_service.py:2967-2985` |
| Headline chosen by key order; Wilson only for share-of-rows | `backend/app/services/sft_lift_summary_service.py:41-72`; `frontend/src/components/evaluation/verdictScore.ts:234-239` |
| How it's scored / Change how it's scored switches | `frontend/src/components/evaluation/HowItsScoredCard.tsx:43,70`; `ScoringSettingsSection.tsx:99-104` |
| Auto lift: test split, 100 rows, 256 tokens, greedy; skip reasons; global switch wins; row cap override | `post_training_eval_service.py:42,49-61,191-249`; `backend/app/config.py:228,235-236` |
| Base cache by content hash and matching settings | `post_training_eval_service.py:433-531` |
| Neutral decoding; base scored twice when its config differs, higher is the bar | `backend/app/services/decoding_settings.py`; `post_training_eval_service.py:533-556,684-760` |
| Repetition penalty 1.1: 232 of 468 nulls vs 16 | `agent-notes/evaluation.md:~64-80`; memory note on repetition-penalty eval leak |
| Verdict rule: <5 rows too few; paired t 95%; McNemar exact p<0.05 for 0/1; 6/0 smallest (p=0.031); 4/0 p=0.125 | `backend/app/services/paired_comparison_stats.py:40,47,61-166` |
| Seeds: ≥2 seeds, t over seeds, 4.303 for 3, no McNemar; +22/+4/−3% example | `paired_comparison_stats.py:3-9,43,231-270` |
| Check across 3 seeds button | `frontend/src/components/evaluation/EvalSummaryCard.tsx:468-480` |
| Eval sets immutable, never training, leak scan exact then Jaccard ≥0.9; variants strip/replace/add; Every prompt format + Both = 4 evals | `agent-notes/evaluation.md:1799-1930`; `frontend/src/components/evaluation/EvalRunForm.tsx:23,193-194,336` |
| Score form auto-opens on a finished run with no evals | `agent-notes/evaluation.md:~646-660` |
| Base vs fine-tuned "Not like for like" line | `frontend/src/components/evaluation/VerdictCard.tsx:~310-316` |
| Pub 17 run #148: 0.20→0.02, 9 worse 2 better, −0.30..−0.04; F1 0.13→0.27; 243 steps, loss 0.53 | blog: blog-irs-pub17-fine-tune-lost-retrieval-won.html |
| Reader run #162: 0.570→0.688, 32/12, +0.054..+0.182; weakest seed +0.067 (+0.003..+0.131); 3B 0.73 vs 1.5B 0.585; licence reason | blog: blog-irs-pub17-teaching-a-small-model-to-read.html |
| NTSB: F1 0.519→0.960 (card), seeds 0.960/0.961/0.960, null recall 0.545→0.976, 382 nulls vs 192 invented, 3,366-token row, max 4,096, 9,931/1,256/1,234, 0 shared, merged ~3 GB, 0.976 on 100 re-check | blog: blog-ntsb-accident-report-extractor.html; image `assets/blog/ntsb-extractor/01-verdict-three-seeds.png` |
| Tagger: 0.88→0.74 second-opinion key, 0.74→0.85, 29 vs 44 test texts, 184M, 23 types | blog: blog-sensitive-data-detector-from-zero.html |
| Tool-call run #221: strict 543/600 vs base instructed 455; 111/23; McNemar p=5e-15; base native 0/600; unseen 174/200; false calls 7/100 vs bar 5 (FAIL); official 553 vs strict 543; 3,990 train; Q4_K_M 986,047,968 bytes | report: `phase-ui-7f/REPORT.md`, `RESULTS.md` |
| Support FAQ demo run #1: 0.167→0.119, 4 better 6 worse of 21, within noise; GGUF 4-bit 100.57 MB | screenshots `assets/screens/workspace/results-verdict.png`, `ship-package.png` |

## Data (docs/data-gates.html, docs/documents-qa-flow.html; boxes in data-quality-splits, gold-sets, public-benchmarks, pretraining-vs-ft-vs-rag)

| Claim | Evidence |
|---|---|
| Leakage: exact or Jaccard ≥0.9, <4 tokens exact only, ≥10% block severity, signal not refusal, no autofix | `backend/app/services/data_health_service.py:96,100,112,1438-1443,1586`; `agent-notes/data.md:457-466` |
| Practice key not a default training source | `agent-notes/data.md:271-273` |
| Split overlap scan, same thresholds; shared-line option ≥50% rows | `data_health_service.py:128-130,1581,1650`; `coach_service.py:1519-1550` |
| Split 0.8/0.1/0.1 seed 42, dedup default, stratify label, <3 to train, 10 snapshots, sha256 | `backend/app/services/dataset_service.py:79,2010-2066` |
| keep_test behaviour; label "Keep the current N test examples" | `dataset_service.py:2043-2048`; `frontend/src/components/data/DatasetPrepPanel.tsx:1754-1756` |
| Own split import: exact membership, no dedup, refuses/drops test overlaps | `agent-notes/data.md:425-428`; blog NTSB |
| Cleaning: chunk 1000 overlap 100, redact_pii true, page furniture; health thresholds 10/25, 10/30, 10/30 | `backend/app/api/cleaning.py:17-30`; `data_health_service.py:60-76` |
| Review queue pending/accepted/rejected, soft reject, restore, purge by reason, group-by options, assist pass/fail/unsure, judge-trust confirm; flow rows accepted | `backend/app/services/synth_review_queue_service.py:8-10,528-622,841-870`; `SynthReviewQueue.tsx:701-710`; `documents_qa_flow_service.py:1560-1561` |
| Benchmark contamination: verbatim, ≥4 chars, clean/low/notable/severe at 2%/10%; leakage_filter | `backend/app/services/benchmark_contamination_service.py:33-38,97-104`; `external_benchmark_service.py:852-868` |
| Prompt gate: whole-word, 400 PROMPT_CARRIES_BENCHMARK_VALUES, ≤25 listed, override recorded | `backend/app/services/prompt_contamination_service.py:3-34,124` |
| 2026-10-17 incident: 32 values × 591; 0.03–0.15 overstated | `benchmark_contamination_service.py:1-31` |
| Target-field gate, 256 rows, 10.5 h F1=0 origin | `backend/app/services/training_data_gate.py:10-19,36,104-175` |
| Upload formats and import tabs | `frontend/src/components/data/IngestionPanel.tsx:35,614-634,839-848`; `backend/app/utils/file_parsers.py:388-405` |
| Flow stages; MIN_PASSAGES 5, 3 pairs, MIN_TRAINING_PAIRS 40; deictic + 0.7 support drop; resume checkpoint | `backend/app/services/documents_qa_flow_service.py:64-70,249-277,537-541,1025-1027`; `agent-notes/documents-qa-flow.md` |
| Sizing 45 rows (DIFF_SD 0.5, GAP 0.15), TEST_RATIO 0.1 → 150 passages, MAX_TEST_RATIO 0.3; 0.08 → 157 | `documents_qa_flow_service.py:82-135`; `agent-notes/documents-qa-flow.md:141-142` |
| Retrieval tiers 0.65 / 0.5 / 0.25 / 0.4; only ready stops; val for sweep, test for report; never F1; not_judged | `backend/app/services/passage_rag_verdict_service.py:303-366`; `documents_qa_flow_service.py:912-918,1262-1279,1367-1436` |
| Passages-vs-fine-tune needs same judge, same base, gain beyond noise | `passage_rag_verdict_service.py:176-270` |
| Train anyway skips the sweep | blog Pub 17 part 1; `agent-notes/documents-qa-flow.md:122-124` |
| Reader options: passage prompts 20% withheld, teacher answers + filter, critique-and-revise | `agent-notes/documents-qa-flow.md:68-121`; `documents_qa_flow_service.py:1280-1298` |
| Backends: local first, cloud only pinned, metered, monthly cap → 402 | `backend/app/services/synth_backends/__init__.py:36-55`; `backend/app/services/llm_budget_guard.py:3-32,239-294`; `backend/app/main.py:587-603` |
| 292 → 4 deictic questions with repair | blog Pub 17 part 2 |

## Export (docs/export-formats.html; boxes in merge-and-infer, chat-templates, quantization)

| Claim | Evidence |
|---|---|
| Formats gguf, onnx, tensorrt, huggingface, docker; picker labels; Other formats | `backend/app/models/export.py:15-20`; `frontend/src/components/export/ExportPanel.tsx:37-39,1180-1212` |
| Package files; serve.py /generate /health MODEL_PATH; Dockerfile per format | `backend/app/services/export_service.py:1017-1197,1541-1574,1792-2110` |
| Manifest keys, portable paths, licence warns never blocks | `export_service.py:1118-1190`; `agent-notes/export.md` |
| GGUF types 2..16 bits; unsupported labels error; ONNX int8 only | `backend/app/services/compression_service.py:280-317` |
| llama.cpp convert + quantize env vars; ONNX optimum; TensorRT no converter | `backend/scripts/quantize.py:122-250`; `export_service.py:~1598` |
| Auto-convert on export | `export_service.py:~830-845`; `agent-notes/export.md` |
| Merge: fp32, base dtype, safetensors; GGUF/ONNX merge in quantize.py | `backend/app/services/adapter_merge_service.py:83-97`; `agent-notes/export.md` |
| Modelfile header comment, FROM, PARAMETER lines, defaults, num_ctx rule, TEMPLATE only ChatML/Llama 3/Gemma | `export_service.py:1634-1790`; `backend/app/services/decoding_settings.py:58-67`; `agent-notes/export.md:~88-100` |
| HF/Docker generation_config rewritten neutral | `agent-notes/export.md:~72-80` |
| Blocks: no artifacts, gate_policy, deployment validation | `export_service.py:769-806,870-880,1180-1189` |
| Hub publish: completed HF only, token order, never stored, model/ root, brewslm/ copies, card only for taggers/readers | `backend/app/services/hub_publish_service.py`; `ExportPanel.tsx:1656-1713` |

## Workspace and CLI (docs/workspace-map.html, docs/cli.html; boxes in picking-a-base-model, slm-project-lifecycle)

| Claim | Evidence |
|---|---|
| Six sections, Data steps, Results tabs + hints, Ship stages + hints | `frontend/src/utils/workspaceRoutes.ts:19-53,184-190`; `pages/ProjectResultsPage.tsx:29-40`; `pages/ProjectShipPage.tsx:32-44` |
| Demos (3), Open demo / Reset to fresh, FAQ 150 tickets 200-row key | `frontend/src/components/dashboard/DemoProjectTiles.tsx:156,167`; `backend/data/demo_samples/*/manifest.json` |
| Overview labels | `frontend/src/pages/ProjectOverviewPage.tsx:87-201` |
| Settings cards and Tools drawer | `frontend/src/pages/ProjectSettingsPage.tsx:118-241`; `workspaceRoutes.ts:98-105` |
| ⌘K palette, Quick search | `frontend/src/components/layout/CommandPalette.tsx:153,241` |
| One Coach notice, "N more tips" | `frontend/src/components/coach/CoachNotices.tsx:6-10` |
| Simulate mode needs both env vars, no weights, no verdict; ~1 minute | `backend/.env.example:68-71`; `post_training_eval_service.py:16-19`; PRODUCT_FACTS B15 |
| CLI wrapper, global flags, every subcommand and default | `brewslm:5-10`; `backend/scripts/brewslm.py` `build_parser()` (walked 2026-11-23, output kept in the session scratchpad) |
| Demo slugs support-faq, sentiment-classifier, pii-detector | `backend/data/demo_samples/` |
| train start defaults to autopilot one-click with a note | `backend/scripts/brewslm.py:535-539` |

## Corrections after review (2026-11-23)

| Claim | Evidence |
|---|---|
| Judge falls back to a configured cloud judge only when no local backend answers | `backend/app/services/answer_judge_service.py:20-27,559-583` |
| Teacher-target runs withhold no passages; run #152 (20% withheld) said "the passages don't say" on 35% of validation questions | `backend/app/services/passage_prompts_service.py:439` (`withheld_fraction: 0.0`); blog Pub 17 part 2 |
| Score form radio label "Both, base vs fine-tuned"; Both is the default only when the set has no base score yet | `frontend/src/components/evaluation/EvalRunForm.tsx:412-413,782-784` |
| paged_adamw_8bit is the default optimizer | `backend/app/schemas/training.py:64` |
| Run #148 trained on 324 of 405 generated pairs | blog Pub 17 part 1 (324 train / 40 val / 41 test) |
