from __future__ import annotations

import json

import pytest

from matcreator.adk_compat import (
    install_lenient_tool_argument_parsing,
    repair_unescaped_string_quotes,
)


def test_valid_json_passes_through_untouched():
    raw = json.dumps({"query": 'find "silicon" skills', "top_k": 5})
    assert json.loads(repair_unescaped_string_quotes(raw)) == json.loads(raw)


def test_unescaped_inner_quotes_are_escaped():
    raw = '{"query": "find "silicon" band-structure skills", "top_k": 5}'
    repaired = repair_unescaped_string_quotes(raw)
    assert json.loads(repaired) == {
        "query": 'find "silicon" band-structure skills',
        "top_k": 5,
    }


def test_unescaped_quotes_in_nested_values():
    raw = '{"a": {"b": "he said "hi" twice"}, "c": ["x"]}'
    repaired = repair_unescaped_string_quotes(raw)
    assert json.loads(repaired) == {"a": {"b": 'he said "hi" twice'}, "c": ["x"]}


def test_already_escaped_quotes_are_preserved():
    raw = '{"skill_name": "vasp-\\"relax\\" workflow"}'
    assert json.loads(repair_unescaped_string_quotes(raw)) == json.loads(raw)


def test_quote_directly_before_structural_char_is_ambiguous_and_best_effort():
    # A trailing quote right before the closing brace is indistinguishable
    # from the string terminator: the payload stays valid JSON and the
    # ambiguous trailing quote is dropped. Documented best-effort behavior —
    # strictly rejecting it would break the far more common repair cases.
    raw = '{"query": "ends with quote "}'
    assert json.loads(repair_unescaped_string_quotes(raw)) == {"query": "ends with quote "}


def test_install_is_idempotent_and_repairs_skill_retrieval_arguments():
    from google.adk.models import lite_llm as adk_lite_llm

    install_lenient_tool_argument_parsing()
    wrapped = adk_lite_llm._parse_tool_call_arguments
    install_lenient_tool_argument_parsing()
    assert adk_lite_llm._parse_tool_call_arguments is wrapped

    # The exact failure mode seen during skill retrieval: a long free-text
    # query whose embedded quotes broke the original parser.
    malformed = (
        '{"query": "search skills about "phonon" and "band structure" for Si", '
        '"top_k": 5, "skills_only": true}'
    )
    assert wrapped(malformed) == {
        "query": 'search skills about "phonon" and "band structure" for Si',
        "top_k": 5,
        "skills_only": True,
    }

    # Valid and already-supported payloads keep their original behavior.
    assert wrapped('{"query": "plain"}') == {"query": "plain"}
    assert wrapped({"already": "a dict"}) == {"already": "a dict"}
    assert wrapped("") == {}

    # Hopelessly malformed input still raises JSONDecodeError so the
    # orchestrator's stream-level retry remains the last line of defense.
    with pytest.raises(json.JSONDecodeError):
        wrapped("{not json at all")


def test_lenient_parser_repairs_shapes_the_quote_heuristic_cannot():
    from google.adk.models import lite_llm as adk_lite_llm

    install_lenient_tool_argument_parsing()
    wrapped = adk_lite_llm._parse_tool_call_arguments

    # Inner quote directly followed by a comma looks like a string
    # terminator to the heuristic; json_repair must rescue it.
    quote_before_comma = '{"query": "find "robust," Au surface skills"}'
    result = wrapped(quote_before_comma)
    assert isinstance(result, dict) and "query" in result
    assert "robust" in result["query"]

    # Missing comma between fields.
    missing_comma = '{"query": "gold surface reconstruction", "top_k": 5 "skills_only": true}'
    result = wrapped(missing_comma)
    assert isinstance(result, dict) and result.get("top_k") == 5

    # Truncated payload that still carries a usable field.
    truncated = '{"query": "Au surface reconstruction potential'
    result = wrapped(truncated)
    assert isinstance(result, dict) and result.get("query")
