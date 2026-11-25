"""Offline SSH proxy contract checks. No SSH server or external host is used."""
from __future__ import annotations

import builtins
import importlib.util
from pathlib import Path
import socket
import threading

import pytest


FILE = Path(__file__).resolve().parents[1] / "src/matcreator/skills/paramiko/scripts/ssh_client.py"
SPEC = importlib.util.spec_from_file_location("mc_ssh_srt_proxy_test_module", FILE)
SSH = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(SSH)


class FakeSSHClient:
    def __init__(self):
        self.calls = []
        self.tunnel_marker = None

    def set_missing_host_key_policy(self, policy):
        pass

    def connect(self, **kwargs):
        self.calls.append(kwargs)
        if "sock" in kwargs:
            self.tunnel_marker = kwargs["sock"].recv(100)

    def close(self):
        if self.calls and "sock" in self.calls[-1]:
            self.calls[-1]["sock"].close()


@pytest.fixture
def environment(monkeypatch):
    for name in ("SOCKS_SERVER", "FTP_PROXY", "ftp_proxy", "ALL_PROXY", "all_proxy", "MATCREATOR_LOCAL_SRT_WORKER"):
        monkeypatch.delenv(name, raising=False)
    client = FakeSSHClient()
    monkeypatch.setattr(SSH.paramiko, "SSHClient", lambda: client)
    return client


def remote():
    return SSH.SSHRemote(host="never-resolve.invalid", port=2222, username="synthetic-user", password="synthetic-ssh-password", timeout=3)


def exact(connection, count):
    data = bytearray()
    while len(data) < count:
        part = connection.recv(count - len(data))
        if not part:
            raise RuntimeError("Incomplete local SOCKS request")
        data.extend(part)
    return bytes(data)


def socks_stub(*, denied=False):
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    listener.listen(1)
    listener.settimeout(5)
    seen, errors = {}, []
    def serve():
        try:
            with listener.accept()[0] as connection:
                connection.settimeout(4)
                greeting = exact(connection, 2)
                methods = exact(connection, greeting[1])
                assert greeting[0] == 5 and 2 in methods
                connection.sendall(b"\x05\x02")
                auth = exact(connection, 2)
                assert auth[0] == 1
                seen["username"] = exact(connection, auth[1]).decode()
                seen["password"] = exact(connection, exact(connection, 1)[0]).decode()
                connection.sendall(b"\x01\x00")
                request = exact(connection, 4)
                assert request == b"\x05\x01\x00\x03"  # host name, not locally resolved IP
                seen["target"] = exact(connection, exact(connection, 1)[0]).decode()
                seen["port"] = int.from_bytes(exact(connection, 2), "big")
                connection.sendall(bytes([5, 2 if denied else 0, 0, 1, 127, 0, 0, 1, 0, 0]))
                if not denied:
                    connection.sendall(b"local-socks-tunnel-marker")
                try:
                    seen["client_closed"] = connection.recv(1) == b""
                except ConnectionResetError:
                    # Windows may reset when PySocks closes after a denied
                    # reply with unread bound-address bytes.
                    seen["client_closed"] = True
        except Exception as exc:
            errors.append(exc)
        finally:
            listener.close()
    thread = threading.Thread(target=serve, daemon=True)
    thread.start()
    return listener.getsockname()[1], seen, errors, thread


def test_process_mode_preserves_existing_paramiko_connection(environment, monkeypatch):
    monkeypatch.setenv("FTP_PROXY", "socks5h://invalid-proxy.invalid:1234")
    value = remote()
    value.connect()
    assert value._connected and len(environment.calls) == 1
    assert "sock" not in environment.calls[0]
    assert environment.calls[0]["allow_agent"] is False
    assert environment.calls[0]["look_for_keys"] is False


def test_srt_uses_real_local_socks_tunnel_remote_dns_and_explicit_paramiko_socket(environment, monkeypatch):
    original_socket = socket.socket
    port, seen, errors, thread = socks_stub()
    monkeypatch.setenv("MATCREATOR_LOCAL_SRT_WORKER", "1")
    monkeypatch.setenv("FTP_PROXY", f"socks5h://srt.test%2Dcommand:proxy%3Atest@127.0.0.1:{port}")
    monkeypatch.setenv("NO_PROXY", "*")
    lookup = socket.getaddrinfo
    def guarded_lookup(host, *args, **kwargs):
        if host == "never-resolve.invalid":
            pytest.fail("Target DNS must be resolved by the policy proxy")
        return lookup(host, *args, **kwargs)
    monkeypatch.setattr(socket, "getaddrinfo", guarded_lookup)
    value = remote()
    try:
        value.connect()
        assert environment.tunnel_marker == b"local-socks-tunnel-marker"
        assert environment.calls[0]["hostname"] == "never-resolve.invalid"
        assert environment.calls[0]["sock"] is value._proxy_socket
        assert socket.socket is original_socket  # No process-wide monkeypatch.
    finally:
        value.disconnect()
        thread.join(5)
    assert not thread.is_alive() and not errors
    assert seen == {"username": "srt.test-command", "password": "proxy:test", "target": "never-resolve.invalid", "port": 2222, "client_closed": True}


def test_srt_proxy_rejection_never_attempts_direct_ssh(environment, monkeypatch):
    port, seen, errors, thread = socks_stub(denied=True)
    monkeypatch.setenv("MATCREATOR_LOCAL_SRT_WORKER", "1")
    monkeypatch.setenv("SOCKS_SERVER", f"socks5h://srt.test:proxy-secret@127.0.0.1:{port}")
    with pytest.raises(ConnectionError, match="direct connections are disabled") as error:
        remote().connect()
    thread.join(5)
    assert "proxy-secret" not in str(error.value)
    assert not environment.calls and not errors and seen["client_closed"] is True


@pytest.mark.parametrize("proxy", [None, "http://localhost:1234", "socks5h://external.invalid:1234", "socks5h://127.0.0.1:bad", "socks5h://127.0.0.1:1234/path"])
def test_missing_or_invalid_srt_proxy_fails_before_paramiko(environment, monkeypatch, proxy):
    monkeypatch.setenv("MATCREATOR_LOCAL_SRT_WORKER", "1")
    if proxy:
        monkeypatch.setenv("FTP_PROXY", proxy)
    monkeypatch.setenv("ALL_PROXY", "http://localhost:7897")
    with pytest.raises(ConnectionError, match="direct connections are disabled"):
        remote().connect()
    assert not environment.calls


def test_missing_pysocks_does_not_fall_back_to_direct_connect(environment, monkeypatch):
    monkeypatch.setenv("MATCREATOR_LOCAL_SRT_WORKER", "1")
    monkeypatch.setenv("FTP_PROXY", "socks5h://127.0.0.1:1234")
    original = builtins.__import__
    def no_socks(name, *args, **kwargs):
        if name == "socks":
            raise ImportError("synthetic missing dependency")
        return original(name, *args, **kwargs)
    monkeypatch.setattr(builtins, "__import__", no_socks)
    with pytest.raises(ConnectionError, match="PySocks"):
        remote().connect()
    assert not environment.calls
