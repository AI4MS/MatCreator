"""Exercise the production graph endpoints without starting ADK/worker services."""
from __future__ import annotations

import ast
import asyncio
import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import JSONResponse, StreamingResponse
from fastapi.testclient import TestClient

from web.agent_graph_payload import graph_summary

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def endpoints(tmp_path):
    names = {
        "_agent_graph_paths", "_agent_graph_file_state", "_load_agent_graph_data",
        "_filter_agent_graph_nodes", "_filter_agent_graph_nodes_by_actions",
        "_graph_stream_delta", "get_agent_graph", "get_agent_graph_node", "stream_agent_graph",
    }
    tree = ast.parse((ROOT / "web/main.py").read_text())
    definitions = [node for node in tree.body if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name in names]
    app = FastAPI()
    namespace = {
        "app": app, "asyncio": asyncio, "json": json, "Path": Path, "Any": Any,
        "HTTPException": HTTPException, "Query": Query, "Request": Request,
        "JSONResponse": JSONResponse, "StreamingResponse": StreamingResponse,
        "graph_summary": graph_summary, "_MATCREATOR_MODE": "local", "_ADK_DIR": tmp_path,
    }
    exec(compile(ast.Module(body=definitions, type_ignores=[]), "web/main.py", "exec"), namespace)  # noqa: S102 - trusted repository definitions
    return namespace, TestClient(app), tmp_path


def graph(events=100, size=1000):
    return {
        "session_id": "session", "updated_at": "same-timestamp", "edges": [],
        "nodes": {f"task-{i}": {
            "id": f"task-{i}", "type": "step", "revision": 1, "status": "running",
            "input": {"node_id": f"task-{i}", "step_number": i, "action": "Work", "prompt": "x" * size},
            "conversation": [{"content": "x" * size} for _ in range(events)],
            "tool_calls": [{"output": "x" * size} for _ in range(events)],
            "artifacts": ["result"], "state_delta": {"large": "x" * size}, "output": "x" * size,
        } for i in range(200)},
    }


def write_graph(root, data):
    path = root / "agent_graphs/session.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data))
    return path


def test_summary_size_is_independent_of_execution_history():
    small, large = graph(events=1, size=10), graph(events=100, size=1000)
    summary = graph_summary(large)
    assert len(json.dumps(summary)) < 100_000
    assert len(json.dumps(summary)) < len(json.dumps(graph_summary(small))) * 1.1
    for node in summary["nodes"].values():
        assert node["conversation_count"] == node["tool_call_count"] == 100
        assert node["artifact_count"] == 1
        assert not {"conversation", "tool_calls", "artifacts", "state_delta", "output"} & node.keys()
        assert "prompt" not in node["input"]


def test_summary_and_filtered_endpoints_exclude_payload_but_detail_is_complete(endpoints):
    _, client, root = endpoints
    data = graph(events=2, size=20)
    data["nodes"]["child"] = {**data["nodes"]["task-1"], "id": "child", "parent_id": "task-0"}
    data["edges"] = [{"from": "task-0", "to": "child"}]
    write_graph(root, data)
    response = client.get("/api/agent-graph/session")
    assert response.status_code == 200
    assert response.json() == graph_summary(data)
    filtered = client.get("/api/agent-graph/session?node_id=task-0").json()
    assert set(filtered["nodes"]) == {"task-0", "child"}
    assert filtered["edges"] == data["edges"]
    assert "conversation" not in filtered["nodes"]["child"]
    detail = client.get("/api/agent-graph/session/nodes/task-0")
    assert detail.status_code == 200
    assert detail.headers["Cache-Control"] == "no-store"
    assert detail.json() == data["nodes"]["task-0"]
    assert client.get("/api/agent-graph/session/nodes/missing").status_code == 404
    assert client.get("/api/agent-graph/missing").json()["nodes"] == {}


async def consume_stream(namespace, *, iterations=5, on_poll=lambda n: None):
    class Connection:
        polls = 0

        async def is_disconnected(self):
            self.polls += 1
            on_poll(self.polls)
            return self.polls > iterations

    async def no_sleep(_):
        pass

    namespace["asyncio"] = SimpleNamespace(to_thread=asyncio.to_thread, sleep=no_sleep)
    response = await namespace["stream_agent_graph"]("session", Connection())
    assert response.headers["X-Accel-Buffering"] == "no"
    return [json.loads(frame.removeprefix("data: ")) async for frame in response.body_iterator]


def test_sse_stats_unchanged_file_without_reading_or_parsing(endpoints, monkeypatch):
    namespace, _, root = endpoints
    data = graph(events=2, size=20)
    path = write_graph(root, data)
    reads = []
    read_text = Path.read_text

    def read(self, *args, **kwargs):
        if self == path:
            reads.append(self)
        return read_text(self, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", read)
    frames = asyncio.run(consume_stream(namespace, iterations=20))
    assert len(reads) == 1
    assert len(frames) == 1
    assert frames[0] == graph_summary(data)


def test_sse_changed_node_sends_only_summary_revision_even_with_same_updated_at(endpoints):
    namespace, _, root = endpoints
    data = graph(events=2, size=20)
    write_graph(root, data)

    def update(poll):
        if poll != 3:
            return
        changed = data["nodes"]["task-0"]
        changed.update(revision=2, status="cancelled", end_time="finished", summary="Cancelled by user")
        changed["conversation"].append({"content": "huge history" * 10000})
        write_graph(root, data)

    frames = asyncio.run(consume_stream(namespace, on_poll=update))
    assert len(frames) == 2
    delta = frames[1]
    assert delta["delta"] is True
    assert set(delta["nodes"]) == {"task-0"}
    node = delta["nodes"]["task-0"]
    assert node["revision"] == 2
    assert node["conversation_count"] == 3
    assert node["status"] == "cancelled"
    assert "conversation" not in node and "tool_calls" not in node
    assert len(json.dumps(delta)) < 1500


def test_sse_retries_partial_writes_and_handles_file_replacement_and_deletion(endpoints):
    namespace, _, root = endpoints
    data = graph(events=1, size=10)
    path = write_graph(root, data)

    def update(poll):
        if poll == 2:
            path.write_text('{"nodes":')
        elif poll == 3:
            data["nodes"]["new"] = {"id": "new", "type": "step", "revision": 1}
            replacement = path.with_suffix(".tmp")
            replacement.write_text(json.dumps(data))
            replacement.replace(path)
        elif poll == 4:
            path.unlink()

    frames = asyncio.run(consume_stream(namespace, on_poll=update))
    assert len(frames) == 3
    assert set(frames[1]["nodes"]) == {"new"}
    assert "new" in frames[2]["removed_node_ids"]


def test_server_mode_observes_worker_graph_path(endpoints):
    namespace, _, root = endpoints
    worker = root / "worker"
    data = graph(events=1, size=10)
    write_graph(worker, data)
    namespace.update(_MATCREATOR_MODE="server", _iter_session_db_paths=lambda: [("owner", "db")], _user_adk_dir=lambda _: worker)
    frames = asyncio.run(consume_stream(namespace))
    assert frames[0] == graph_summary(data)


def test_legacy_logs_have_revision_and_bounded_display_text():
    data = graph(events=1, size=10)
    node = data["nodes"]["task-0"]
    del node["revision"]
    node["summary"] = "s" * 10000
    node["label"] = "l" * 10000
    summary = graph_summary(data)["nodes"]["task-0"]
    assert summary["revision"] == data["updated_at"]
    assert len(summary["summary"]) == len(summary["label"]) == 1024


def test_legacy_input_batch_metadata_is_lifted_into_summary():
    data = {"nodes": {"a": {"id": "a", "input": {"execution_batch_id": "round-2", "prompt": "large"}}}}
    summary = graph_summary(data)["nodes"]["a"]
    assert summary["batch_id"] == "round-2"
    assert summary["input"] == {}


def test_sse_preserves_creation_topology_and_timing(endpoints):
    namespace, _, root = endpoints
    data = graph(events=1, size=10)
    write_graph(root, data)

    def update(poll):
        if poll != 2:
            return
        data["nodes"]["child"] = {
            "id": "child", "type": "step", "status": "running", "revision": 1,
            "parent_id": "task-0", "dependency_ids": ["task-1"], "batch_id": "batch",
            "start_time": "start", "end_time": None, "summary": "Started",
        }
        data["edges"] = [{"from": "task-1", "to": "child"}]
        write_graph(root, data)

    frames = asyncio.run(consume_stream(namespace, on_poll=update))
    assert frames[1]["nodes"]["child"] == graph_summary(data)["nodes"]["child"]
    assert frames[1]["layout_changed"] is True
    assert frames[1]["edges"] == data["edges"]


def test_sse_does_not_reparse_an_unchanged_corrupt_file(endpoints, monkeypatch):
    namespace, _, root = endpoints
    path = write_graph(root, graph(events=1, size=10))
    path.write_text('{"nodes":')
    reads = []
    original = Path.read_text

    def read(self, *args, **kwargs):
        if self == path:
            reads.append(self)
        return original(self, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", read)
    assert asyncio.run(consume_stream(namespace, iterations=10)) == []
    assert len(reads) == 1
