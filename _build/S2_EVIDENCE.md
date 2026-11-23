# S2 claim → evidence (2026-11-23)

Product claims added in phase S2 (45 "Do this in BrewSLM" boxes, tutorial pagers, glossary/roadmap docs links).
Code paths are in the BrewSLM repo (github.com/TensorGreed/__SLM__, `main` at `c530316`), relative to its root.
"blog:" = a published post on this site; "facts:" = the internal product-facts list (claim → file:line); "A/B:" =
`data/curriculum_ab/curriculum_ab_results.json` (2026-05-25). Re-check line numbers when main moves.

## Training / telemetry / data (Foundations, SFT, Hands-on boxes)

| Claim | Evidence |
|---|---|
| Snapshot keeps 12 top-gradient layers; summary ranks by frequency (`most_common(12)`); UI shows 6 with avg/max | `backend/scripts/train.py` telemetry; `backend/app/services/training_telemetry_service.py:859` |
| Training gate refuses only when no sampled row (≤256) has answer/completion/output/response (sft, instruction_sft) | `backend/app/services/training_preflight_service.py` |
| Auto epochs: accumulation → 1 first, 8-epoch ceiling; 108 rows → 216 steps, eval loss 3.69 → 3.16 | `backend/app/services/training_epoch_policy.py:1-26` |
| Import rejections carry reason codes (`missing_assistant_reply`, `invalid_messages_shape`) | `backend/app/services/*import*` adapters |
| Doctor: WARN exits 0, FAIL 1, `--strict` | Phase 7g commit c10d8c0 |
| TRL SFTTrainer path does not use completion-only masking; packing only for CPT | `backend/scripts/train.py` |

## Advanced

| Claim | Evidence |
|---|---|
| Offline KD presets (Classification 512, Q&A 1024, Span 1024; α 0.5, T 2.0, r16) | `backend/app/services/training_recipe_service.py:155,197,239` |
| Live distillation α 0.6, T 2.0, hidden-state weight 0 | `backend/app/schemas/training.py:217,223` |
| `kd_loss` = α·CE + (1−α)·T²·KL; logs `distill_ce_loss` / `distill_kd_loss` | `backend/app/services/distillation/kd_loss.py:50`; `kd_trainer.py:132-136` |
| Capture: TEACHER_MODEL_API_*; per-row errors; budget cap ends capture; code-findings unsupported | `backend/app/services/distillation/` capture service |
| Quality retained = stored-score ratio, "exceeds" at teacher 0, no interval | Results → Compare panel + service |
| DPO/ORPO: beta 0.1, prompt 1024, length 2048, auto-filter off, threshold 3.0, min keep 0.4 | `schemas/training.py:177`; `frontend/src/components/training/useTrainingConfigForm.ts:54`; `alignment_dataset_service.py:717-790` |
| Playground pairs merged by default, cap 5000 (max 50000) | `alignment_dataset_service.py:517,541`; `TrainingAdvancedPeftSection.tsx:385` |
| Pairs from wrong/partial answers; test split refused unless ticked | `frontend/src/components/evaluation/PreferencePairsFromEvalCard.tsx:107` (+ test :77) |
| Curriculum auto-on ≤200 classification rows; A/B 0.20→0.39, 0.25→0.39, 5 seeds, gate passed | `training_service.py:289`; A/B |
| Monitor filters, limit 500, Failure clusters; support-bundle redaction | `frontend` Ship → Monitor; `backend/app/services/support_bundle_service.py`; screenshot `assets/screens/workspace/ship-monitor.png` |
| Multi-LoRA plan: `--max-loras`, `--max-lora-rank`, refuses mixed bases / non-LoRA / unfinished; 127.0.0.1 | `backend/app/services/serve_service.py:690`; `frontend/src/components/gateway/MultiLoraCard.tsx:68` |
| No speculative decoding, no pruning; "pruning" appears only in a hint | grep; `frontend/src/pages/ProjectShipPage.tsx:130` |
| `<think>` stripped from synthetic output | `backend/app/services/synthetic_service.py:284` |
| QA answer-span markers; `answer_span_extracted_rate` | `backend/app/services/eval_task_handler_service.py:588,601` |

## Tutorials

| Claim | Evidence |
|---|---|
| pip 109.7 s, npm ci 26.8 s, simulated run ≈1 min | facts: |
| RUN_HAS_NO_WEIGHTS | `backend/app/api/export.py:114` |
| Pub 17 reader 0.073 / 0.570 / 0.688, 32 better / 12 worse of 157, 292 → 4 deictic questions, 628 / 242 / 386 / 69 revise round | blog: Pub 17 reader post |
| Pub 17 SFT 0.20 → 0.02, RAG 0.73, run #148 loss 0.53 / 243 steps | blog: Pub 17 post |
| SQLi bootstrap (4,000/class, 11 attack + 10 benign, 60/40 gold) | `backend/scripts/bootstrap_sqli_demo.py` `BENIGN_TEMPLATES` (10 keys; the comment at line 35 still says 9) |
| Own-split import `on_leak=fail` | `backend/app/api/dataset.py:469` |
| NTSB 9,931 / 1,256 / 1,234, 0 shared, 0 dup | blog: NTSB extractor post |
| Ticket Router template: 5 labels, 200-row gold, brief macro-F1 0.85 | `backend/data/project_templates/ticket-router` |
| finding_set IoU ≥ 0.5, parent credit 0.5 | `backend/app/services/eval_task_handler_service.py` (finding_set) |
| Recall floors table, validation-only tuning, thresholds.json | `frontend/src/components/evaluation/SpanThresholdTuner.tsx:137`; span threshold service |
| Tagger #52 F1 0.11 (45 steps), #53 0.90 on 29 texts | blog: tagger post |
| `teacher_filter_keep: "correct"` API-only; revise checkbox | `frontend/src/components/training/DocumentsQaFlowCard.tsx:178`; documents-QA flow API |
| general-pack-v1 gates staging 0.65/0.75, prod 0.70/0.80/0.92/0.03; duplicate → v2 | `backend/app/services/domain_pack_service.py:18` |
| Demo answer keys are 200 rows (sentiment 70 / 65 / 65) | app commit aa04beb; `backend/data/demo_samples/*/gold.jsonl` |
