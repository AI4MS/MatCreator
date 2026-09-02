"""Runtime compatibility patches for third-party ADK behavior.

Some OpenAI-compatible endpoints (notably GLM models) intermittently emit a
tool call whose ``function.arguments`` JSON contains unescaped double quotes
inside a string value, for example::

    {"query": "find "silicon" band-structure skills", "top_k": 5}

ADK's LiteLlm adapter already repairs unquoted object keys before it raises
``json.JSONDecodeError`` from ``_parse_tool_call_arguments``, but nothing
repairs inner quotes. When the failure surfaces during skill retrieval (long
free-text ``query`` arguments are the most quote-prone payloads) it kills the
whole planning stream even though the orchestrator retries it.

``install_lenient_tool_argument_parsing`` wraps the ADK parser with one extra
repair pass that escapes string-inner quotes, so the intended tool call can
still execute.
"""

from __future__ import annotations

import json
import logging

logger = logging.getLogger(__name__)

_PARSED_ATTR = "_matcreator_lenient_parsing_installed"


def repair_unescaped_string_quotes(raw: str) -> str:
    """Escape double quotes inside JSON string values that are not structural.

    A closing quote of a JSON string must be followed (ignoring whitespace) by
    one of the structural characters ``:`` ``,`` ``}`` ``]``. Any other quote
    inside a string is an unescaped inner quote and gets escaped. This is a
    best-effort heuristic: payloads whose intended text itself ends with a
    quote directly before a structural character cannot be distinguished and
    are left for the strict parser to reject.
    """
    out: list[str] = []
    in_string = False
    i = 0
    length = len(raw)
    while i < length:
        char = raw[i]
        if not in_string:
            if char == '"':
                in_string = True
            out.append(char)
            i += 1
            continue
        if char == "\\":
            out.append(raw[i : i + 2])
            i += 2
            continue
        if char == '"':
            j = i + 1
            while j < length and raw[j] in " \t\r\n":
                j += 1
            if j >= length or raw[j] in ":,}]":
                in_string = False
                out.append(char)
                i += 1
                continue
            out.append('\\"')
            i += 1
            continue
        out.append(char)
        i += 1
    return "".join(out)


def install_lenient_tool_argument_parsing() -> None:
    """Wrap ADK's LiteLlm tool-argument parser with JSON repair fallbacks.

    Idempotent: repeated calls keep exactly one wrapper around the original
    parser. The wrapper only acts when the original parser raises
    ``json.JSONDecodeError``; valid payloads pass through untouched. Repair
    chain: escape unescaped inner quotes, then the ``json_repair`` library
    (a litellm dependency, handles missing commas, truncation, mixed
    Python/JSON literals). If no repair yields a usable object the original
    error is re-raised so the orchestrator's stream-level retry remains the
    last line of defense.
    """
    try:
        from google.adk.models import lite_llm as adk_lite_llm
    except ImportError:  # pragma: no cover - ADK is a hard dependency in practice
        logger.warning("adk_compat: google.adk.models.lite_llm unavailable; patch skipped")
        return

    original = getattr(adk_lite_llm, "_parse_tool_call_arguments", None)
    if original is None or getattr(original, _PARSED_ATTR, False):
        return

    def _log_rescued(arguments: str, strategy: str) -> None:
        preview = arguments if len(arguments) <= 500 else arguments[:500] + "..."
        logger.warning(
            "adk_compat: rescued malformed tool-call arguments via %s: %s",
            strategy,
            preview,
        )

    def lenient_parse_tool_call_arguments(arguments):
        try:
            return original(arguments)
        except json.JSONDecodeError:
            if not isinstance(arguments, str) or not arguments:
                raise
            repaired = repair_unescaped_string_quotes(arguments)
            if repaired != arguments:
                try:
                    result = json.loads(repaired)
                    _log_rescued(arguments, "inner-quote escaping")
                    return result
                except json.JSONDecodeError:
                    pass
            try:
                import json_repair
            except ImportError:
                json_repair = None
            if json_repair is not None:
                result = json_repair.loads(arguments)
                if isinstance(result, dict) and result:
                    _log_rescued(arguments, "json_repair")
                    return result
            raise

    setattr(lenient_parse_tool_call_arguments, _PARSED_ATTR, True)
    adk_lite_llm._parse_tool_call_arguments = lenient_parse_tool_call_arguments
    logger.debug("adk_compat: lenient tool-argument parsing installed")
