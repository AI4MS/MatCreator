from matcreator.knowledge_schedule import is_knowledge_run_due, knowledge_frequency


def test_knowledge_frequency_defaults_and_disable(monkeypatch) -> None:
    monkeypatch.delenv("MATCREATOR_MEMORIZATION_FREQUENCY", raising=False)
    assert knowledge_frequency("MATCREATOR_MEMORIZATION_FREQUENCY", 1) == 1
    assert is_knowledge_run_due(3, 0) is False
    assert is_knowledge_run_due(3, 1) is True
    assert is_knowledge_run_due(3, 2) is False


def test_invalid_knowledge_frequency_uses_default(monkeypatch) -> None:
    monkeypatch.setenv("MATCREATOR_REVIEW_FREQUENCY", "invalid")
    assert knowledge_frequency("MATCREATOR_REVIEW_FREQUENCY", 10) == 10

    monkeypatch.setenv("MATCREATOR_REVIEW_FREQUENCY", "-1")
    assert knowledge_frequency("MATCREATOR_REVIEW_FREQUENCY", 10) == 10
