import json

import pytest

from learning.store import DisallowedSource
from learning.teacher import is_wrong_feedback
from tests.test_orchestrator import extraction, make


def diagnosis(
    rule_type="preference", rule_text="Answer with the number first, then one sentence.", personal=False
):
    return json.dumps(
        {
            "scores": {"correct": 2},
            "problem": "It rambled before giving the number.",
            "kind": "length",
            "needs_research": False,
            "research_query": "",
            "search_documents": False,
            "rule": {"type": rule_type, "text": rule_text},
            "personal": personal,
        }
    )


def first_answer(s, text="What's 15% of 80?", reply="Well, percentages are interesting... it's 12."):
    s.llm.replies[:0] = [reply, extraction()]
    ev = list(s.turn(text))
    s.bg.drain()
    done = ev[-1]
    return done["conversation_id"], done["message_id"]


@pytest.mark.parametrize(
    "text",
    [
        "Sylvie, that's wrong",
        "that's not right",
        "eso está mal",
        "no, eso no es correcto",
        "c'est faux",
        "Faux !",
    ],
)
def test_wrong_feedback_detection(text):
    assert is_wrong_feedback(text)


@pytest.mark.parametrize(
    "text", ["what's wrong with my code?", "is it wrong to eat late?", "¿qué está mal en esto?"]
)
def test_not_wrong_feedback(text):
    assert not is_wrong_feedback(text)


def test_thumbs_down_teacher_proposal_accept_applies_next_time(tmp_path):
    s = make(tmp_path, [])
    cid, mid = first_answer(s)
    s.llm.replies += [diagnosis(), "12."]
    corr = s.teacher.report(mid, "thumbs_down", comment="too much rambling")
    s.bg.drain()
    c = s.learning.correction(corr)
    assert c.status == "proposed" and c.proposal == "12." and c.diagnosis["kind"] == "length"
    assert s.llm.calls[-2]["thinking"] is True  # diagnosis used thinking mode
    out = s.teacher.accept(corr)
    assert out["source"].startswith("sylvie-local:") and out["rule"]["type"] == "preference"
    ex = s.learning.examples()
    assert ex[0]["chosen"] == "12." and ex[0]["rejected"].startswith("Well") and ex[0]["tag"] == "general"
    assert "accepted by Lea" in ex[0]["license"]
    # Gate: the correction is applied the next time (new conversation's system prompt has the rule).
    s.llm.replies += ["12.", extraction()]
    list(s.turn("What's 15% of 80?"))
    assert (
        "Answer with the number first" in s.llm.calls[-1]["messages"][0]["content"]
        or "Answer with the number first" in s.llm.calls[-2]["messages"][0]["content"]
    )


def test_lea_editing_the_proposal_makes_it_her_example(tmp_path):
    s = make(tmp_path, [])
    _, mid = first_answer(s)
    s.llm.replies += [diagnosis(rule_type="none", rule_text=""), "It's 12."]
    corr = s.teacher.report(mid, "thumbs_down")
    s.bg.drain()
    out = s.teacher.accept(corr, edited_text="12 (15% of 80).")
    assert out["source"] == "lea" and s.learning.examples()[0]["weight"] == 2.0


def test_reject(tmp_path):
    s = make(tmp_path, [])
    _, mid = first_answer(s)
    s.llm.replies += [diagnosis(), "12."]
    corr = s.teacher.report(mid, "thumbs_down")
    s.bg.drain()
    s.teacher.reject(corr)
    assert s.learning.correction(corr).status == "rejected" and s.learning.examples() == []


def test_lea_edit_is_accepted_automatically_and_updates_style(tmp_path):
    s = make(tmp_path, [])
    _, mid = first_answer(
        s,
        reply="Well, percentages are interesting because they express parts of a hundred, so 15% of 80 is 12. 😊",
    )
    s.llm.replies += [diagnosis(rule_type="preference", rule_text="Keep answers short.")]
    s.teacher.report(mid, "edit", edited="12.")
    s.bg.drain()
    c = s.learning.corrections()[0]
    assert c.status == "accepted" and c.final_answer == "12."
    assert s.learning.examples()[0]["source"] == "lea"
    traits = {t.name for t in s.style.list()}
    assert {"prefers_short_answers", "no_emojis"} <= traits
    assert "Keep answers short." in [p.text for p in s.prefs.list()]


def test_thats_wrong_in_chat_triggers_review(tmp_path):
    s = make(tmp_path, [])
    cid, mid = first_answer(s)
    s.llm.replies += ["Sorry! It's 12.", extraction(), diagnosis(), "12."]
    ev = list(s.turn("Sylvie, that's wrong", conversation_id=cid))
    assert any(e["type"] == "notice" and e["kind"] == "feedback" for e in ev)
    s.bg.drain()
    assert s.convs.message(mid)["feedback"] == -1
    c = s.learning.correction_for_message(mid)
    assert c.kind == "wrong" and c.status == "proposed"


def test_training_queue_refuses_disallowed_sources(tmp_path):
    s = make(tmp_path, [])
    with pytest.raises(DisallowedSource):
        s.learning.add_example(
            [{"role": "user", "content": "hi"}], "hello", source="claude", license="?", tag="general"
        )
    with pytest.raises(ValueError):
        s.learning.add_example(
            [{"role": "user", "content": "hi"}], "hello", source="lea", license="own", tag="secret"
        )


def test_remember_im_into_confirms_interest(tmp_path):
    s = make(tmp_path, ["Noted!", extraction(interests=["climbing"])])
    list(s.turn("Remember I'm into climbing"))
    s.bg.drain()
    assert s.interests.find("climbing").status == "confirmed"
