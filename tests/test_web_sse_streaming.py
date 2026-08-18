from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_managed_run_sse_is_not_buffered_by_nginx() -> None:
    config = (ROOT / "deploy" / "nginx.server.conf.template").read_text(encoding="utf-8")

    assert "api/runs/[^/]+/events" in config
    sse_location = config[config.index("location ~ ^/(run_sse"):config.index("    # All other traffic")]
    assert "proxy_buffering off;" in sse_location
    assert "proxy_cache off;" in sse_location


def test_managed_run_endpoint_disables_reverse_proxy_buffering() -> None:
    source = (ROOT / "web" / "main.py").read_text(encoding="utf-8")
    endpoint = source[source.index('async def stream_managed_run_events'):source.index('@app.api_route("/run_sse"')]

    assert '"Cache-Control": "no-cache"' in endpoint
    assert '"X-Accel-Buffering": "no"' in endpoint


def test_frontend_reports_connection_before_first_agent_event() -> None:
    source = (ROOT / "web" / "vite-frontend" / "src" / "features" / "chat" / "messageStream.js").read_text(encoding="utf-8")

    assert 'updateAgentRunningStatus("connecting")' in source
    assert 'updateAgentRunningStatus("connected")' in source
