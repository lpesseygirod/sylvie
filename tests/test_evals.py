from evals.run import compare, number_candidates, score


def test_number_parsing_handles_both_conventions():
    assert 20780.12 in number_candidates("You'd have €20,780.12 at the end.")
    assert 20780.12 in number_candidates("Tendrías 20.780,12 €")
    assert 12.9 in number_candidates("son 12,90 €")
    assert 3600.0 in number_candidates("3 600 secondes")


def test_scoring():
    assert score(
        {"prompt": "x", "check": {"type": "number", "value": 397.8, "tol": 0.01}}, "It's 397.80 €", []
    )
    assert not score({"prompt": "x", "check": {"type": "number", "value": 397.8}}, "about 400", [])
    assert score(
        {"prompt": "¿Capital de Portugal?", "check": {"type": "contains", "value": "lisboa", "lang": "es"}},
        "Es Lisboa, claro.",
        [],
    )
    assert not score(
        {"prompt": "q", "check": {"type": "contains", "value": "lisboa", "lang": "es"}},
        "It's Lisboa, of course.",
        [],
    )
    assert score({"prompt": "hi", "check": {"type": "tool", "value": None}}, "Hello!", [])
    assert score({"prompt": "timer", "check": {"type": "tool", "value": "set_timer"}}, "Done", ["set_timer"])
    assert not score(
        {"prompt": "hey how are you?", "check": {"type": "style", "max_words": 20}},
        "As an AI language model, I am fine.",
        [],
    )
    assert score({"prompt": "x", "check": {"type": "judge"}}, "anything", []) is None


def test_compare_flags_regressions():
    old = {"summary": {"math": {"accuracy": 0.9}, "style": {"accuracy": 0.5}}}
    new = {"summary": {"math": {"accuracy": 0.7}, "style": {"accuracy": 0.8}}}
    assert compare(old, new)["regressions"] == ["math"]


def test_scorer_handles_typographic_minus_and_spanish_thousands():
    assert score(
        {"prompt": "x", "check": {"type": "number", "value": -3970.54, "tol": 0.05}}, "**NPV = −3970.54**", []
    )
    assert score(
        {"prompt": "x", "check": {"type": "contains", "value": "3600"}}, "¡Hola! 3.600 segundos.", []
    )
