# Evaluation sets (never used for training)

13 sets, 360 items. `learning/dataset.py` refuses any training example whose prompt matches an eval prompt.

- From `evals/generate.py` (fixed seed, answers computed by code): `math` (25), `finance` (14), `multilingual` (10),
  `tools` (9), `general` (8), `style` (12).
- `knowledge` (150): held-out MMLU questions across 57 subjects (MIT), from `evals/make_knowledge.py`.
- Written by me, checked by code (the tool she must call, a key argument, the answer language): `everyday` (35),
  `apps` (34), `requests` (22, my real voice requests word for word), `todo` (15).
- `extraction` (16): what the background learner should and shouldn't save from a conversation.
- `memory` (10): remembering what I said in earlier conversations. `"earlier"` is a made-up earlier conversation
  ([my words, the answer], dated `"earlier_at"`), never a real one; `contains_any` accepts any of several wordings
  (an honest "I can't find it"), `not_contains` catches made-up details.

My own sets stay on my Mac, not in git:
- `lea_questions.jsonl`: questions I actually ask.
  `{"set": "lea", "id": "lea-1", "prompt": "…", "check": {"type": "contains", "value": "…"}}`
  (`"check": {"type": "judge"}` when there's no single right answer; those go to the blind A/B).
- `conversations.jsonl`: person-to-person moments ("I'm stressed about exams", "guess what happened today"),
  `"check": {"type": "judge"}`, rated by me in the blind A/B.
- `interests.jsonl`: messages where Sylvie should connect to one of my interests, and ones where she should stay quiet.
  `"check": {"type": "aside", "value": true}`.

Run: `make eval` (current version). Results go to `~/Library/Application Support/Sylvie/evals/`; the runs behind
the README numbers are in [`evals/results/`](../results).
