# Eval results

Scores copied from the runs saved on my Mac (`evals/run.py` writes one JSON per run). I only make a fine-tuned version
the default if no set drops by more than 5% (`evals/run.py`) and it also wins my blind A/B (`learning/promote.py`).
None has been made the default yet: Sylvie still runs on the base models.

## Qwen 27B: base vs my fine-tunes (5–6 Nov 2026)

v002 lost points in finance, languages and tool use, so it was rejected. v004, trained after I fixed the truncated
examples, has no regressions. The style set grew from 6 to 12 questions between runs.

| Test set | Base | v002 | Base (re-run) | v004 |
|---|---|---|---|---|
| Maths | 25/25 | 25/25 | 25/25 | 25/25 |
| Finance | 14/14 | 12/14 | 14/14 | 14/14 |
| Languages | 10/10 | 9/10 | 10/10 | 10/10 |
| Tool use | 9/9 | 8/9 | 9/9 | 9/9 |
| General | 8/8 | 8/8 | 8/8 | 8/8 |
| Style | 3/6 | 6/12 | 4/12 | 6/12 |

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
