import json

import pytest

from core.db import Database
from core.llm import FakeLLM
from knowledge.embeddings import HashEmbedder
from lea_profile.interests import InterestStore, decayed
from lea_profile.preferences import PreferenceStore, detect_preference, maybe_preference
from lea_profile.style import StyleStore
from lea_profile.summary import profile_text
from memory.extract import parse_extraction
from memory.forget import detect_forget, forget
from memory.recall import fts_query, recall
from memory.store import MemoryStore


@pytest.fixture
def db(tmp_path):
    d = Database(tmp_path / "t.db", dim=256)
    yield d
    d.close()


@pytest.fixture
def emb():
    return HashEmbedder(dim=256)


def test_add_recall_and_supersede(db, emb):
    m = MemoryStore(db, emb)
    f1, out1 = m.add("Lea lives in Paris", key="lea.city", category="places")
    assert out1 == "added"
    f2, out2 = m.add("Lea lives in Madrid", key="lea.city", category="places")
    assert out2 == "superseded"
    assert m.get(f1.id).superseded_by == f2.id
    active = [f.text for f in m.list()]
    assert "Lea lives in Madrid" in active and "Lea lives in Paris" not in active
    assert [f.text for f in m.history("lea.city")] == ["Lea lives in Paris", "Lea lives in Madrid"]
    hits = recall(db, emb, "where does Lea live?")
    assert hits and hits[0].text == "Lea lives in Madrid"


def test_what_she_told_sylvie_comes_before_coding_sessions(db, emb):
    """2026-11-12: 217 of 241 facts came from her Claude coding sessions and crowded out her own words."""
    m = MemoryStore(db, emb)
    for i in range(6):
        m.add(f"Lea's app project step {i} for the Azure deployment is done", key=f"lea.step{i}",
              source=f"claude:s#{i}")  # fmt: skip
    m.add("Lea needs Sylvie to retest the Azure step on the iPhone", key="lea.retest", source="claude:s#9")
    m.add("Lea has to finish the Azure setup for Microsoft", key="lea.azure", source="c1#5")
    hits = recall(db, emb, "Azure", k=4, for_answer=True)
    assert hits[0].text == "Lea has to finish the Azure setup for Microsoft"
    assert sum(f.source.startswith("claude:") for f in hits) <= 2
    assert all("Sylvie" not in f.text for f in hits)
    # Learning and "forget that" still see everything
    assert any("Sylvie" in f.text for f in recall(db, emb, "Azure", k=8))


def test_duplicate_is_not_added_twice(db, emb):
    m = MemoryStore(db, emb)
    m.add("Lea studies computer science", key="lea.studies")
    _, out = m.add("Lea studies computer science", key="lea.studies")
    assert out == "duplicate"
    assert len(m.list()) == 1


def test_forget_bare_and_targeted(db, emb):
    m = MemoryStore(db, emb)
    a, _ = m.add("Lea has a cat named Miso", source="c1#5")
    b, _ = m.add("Lea plays the piano", source="c1#3")
    assert detect_forget("Sylvie, forget that") == ""
    assert detect_forget("don't forget my exam tomorrow") is None
    assert detect_forget("olvida que toco el piano") is not None
    gone = forget(m, "forget that", ["c1#5", "c1#3"])
    assert [f.id for f in gone] == [a.id]
    assert not m.get(a.id).active
    gone = forget(m, "forget that I play the piano", [])
    assert [f.id for f in gone] == [b.id]
    m.restore(a.id)
    assert m.get(a.id).active
    assert forget(m, "what's the weather?", []) is None


def test_fts_query_is_safe():
    assert fts_query('hello "world" OR NEAR(') == '"hello" OR "world" OR "near"'
    assert fts_query("a ?") is None


def test_extraction_drops_unrequested_sensitive_facts():
    raw = json.dumps(
        {
            "facts": [
                {"key": "lea.health", "fact": "Lea has asthma", "category": "other", "sensitive": True},
                {
                    "key": "lea.studies",
                    "fact": "Lea studies CS and BBA",
                    "category": "studies",
                    "sensitive": False,
                },
            ],
            "interests": ["machine learning"],
            "style": [
                {
                    "trait": "mixes_languages",
                    "description": "Mixes Spanish and English",
                    "evidence": "vale, ok",
                }
            ],
        }
    )
    ex = parse_extraction("Sure:\n```json\n" + raw + "\n```", "I study CS and BBA, and I have asthma")
    assert [f["fact"] for f in ex.facts] == ["Lea studies CS and BBA"]
    ex2 = parse_extraction(raw, "Please remember that I have asthma")
    assert any(f["sensitive"] for f in ex2.facts)
    assert ex.interests == ["machine learning"] and ex.style[0]["trait"] == "mixes_languages"


def test_preferences_detect_and_replace(db):
    store = PreferenceStore(db)
    assert maybe_preference("no Sylvie, too long, shorter please")
    assert maybe_preference("prefiero respuestas más cortas")
    assert not maybe_preference("what's the capital of France?")
    llm = FakeLLM(
        [
            '{"is_preference": true, "preference": "Keep answers short.", "replaces": [], "ack": "Got it, shorter."}'
        ]
    )
    p = detect_preference(llm, store, "too long! keep it shorter from now on")
    assert p["preference"] == "Keep answers short."
    llm2 = FakeLLM(
        [
            f'{{"is_preference": true, "preference": "Give detailed answers.", "replaces": [{p["id"]}], "ack": "Ok."}}'
        ]
    )
    detect_preference(llm2, store, "actually I prefer longer answers")
    assert [x.text for x in store.list()] == ["Give detailed answers."]
    assert "Give detailed answers." in store.as_prompt()


def test_style_traits_grow_and_respect_rejection_and_sensitivity(db):
    s = StyleStore(db)
    t = s.observe("prefers short answers", "Likes short answers", "too long")
    assert t.status == "guess" and t.confidence == pytest.approx(0.3)
    t = s.observe("prefers short answers", "Likes short answers", "shorter pls")
    assert t.confidence == pytest.approx(0.45) and len(t.evidence) == 2
    s.set_status(t.id, "rejected")
    assert s.observe("prefers short answers", "x", "y").status == "rejected"
    assert s.observe("seems depressed", "Mood is low") is None


def test_interests_bump_decay_and_confirm(db, emb):
    store = InterestStore(db, emb)
    for _ in range(3):
        store.bump("Formula 1")
    i = store.find("formula 1")
    assert i.mentions == 3 and i.score == pytest.approx(3, rel=0.01)
    assert store.to_confirm().topic == "Formula 1"
    store.mark_asked(i.id)
    assert store.to_confirm() is None
    assert decayed(
        4.0,
        "2026-01-01T00:00:00+00:00",
        now=__import__("datetime").datetime.fromisoformat("2026-01-31T00:00:00+00:00"),
    ) == pytest.approx(2.0)
    store.remove("Formula 1")
    store.bump("Formula 1")
    assert store.find("Formula 1").status == "rejected"


def test_profile_text(db, emb):
    m, s, i = MemoryStore(db, emb), StyleStore(db), InterestStore(db, emb)
    m.add("Lea studies CS and BBA in Madrid", key="lea.studies", category="studies")
    m.add("Lea likes her coffee black", category="preferences")
    t = s.observe("mixes_languages", "Mixes Spanish and English", "vale ok")
    s.set_status(t.id, "confirmed")
    i.bump("startups", 2)
    txt = profile_text(m, s, i)
    assert "Lea studies CS and BBA" in txt and "Mixes Spanish and English" in txt and "startups" in txt
    assert "coffee" not in txt  # non-core category stays out of the system prompt (recall handles it)
