"""Training data for the identity & style adapter (plan, Phase 4).

Both the questions and the answers are written by Sylvie's own local model (plan rule 4: never
by Claude): it invents everyday messages Lea might send (who are you, greetings, small talk, in
EN/ES/FR), then answers them with the persona and Lea's saved preferences in the system prompt.
The examples are stored WITHOUT that system prompt, so the adapter learns the identity itself.
Personal facts are left out of the generation prompt (rule 12). The batch is staged until Lea
approves it in the dashboard.
"""

from __future__ import annotations

from datetime import datetime

from core.jsonutil import extract_json

QUESTION_PROMPT = """Write {n} short, varied messages that Lea might send to her personal assistant Sylvie: \
greetings, asking who Sylvie is or what she can do, small talk, asking for a quick opinion, thanking her, \
saying goodbye. Mix English, Spanish and French (some mixing languages in one message). Casual tone, like real chat.
Return a JSON list of strings only."""


def generate(llm, store, persona_prompt: str, n: int = 30, model_name: str = "local") -> tuple[str, int]:
    raw = llm.complete([{"role": "user", "content": QUESTION_PROMPT.format(n=n)}], max_tokens=1500)
    questions = extract_json(raw)
    if not isinstance(questions, list):
        return "", 0
    batch = "identity-" + datetime.now().strftime("%Y%m%d-%H%M")
    kept = 0
    for q in [str(x).strip() for x in questions if str(x).strip()][:n]:
        answer = llm.complete(
            [{"role": "system", "content": persona_prompt}, {"role": "user", "content": q}], max_tokens=400
        )
        if not answer:
            continue
        store.add_example(
            [{"role": "user", "content": q}],
            chosen=answer,
            source=f"sylvie-local:{model_name}",
            license="apache-2.0 (local model output; identity batch)",
            tag="general",
            status="staged",
            batch=batch,
        )
        kept += 1
    return batch, kept
