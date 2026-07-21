"""Static tests reading docker-compose files as text. No Docker required.

These tests verify that compose files use environment variable substitution
for port configuration rather than hardcoded values.
"""

from __future__ import annotations

import os

import pytest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _read_compose_file(filename: str) -> str:
    """Read a docker-compose file from the repo root, relative to this test file."""
    path = os.path.join(os.path.dirname(__file__), "..", filename)
    with open(path, encoding="utf-8") as fh:
        return fh.read()


# ===================================================================
# docker-compose.yml tests
# ===================================================================


def test_docker_compose_has_env_var_ports() -> None:
    """docker-compose.yml must not contain hardcoded port mappings.

    Instead of literal ``"8000:8000"``, ``"8001:8001"``, ``"5173:5173"``
    the file should use env var substitution patterns like
    ``${MATCREATOR_ADK_HOST_PORT`` or ``${MATCREATOR_WEB_HOST_PORT``.
    """
    content = _read_compose_file("docker-compose.yml")

    # Must NOT contain hardcoded host:container port pairs
    assert '"8000:8000"' not in content, (
        "docker-compose.yml should not contain hardcoded '8000:8000'"
    )
    assert '"8001:8001"' not in content, (
        "docker-compose.yml should not contain hardcoded '8001:8001'"
    )
    assert '"5173:5173"' not in content, (
        "docker-compose.yml should not contain hardcoded '5173:5173'"
    )

    # Must contain env var substitution patterns
    has_adk_host_port = "${MATCREATOR_ADK_HOST_PORT" in content
    has_web_host_port = "${MATCREATOR_WEB_HOST_PORT" in content
    assert has_adk_host_port or has_web_host_port, (
        "docker-compose.yml should use ${MATCREATOR_ADK_HOST_PORT "
        "or ${MATCREATOR_WEB_HOST_PORT} for port configuration"
    )


# ===================================================================
# docker-compose.server.yml tests
# ===================================================================


def test_docker_compose_server_has_env_var_ports() -> None:
    """docker-compose.server.yml must not contain hardcoded port mappings.

    Instead of literal ``"80:80"`` or ``"8001:8001"`` the file should use
    env var patterns like ``${MATCREATOR_SERVER_PROXY_HOST_PORT`` or
    ``${MATCREATOR_SERVER_PROXY_PORT``.
    """
    content = _read_compose_file("docker-compose.server.yml")

    # Must NOT contain hardcoded host:container port pairs
    assert '"80:80"' not in content, (
        "docker-compose.server.yml should not contain hardcoded '80:80'"
    )
    assert '"8001:8001"' not in content, (
        "docker-compose.server.yml should not contain hardcoded '8001:8001'"
    )

    # Must contain env var substitution patterns for server proxy
    has_proxy_host_port = "${MATCREATOR_SERVER_PROXY_HOST_PORT" in content
    has_proxy_port = "${MATCREATOR_SERVER_PROXY_PORT" in content
    assert has_proxy_host_port or has_proxy_port, (
        "docker-compose.server.yml should use ${MATCREATOR_SERVER_PROXY_HOST_PORT "
        "or ${MATCREATOR_SERVER_PROXY_PORT} for port configuration"
    )


def test_docker_compose_server_healthcheck_uses_web_port() -> None:
    """Healthcheck in docker-compose.server.yml must use env var, not hardcoded port.

    The healthcheck URL should reference ``${MATCREATOR_WEB_PORT`` rather than
    the hardcoded value ``8001``.
    """
    content = _read_compose_file("docker-compose.server.yml")

    # The healthcheck must use the env var pattern
    assert "${MATCREATOR_WEB_PORT" in content, (
        "docker-compose.server.yml healthcheck should use ${MATCREATOR_WEB_PORT}"
    )

    # Verify the healthcheck section exists and references the env var
    assert "healthcheck" in content.lower(), (
        "docker-compose.server.yml should contain a healthcheck section"
    )

    # Extract the healthcheck line and verify it uses the env var
    lines = content.splitlines()
    healthcheck_lines = [
        line for line in lines
        if "healthcheck" in line.lower() or ("test" in line.lower() and "curl" in line)
    ]
    # At least one curl-based healthcheck should reference the env var
    curl_with_env = any(
        "${MATCREATOR_WEB_PORT" in line for line in healthcheck_lines
    )
    assert curl_with_env, (
        "Healthcheck curl command should reference ${MATCREATOR_WEB_PORT}"
    )


def test_docker_compose_server_separates_control_plane_and_worker_images() -> None:
    """Server mode must build distinct control-plane and worker image targets."""
    content = _read_compose_file("docker-compose.server.yml")

    assert "target: control-plane" in content
    assert "target: worker" in content
    assert "${MATCREATOR_CONTROL_PLANE_IMAGE:-matcreator-control-plane:latest}" in content
    assert "${MATCREATOR_WORKER_IMAGE:-matcreator-worker:latest}" in content
    assert "MATCREATOR_WORKER_IMAGE=${MATCREATOR_WORKER_IMAGE:-matcreator-worker:latest}" in content
