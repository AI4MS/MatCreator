from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import pytest
from fastapi import HTTPException


WEB_DIR = Path(__file__).resolve().parents[1] / "web"
if str(WEB_DIR) not in sys.path:
    sys.path.insert(0, str(WEB_DIR))

import main


def test_benchmark_client_reads_benchmark_section_from_config(monkeypatch) -> None:
    monkeypatch.delenv("MAT_BENCH_SERVER_URL", raising=False)
    monkeypatch.delenv("MAT_BENCH_TOKEN", raising=False)
    monkeypatch.setattr(
        main,
        "load_config",
        lambda: {"benchmark": {"server_url": "https://bench.example/", "token": "config-token"}},
    )

    client = main._benchmark_client()

    assert client.base_url == "https://bench.example"
    assert client.token == "config-token"


def test_benchmark_environment_overrides_config(monkeypatch) -> None:
    monkeypatch.setenv("MAT_BENCH_SERVER_URL", "https://env.example")
    monkeypatch.setenv("MAT_BENCH_TOKEN", "env-token")
    monkeypatch.setattr(
        main,
        "load_config",
        lambda: {"benchmark": {"server_url": "https://config.example", "token": "config-token"}},
    )

    client = main._benchmark_client()

    assert client.base_url == "https://env.example"
    assert client.token == "env-token"


def test_benchmark_client_requires_complete_configuration(monkeypatch) -> None:
    monkeypatch.delenv("MAT_BENCH_SERVER_URL", raising=False)
    monkeypatch.delenv("MAT_BENCH_TOKEN", raising=False)
    monkeypatch.setattr(main, "load_config", lambda: {"benchmark": {"server_url": "https://bench.example"}})

    with pytest.raises(HTTPException, match="benchmark.server_url"):
        main._benchmark_client()


def test_benchmark_client_registers_and_persists_missing_development_token(monkeypatch) -> None:
    monkeypatch.delenv("MAT_BENCH_SERVER_URL", raising=False)
    monkeypatch.delenv("MAT_BENCH_TOKEN", raising=False)
    config = {"benchmark": {"server_url": "https://bench.example"}}
    saved = []
    monkeypatch.setattr(main, "_load_config_for_user", lambda _owner: config)
    monkeypatch.setattr(main, "_save_config_for_user", lambda value, owner: saved.append((value, owner)))

    async def register(server_url: str) -> str:
        assert server_url == "https://bench.example"
        return "registered-token"

    monkeypatch.setattr(main.BenchmarkClient, "register_token", register)

    client = asyncio.run(main._benchmark_client_for_owner("alice"))

    assert client.token == "registered-token"
    assert saved == [
        ({"benchmark": {"server_url": "https://bench.example", "token": "registered-token"}}, "alice")
    ]


def test_benchmark_client_does_not_register_when_environment_token_exists(monkeypatch) -> None:
    monkeypatch.setenv("MAT_BENCH_SERVER_URL", "https://bench.example")
    monkeypatch.setenv("MAT_BENCH_TOKEN", "environment-token")
    monkeypatch.setattr(main, "_load_config_for_user", lambda _owner: {"benchmark": {}})

    async def unexpected_registration(_server_url: str) -> str:
        raise AssertionError("registration should not be called")

    monkeypatch.setattr(main.BenchmarkClient, "register_token", unexpected_registration)

    client = asyncio.run(main._benchmark_client_for_owner("alice"))

    assert client.base_url == "https://bench.example"
    assert client.token == "environment-token"


def test_benchmark_client_registers_one_token_for_concurrent_first_use(monkeypatch) -> None:
    monkeypatch.delenv("MAT_BENCH_SERVER_URL", raising=False)
    monkeypatch.delenv("MAT_BENCH_TOKEN", raising=False)
    config = {"benchmark": {"server_url": "https://bench.example"}}
    registrations = 0

    monkeypatch.setattr(main, "_load_config_for_user", lambda _owner: config)
    monkeypatch.setattr(main, "_save_config_for_user", lambda value, _owner: config.update(value))

    async def register(_server_url: str) -> str:
        nonlocal registrations
        registrations += 1
        await asyncio.sleep(0)
        return "registered-once"

    monkeypatch.setattr(main.BenchmarkClient, "register_token", register)

    async def exercise() -> list[main.BenchmarkClient]:
        return await asyncio.gather(
            main._benchmark_client_for_owner("alice"),
            main._benchmark_client_for_owner("alice"),
        )

    clients = asyncio.run(exercise())

    assert registrations == 1
    assert [client.token for client in clients] == ["registered-once", "registered-once"]