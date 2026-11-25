import hashlib
import hmac
import io
import json
import os
import socket
import subprocess
import sys
import threading
import time

import pytest

from matcreator.scripts import local_srt_channel as channel
from matcreator.scripts import local_srt_supervisor as supervisor
from matcreator.scripts import local_srt_worker as worker


ACCOUNT = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"


def wait_for(predicate, timeout=5):
    deadline = time.monotonic() + timeout
    while not predicate():
        if time.monotonic() >= deadline:
            pytest.fail("Bounded wait expired")
        time.sleep(0.01)


def test_worker_environment_keeps_srt_network_proxy_but_removes_host_bootstrap():
    env = worker.worker_environment({"MATCREATOR_LOCAL_ACCOUNT_ID": ACCOUNT, "MATCREATOR_HOME": "/private/alice",
        "NODE_OPTIONS": "malicious", "LD_PRELOAD": "bad", "PYTHONPATH": "/evil",
        "MATCREATOR_CONTAINER_BOOT_TOKEN": "secret", "HTTP_PROXY": "http://127.0.0.1:1234",
        "MATCREATOR_LOCAL_ACCOUNT_ROOT": "/real/broker"}, "/opt/shared/app")
    assert "NODE_OPTIONS" not in env and "LD_PRELOAD" not in env
    assert "MATCREATOR_CONTAINER_BOOT_TOKEN" not in env
    assert env["PYTHONPATH"] == "/opt/shared/app/src:/opt/shared/app"
    assert env["HTTP_PROXY"] == "http://127.0.0.1:1234"
    assert env["MATCREATOR_LOCAL_ACCOUNT_ROOT"] == "/mc-broker-boundary"
    assert env["MATCREATOR_LOCAL_SRT_WORKER"] == "1"


def test_supervisor_does_not_inherit_node_or_provider_credentials(monkeypatch):
    monkeypatch.setenv("NODE_OPTIONS", "--import /evil")
    monkeypatch.setenv("HTTP_PROXY", "http://ambient:1234")
    monkeypatch.setenv("OPENAI_API_KEY", "secret")
    env = supervisor.clean_environment({"home": "/private/alice", "source_root": "/opt/shared/app",
        "bwrap_wrapper_dir": "/opt/shared/bin", "node": "/opt/tools/node/bin/node",
        "python": "/opt/shared/venv/bin/python", "account_id": ACCOUNT})
    assert not ({"NODE_OPTIONS", "HTTP_PROXY", "OPENAI_API_KEY"} & env.keys())
    assert env["PATH"].startswith("/opt/shared/bin:/opt/tools/node/bin:/opt/shared/venv/bin:")


@pytest.mark.parametrize("kind,flow,length", [(0, 1, 0), (channel.OPEN, 0, 0), (channel.OPEN, 1, 1),
    (channel.DATA, 1, 0), (channel.DATA, 1, channel.MAX_FRAME + 1)])
def test_invalid_frames_rejected_before_payload_allocation(kind, flow, length):
    with pytest.raises(channel.ChannelError):
        channel.read_frame(io.BytesIO(channel.HEADER.pack(kind, flow, length)))


def test_truncated_frame_fails_closed():
    with pytest.raises(channel.ChannelError):
        channel.read_frame(io.BytesIO(channel.HEADER.pack(channel.DATA, 1, 3) + b"x"))


def test_worker_cannot_select_arbitrary_ingress_target():
    with pytest.raises(ValueError):
        channel.MuxChannel(io.BytesIO(), io.BytesIO(), worker=True, target=("127.0.0.1", 9000))


def test_worker_protocol_keeps_stdout_clean_and_stdin_is_not_a_socket():
    code = """
import os, socket
from matcreator.scripts.local_srt_worker import protocol_streams
reader, writer = protocol_streams()
try:
    socket.socket(fileno=os.dup(0))
except OSError:
    writer.write(b'pipe-only')
else:
    raise RuntimeError('Host socket capability was inherited')
print('application-log', flush=True)
reader.close()
writer.close()
"""
    result = subprocess.run([sys.executable, "-c", code], input=b"", capture_output=True, timeout=10)
    assert result.returncode == 0, result.stderr
    assert result.stdout == b"pipe-only"
    assert b"application-log" in result.stderr


def test_unknown_flow_and_worker_originated_open_fail_account():
    for packet in (channel.HEADER.pack(channel.DATA, 55, 1) + b"x", channel.HEADER.pack(channel.OPEN, 1, 0)):
        mux = channel.MuxChannel(io.BytesIO(packet), io.BytesIO())
        mux.start()
        assert mux.stop_event.wait(2)
        assert mux.error


@pytest.fixture
def pipe_pair():
    streams = []
    def pipe():
        read, write = os.pipe()
        pair = os.fdopen(read, "rb", buffering=0), os.fdopen(write, "wb", buffering=0)
        streams.extend(pair)
        return pair
    host_read, worker_write = pipe()
    worker_read, host_write = pipe()
    yield host_read, host_write, worker_read, worker_write
    # Close writers first so Windows blocking pipe reads observe EOF.
    for stream in sorted(streams, key=lambda item: item.readable()):
        stream.close()


@pytest.mark.parametrize("round_number", range(3))
def test_pipe_mux_parallel_large_streams_half_close_and_releases_slots(pipe_pair, monkeypatch, round_number):
    echo = socket.socket()
    echo.bind(("127.0.0.1", 0))
    echo.listen(10)
    echo.settimeout(0.2)
    stop = threading.Event()
    def echo_connection(connection):
        with connection:
            while chunk := connection.recv(65536):
                connection.sendall(chunk)
    def accept():
        while not stop.is_set():
            try:
                connection, _ = echo.accept()
            except socket.timeout:
                continue
            except OSError:
                return
            threading.Thread(target=echo_connection, args=(connection,), daemon=True).start()
    threading.Thread(target=accept, daemon=True).start()
    original_connect = socket.create_connection
    def sandbox_connect(target, timeout=1, **kwargs):
        assert target == ("127.0.0.1", 8001)
        return original_connect(echo.getsockname(), timeout=timeout)
    monkeypatch.setattr(channel.socket, "create_connection", sandbox_connect)
    host_read, host_write, worker_read, worker_write = pipe_pair
    host = channel.MuxChannel(host_read, host_write)
    inner = channel.MuxChannel(worker_read, worker_write, worker=True)
    ingress = supervisor.HostIngress()
    ingress.start(host)
    inner.start()
    payloads = [os.urandom(2 * 1024 * 1024), b"GET /socket HTTP/1.1\r\nUpgrade: websocket\r\n\r\n" + b"\x81\x05hello" * 50]
    errors = []
    def exchange(payload):
        try:
            connection = original_connect(("127.0.0.1", ingress.port), timeout=10)
            with connection:
                def send():
                    connection.sendall(payload)
                    connection.shutdown(socket.SHUT_WR)
                sender = threading.Thread(target=send, daemon=True)
                sender.start()
                result = bytearray()
                while chunk := connection.recv(65536):
                    result.extend(chunk)
                sender.join(2)
                assert bytes(result) == payload, (len(result), len(payload), host.error, inner.error)
        except BaseException as exc:
            errors.append(exc)
    threads = [threading.Thread(target=exchange, args=(payload,)) for payload in payloads]
    try:
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(20)
            assert not thread.is_alive()
        assert not errors, errors
        wait_for(lambda: not host.flows and not inner.flows)
        assert not host.error and not inner.error
        assert len(host.threads) <= 2 + 2 * channel.MAX_FLOWS
    finally:
        ingress.close()
        inner.close()
        stop.set()
        echo.close()


def test_signed_readiness_rejects_wrong_proof_and_non_object(monkeypatch):
    spec = {"instance_id": "1" * 32, "access_token": "A" * 43}
    class Connection:
        value = []
        def __init__(self, *args, **kwargs): pass
        def request(self, method, path): self.nonce = path.split("nonce=")[1]
        def getresponse(self): return self
        status = 200
        def read(self, limit): return json.dumps(self.value).encode()
        def close(self): pass
    monkeypatch.setattr(supervisor.http.client, "HTTPConnection", Connection)
    assert supervisor._ready(1, spec, "web") is None
    Connection.value = {"instance_id": spec["instance_id"], "audience": "web", "pid": 42, "proof": "bad"}
    assert supervisor._ready(1, spec, "web") is None
    monkeypatch.setattr(supervisor.secrets, "token_hex", lambda _: "nonce")
    Connection.value["proof"] = hmac.new(spec["access_token"].encode(),
        f"mc-local-server-v1|{spec['instance_id']}|web|42|nonce".encode(), hashlib.sha256).hexdigest()
    assert supervisor._ready(1, spec, "web") == 42


def test_rotating_log_redaction_spans_chunks():
    class Stream:
        values = iter([b"prefix sec", b"ret suffix", b""])
        def read1(self, count): return next(self.values)
    class Log:
        values = []
        def info(self, value): self.values.append(value)
    log = Log()
    supervisor._log_output(Stream(), log, "secret")
    assert "secret" not in "".join(log.values)
    assert "[redacted]" in "".join(log.values)


def test_adk_failure_stops_web_and_leaves_unrelated_process_alive(tmp_path, monkeypatch):
    # Harmless local child probes, never a real MC server or scientific job.
    monkeypatch.setattr(worker.os, "environ", dict(os.environ))
    original_environment = worker.worker_environment
    monkeypatch.setattr(worker, "worker_environment", lambda env, source: original_environment(env, "/opt/shared/app"))
    class Channel:
        stop_event = threading.Event()
        error = None
        def start(self): pass
        def close(self): self.stop_event.set()
    class Server:
        should_exit = False
        def run(self): wait_for(lambda: self.should_exit)
    unrelated = subprocess.Popen([sys.executable, "-c", "import time;time.sleep(20)"], stdin=subprocess.DEVNULL)
    try:
        result = worker.run_stack(Channel(), {"MATCREATOR_LOCAL_ACCOUNT_ID": ACCOUNT, "MATCREATOR_HOME": "/probe/alice"},
            str(tmp_path), _server_factory=Server, _adk_command=[sys.executable, "-c", "raise SystemExit(7)"])
        assert result == 7
        assert unrelated.poll() is None
    finally:
        unrelated.terminate()
        unrelated.wait(timeout=5)
