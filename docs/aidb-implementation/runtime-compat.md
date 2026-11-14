# ADK tool argument parser runtime compatibility

The real normal-chat acceptance run failed with `TypeError: install_lenient_tool_argument_parsing.<locals>.lenient_parse_tool_call_arguments() got an unexpected keyword argument 'function_name'` before the new AIDB tool could execute. The installed declared dependency is google-adk 2.14.0; its LiteLLM parser accepts `arguments` and keyword-only `function_name`. MatCreator's wrapper accepted only `arguments`.

The wrapper now forwards extra positional and keyword parser arguments. Both inner-quote repair and json_repair candidates pass through the original ADK parser with that same context, retaining upstream validation and behavior. Existing idempotence, valid payloads, malformed-payload recovery, and failure behavior remain covered.

## TDD evidence

Approved seam: patched model-tool argument parser in `tests/test_adk_compat.py`; actual normal chat remains the application acceptance check owned by integration.

1. Added actual installed-parser regression with `function_name` for valid JSON, unescaped inner quotes, and missing comma. Before implementation: 3 failures reproducing the exact TypeError.
2. Forwarded parser arguments. Existing and new parser tests: 10 passed.
3. Added external-parser boundary regression whose returned value includes positional context and function name. Normal input passed; both repair inputs failed because repaired values bypassed the original parser.
4. Routed repaired candidates through the original parser. Final command from this worktree:

```sh
PYTHONPATH=src ../integration/.venv/bin/python -m pytest tests/test_adk_compat.py tests/aidb -q
```

Result: 23 passed, 3 subtests passed, 4 existing dependency deprecation warnings. No network/model request is needed for these boundary tests. The live chat retry is performed by the integration owner after merging this fix.
