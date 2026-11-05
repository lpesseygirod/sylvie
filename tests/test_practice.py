import json

from core.db import Database
from core.llm import FakeLLM
from knowledge.docs_index import DocsIndex
from knowledge.embeddings import HashEmbedder
from lea_profile.interests import InterestStore
from learning.practice import practice, research_due, research_interests
from learning.store import LearningStore


def test_interest_research_writes_indexed_note_weekly(tmp_path):
    db = Database(tmp_path / "p.db", dim=64)
    emb = HashEmbedder(64)
    interests, docs = InterestStore(db, emb), DocsIndex(db, emb)
    i = interests.bump("Formula 1", 3)
    llm = FakeLLM(["- Verstappen won Mexico [1]\n- New 2026 car rules [2]"])

    def search(q):
        return (
            "<untrusted>…</untrusted>",
            [
                {"n": 1, "title": "F1", "url": "https://f1.example/a"},
                {"n": 2, "title": "Rules", "url": "https://f1.example/b"},
            ],
        )

    written = research_interests(llm, interests, docs, db, search, tmp_path / "notes")
    assert len(written) == 1 and "f1.example/a" in written[0].read_text()
    assert docs.search("Verstappen Mexico")[0].path.endswith(".md")
    assert not research_due(db, i.id, "weekly")  # not again this week
    assert research_interests(FakeLLM([]), interests, docs, db, search, tmp_path / "notes") == []


def test_practice_needs_leas_messages_and_keeps_clear_winners(tmp_path):
    db = Database(tmp_path / "p.db", dim=8)
    store = LearningStore(db)
    from core.conversations import Conversations

    convs = Conversations(db)
    assert practice(FakeLLM([]), FakeLLM([]), store, db, "", "You are Sylvie.", "rubric") == 0
    c = convs.create()
    for t in [
        "vale, mañana estudio finanzas",
        "ugh exam stress",
        "ok merci sylvie",
        "can you remind me later",
        "jajaja true",
    ]:
        convs.add_message(c["id"], "user", t)
    sylvie = FakeLLM(
        [
            '["hoy no puedo más jaja"]',
            "Ánimo Lea, descansa un rato y luego seguimos.",
            "I understand. Here are 7 tips for stress management: …",
        ]
    )
    judge = FakeLLM([json.dumps({"better": "A", "margin": 7})])
    assert (
        practice(
            sylvie,
            judge,
            store,
            db,
            "Mixes Spanish and English; casual",
            "You are Sylvie.",
            "rubric",
            n=1,
            batch="b",
        )
        == 1
    )
    ex = store.examples(status="staged")[0]
    assert ex["tag"] == "personal" and ex["chosen"].startswith("Ánimo")
