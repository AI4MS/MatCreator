from __future__ import annotations

import sys
import types

import pytest

from matcreator.control_plane.e2b import E2BConfigurationError, E2BSandboxAdapter, E2BSandboxSpec


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
    paused = False
    killed = False
    files = _FakeFiles()

    @classmethod
    def create(cls, **kwargs):
        cls.created_with = kwargs
        return cls()

    @classmethod
    def connect(cls, sandbox_id):
        cls.connected_to.append(sandbox_id)
        return cls()

    class commands:
        @staticmethod
        def run(command, user, **kwargs):
            assert command == "echo hello"
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
    _FakeSandbox.paused = False
    _FakeSandbox.killed = False
    _FakeSandbox.files = _FakeFiles()
    monkeypatch.setitem(sys.modules, "e2b_code_interpreter", types.SimpleNamespace(Sandbox=_FakeSandbox))


def test_adapter_creates_sandbox_with_project_header() -> None:
    adapter = E2BSandboxAdapter()
    sandbox_id = adapter.create(
        E2BSandboxSpec(
            template="doc-compiler",
            api_key="secret",
            api_url="https://e2b.example",
            project_id="project-42",
            lifecycle={"on_timeout": "pause"},
        )
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
    adapter.pause("sandbox-123")
    adapter.terminate("sandbox-123")

    assert _FakeSandbox.connected_to == ["sandbox-123", "sandbox-123", "sandbox-123"]
    assert _FakeSandbox.paused is True
    assert _FakeSandbox.killed is True


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