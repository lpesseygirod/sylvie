# Eval results

Every number below comes from a run file in [`runs/`](runs): `evals/run.py` writes one JSON per run, with each prompt, Sylvie's answer, the tools she called and whether it passed. I only make a fine-tuned version
the default if no set drops by more than 5% (`evals/run.py`) and it also wins my blind A/B (`learning/promote.py`).
None has been made the default yet: Sylvie still runs on the base models.

## Qwen 27B: base vs my fine-tunes (5–6 Nov 2026)

Each fine-tune is compared with a run of the base model from the same session, because the base model itself varies a
little between runs (languages 9–10/10, tool use 8–9/9 below). That is also why the comparison allows a 5% margin.

v002 lost two finance questions against its base run (14 → 12): it did compound interest in its head instead of with
the calculator, so I rejected it. v004, trained after I fixed the truncated examples, has no regressions against its
base run. The style set grew from 6 to 12 questions after the first run.

| Test set | Base (5 Nov) | Base (6 Nov, 04:07) | v002 | Base (6 Nov, 06:03) | v004 |
|---|---|---|---|---|---|
| Maths | 25/25 | 25/25 | 25/25 | 25/25 | 25/25 |
| Finance | 14/14 | 14/14 | **12/14** | 14/14 | 14/14 |
| Languages | 10/10 | 9/10 | 9/10 | 10/10 | 10/10 |
| Tool use | 9/9 | 8/9 | 8/9 | 9/9 | 9/9 |
| General | 8/8 | 8/8 | 8/8 | 8/8 | 8/8 |
| Style | 3/6 | 6/12 | 6/12 | 4/12 | 6/12 |

## Qwen 9B: base vs v005 (12 Nov 2026)

v005 improved style but got worse at maths, finance and tool use, so it was rejected and Sylvie stays on the base 9B.

| Test set | Base | v005 |
|---|---|---|
| Maths | 25/25 | 23/25 |
| Finance | 14/14 | 10/14 |
| Languages | 10/10 | 10/10 |
| Tool use | 9/9 | 7/9 |
| General | 7/8 | 8/8 |
| Style | 7/12 | 9/12 |
| Knowledge (MMLU sample) | 105/150 | 106/150 |

## A fix measured before and after (10–11 Nov 2026)

To-do requests ("remind me to…") were handled badly. I wrote the test set first, then fixed the routing.

| Test set | Before | After |
|---|---|---|
| To-dos | 3/15 | 15/15 |
| Everyday requests | 18/22 | 21/22 |

## Run files

| Run | File |
|---|---|
| 27B base, 5 Nov | [`base-qwen3.8-27b-20261105-1944.json`](runs/base-qwen3.8-27b-20261105-1944.json) |
| 27B base, 6 Nov 04:07 | [`current-20261106-0407.json`](runs/current-20261106-0407.json) |
| 27B v002 | [`v002-20261106-0427.json`](runs/v002-20261106-0427.json) |
| 27B base, 6 Nov 06:03 | [`current-20261106-0603.json`](runs/current-20261106-0603.json) |
| 27B v004 | [`v004-20261106-0622.json`](runs/v004-20261106-0622.json) |
| 9B base, 12 Nov | [`current-20261112-0407.json`](runs/current-20261112-0407.json) |
| 9B v005 | [`v005-20261112-0417.json`](runs/v005-20261112-0417.json) |
| To-dos before / after | [`todo-before-20261110-2344.json`](runs/todo-before-20261110-2344.json), [`todo-after-20261110-2344.json`](runs/todo-after-20261110-2344.json) |
| Everyday requests before / after | [`requests-old-20261111-0025.json`](runs/requests-old-20261111-0025.json), [`requests-new2-20261111-0030.json`](runs/requests-new2-20261111-0030.json) |
