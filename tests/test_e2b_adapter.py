from __future__ import annotations

import sys
import types

import pytest

from matcreator.control_plane.providers.e2b import E2BConfigurationError, E2BSandboxAdapter, E2BSandboxSpec


class _FakeResult:
    stdout = "hello\n"
    stderr = ""
    exit_code = 0


class _FakeStreamReader:
    """Mimics e2b FileStreamReader: iterable bytes + close()."""

    def __init__(self, chunks: list[bytes]) -> None:
        self._chunks = list(chunks)
        self.closed = False

    def __iter__(self):
        return self

    def __next__(self):
        if not self._chunks:
            raise StopIteration
        return self._chunks.pop(0)

    def close(self) -> None:
        self.closed = True


class _FakeFiles:
    def __init__(self) -> None:
        self.read_calls: list[dict] = []
        self._chunks: list[bytes] = []

    def write(self, destination, file_handle, **kwargs):
        self._chunks = [file_handle.read()]

    def read(self, path, *, format="text", user=None, **kwargs):
        self.read_calls.append({"path": path, "format": format, "user": user})
        if format != "stream":
            return b"".join(self._chunks)
        return _FakeStreamReader(list(self._chunks))


class _FakeSandbox:
    sandbox_id = "sandbox-123"
    created_with: dict = {}
    connected_to: list[str] = []
    connect_opts: list[dict] = []
    paused = False
    killed = False
    files = _FakeFiles()

    @classmethod
    def create(cls, **kwargs):
        cls.created_with = kwargs
        return cls()

    @classmethod
    def connect(cls, sandbox_id, **opts):
        cls.connected_to.append(sandbox_id)
        cls.connect_opts.append(opts)
        return cls()

    class commands:
        last_command: str | None = None
        last_kwargs: dict = {}

        @staticmethod
        def run(command, user, **kwargs):
            _FakeSandbox.commands.last_command = command
            _FakeSandbox.commands.last_kwargs = kwargs
            assert user == "root"
            return _FakeResult()

    def pause(self):
        type(self).paused = True

    def kill(self):
        type(self).killed = True


@pytest.fixture(autouse=True)
def fake_e2b_module(monkeypatch):
    _FakeSandbox.created_with = {}
    _FakeSandbox.connected_to = []
    _FakeSandbox.connect_opts = []
    _FakeSandbox.paused = False
    _FakeSandbox.killed = False
    _FakeSandbox.files = _FakeFiles()
    _FakeSandbox.commands.last_command = None
    _FakeSandbox.commands.last_kwargs = {}
    monkeypatch.setitem(sys.modules, "e2b_code_interpreter", types.SimpleNamespace(Sandbox=_FakeSandbox))
    # Reconnects require the endpoint configuration in the environment.
    monkeypatch.setenv("E2B_API_KEY", "secret")
    monkeypatch.setenv("E2B_API_URL", "https://e2b.example")
    monkeypatch.setenv("BOHRIUM_PROJECT_ID", "project-42")


def test_adapter_creates_sandbox_with_project_header() -> None:
    adapter = E2BSandboxAdapter()
    sandbox_id = adapter.create(
        {
            "template": "doc-compiler",
            "api_key": "secret",
            "api_url": "https://e2b.example",
            "project_id": "project-42",
            "lifecycle": {"on_timeout": "pause"},
        }
    )

    assert sandbox_id == "sandbox-123"
    assert _FakeSandbox.created_with["headers"] == {"X-Project-Id": "project-42"}
    assert _FakeSandbox.created_with["lifecycle"] == {"on_timeout": "pause"}


def test_adapter_connects_for_command_and_controls() -> None:
    adapter = E2BSandboxAdapter()

    assert adapter.run_command("sandbox-123", "echo hello") == {
        "stdout": "hello\n",
        "stderr": "",
        "exit_code": 0,
    }
    assert _FakeSandbox.commands.last_command == "echo hello"
    assert _FakeSandbox.commands.last_kwargs["timeout"] == 0
    adapter.pause("sandbox-123")
    adapter.terminate("sandbox-123")

    assert _FakeSandbox.connected_to == ["sandbox-123", "sandbox-123", "sandbox-123"]
    assert _FakeSandbox.paused is True
    assert _FakeSandbox.killed is True
    # Reconnects must carry the configured endpoint, never the SDK default.
    assert _FakeSandbox.connect_opts[0] == {
        "api_key": "secret",
        "api_url": "https://e2b.example",
        "headers": {"X-Project-Id": "project-42"},
    }


def test_adapter_connect_fails_loudly_without_endpoint_configuration(monkeypatch) -> None:
    adapter = E2BSandboxAdapter()
    monkeypatch.delenv("E2B_API_KEY", raising=False)

    with pytest.raises(E2BConfigurationError, match="E2B_API_KEY and E2B_API_URL"):
        adapter.run_command("sandbox-123", "echo hello")
    assert _FakeSandbox.connected_to == []


def test_adapter_cancel_aliases_terminate() -> None:
    adapter = E2BSandboxAdapter()

    adapter.cancel("sandbox-123")

    assert _FakeSandbox.killed is True


def test_adapter_create_error_includes_request_context(monkeypatch) -> None:
    def _boom(**kwargs):
        raise RuntimeError("404: Resource not found")

    monkeypatch.setattr(_FakeSandbox, "create", _boom)
    adapter = E2BSandboxAdapter()

    with pytest.raises(RuntimeError) as excinfo:
        adapter.create(
            {
                "template": "doc-compiler",
                "api_key": "secret",
                "api_url": "https://open.bohrium.com/wrong/path",
                "project_id": "project-42",
            }
        )

    message = str(excinfo.value)
    assert "404: Resource not found" in message
    assert "https://open.bohrium.com/wrong/path" in message
    assert "doc-compiler" in message
    assert "project-42" in message
    assert "secret" not in message


def test_adapter_status_reports_liveness_without_a_normalized_status() -> None:
    adapter = E2BSandboxAdapter()

    status = adapter.status("sandbox-123")

    assert status.normalized_status is None
    assert status.snapshot["provider_status"] == "reachable"


def test_adapter_status_uses_finite_configured_probe_timeout(monkeypatch) -> None:
    monkeypatch.setenv("MATCREATOR_REMOTE_PROVIDER_QUERY_TIMEOUT_SECONDS", "0.25")

    status = E2BSandboxAdapter().status("sandbox-123")

    assert status.normalized_status is None
    assert _FakeSandbox.commands.last_command == "true"
    assert _FakeSandbox.commands.last_kwargs["timeout"] == 0.25


def test_adapter_run_command_accepts_finite_control_plane_timeout() -> None:
    E2BSandboxAdapter().run_command(
        "sandbox-123",
        "true",
        timeout_seconds=2.5,
    )

    assert _FakeSandbox.commands.last_kwargs["timeout"] == 2.5


def test_adapter_download_file_streams_to_local_destination(tmp_path) -> None:
    adapter = E2BSandboxAdapter()
    adapter.upload_file("sandbox-123", _write(tmp_path / "in.bin", b"\x00\x01binary"), "/home/user/CHGCAR")

    dest = tmp_path / "out" / "CHGCAR"
    returned = adapter.download_file("sandbox-123", "/home/user/CHGCAR", dest)

    assert returned == dest.resolve()
    assert dest.read_bytes() == b"\x00\x01binary"
    assert _FakeSandbox.files.read_calls[-1] == {
        "path": "/home/user/CHGCAR",
        "format": "stream",
        "user": None,
    }


def _write(path, data: bytes):
    path.write_bytes(data)
    return path


def test_adapter_download_cleans_up_partial_file_on_stream_error(tmp_path) -> None:
    adapter = E2BSandboxAdapter()

    class _BoomStream:
        def __iter__(self):
            return self

        def __next__(self):
            raise RuntimeError("network dropped")

        def close(self) -> None:
            pass

    _FakeSandbox.files.read = lambda path, *, format="text", user=None, **kw: _BoomStream()

    dest = tmp_path / "CHGCAR"
    with pytest.raises(RuntimeError, match="network dropped"):
        adapter.download_file("sandbox-123", "/home/user/CHGCAR", dest)

    assert not dest.exists()


def test_adapter_rejects_missing_required_configuration() -> None:
    spec = E2BSandboxSpec(
        template="",
        api_key="secret",
        api_url="https://e2b.example",
        project_id="project-42",
    )

    with pytest.raises(E2BConfigurationError, match="template"):
        spec.create_kwargs()
