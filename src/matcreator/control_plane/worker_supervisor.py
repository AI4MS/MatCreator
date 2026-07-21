"""Lifecycle supervision for persistent per-user MatCreator workers.

The supervisor is deliberately unaware of HTTP, authentication, and local
mode. The control plane supplies user-home and environment resolvers, while
this module owns Docker reconciliation and the worker runtime registry.
"""

from __future__ import annotations

import logging
import re
import threading
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


class WorkerSupervisor:
    """Provision and supervise one persistent ADK worker per user."""

    def __init__(
        self,
        *,
        image: str,
        network: str,
        connect_mode: str,
        base_port: int,
        adk_port: Callable[[], int],
        user_home: Callable[[str, bool], Path],
        worker_environment: Callable[[], dict[str, str]],
        shared_mounts: Callable[[], dict[str, dict[str, str]]],
        memory_limit: str = "",
        cpus: str = "",
        pids_limit: str = "",
    ) -> None:
        if connect_mode not in {"network", "host-port"}:
            raise ValueError("connect_mode must be 'network' or 'host-port'")
        self.image = image
        self.network = network
        self.connect_mode = connect_mode
        self.base_port = base_port
        self.adk_port = adk_port
        self.user_home = user_home
        self.worker_environment = worker_environment
        self.shared_mounts = shared_mounts
        self.memory_limit = memory_limit
        self.cpus = cpus
        self.pids_limit = pids_limit
        self._registry: dict[str, str] = {}
        self._ports: dict[str, int] = {}
        self._last_used: dict[str, float] = {}
        self._lock = threading.Lock()
        self._docker_client: Any = None

    @staticmethod
    def container_name(user_id: str) -> str:
        safe = re.sub(r"[^a-zA-Z0-9_-]", "-", user_id)[:64]
        return f"matcreator-worker-{safe}"

    def _get_docker(self):
        if self._docker_client is None:
            try:
                import docker

                self._docker_client = docker.from_env()
            except Exception as exc:
                raise RuntimeError(f"Docker unavailable: {exc}") from exc
        return self._docker_client

    def docker_client(self):
        """Return the Docker client used for worker lifecycle and exec operations."""
        return self._get_docker()

    def _target_url(self, user_id: str, port: int | None = None) -> str:
        if self.connect_mode == "host-port":
            if port is None:
                raise RuntimeError("host-port worker routing requires a host port")
            return f"http://127.0.0.1:{port}"
        return f"http://{self.container_name(user_id)}:{self.adk_port()}"

    def _next_free_port(self) -> int:
        port = self.base_port
        used = set(self._ports.values())
        while port in used:
            port += 1
        return port

    def _worker_image_id(self, docker_client) -> str:
        try:
            return docker_client.images.get(self.image).id or ""
        except Exception as exc:
            logger.warning("Could not resolve worker image %s: %s", self.image, exc)
            return ""

    @staticmethod
    def _container_image_id(container) -> str:
        image = getattr(container, "image", None)
        image_id = getattr(image, "id", "") or ""
        if image_id:
            return image_id
        attrs = getattr(container, "attrs", {}) or {}
        return str(attrs.get("Image") or "")

    def _uses_current_image(self, docker_client, container) -> bool:
        current_id = self._worker_image_id(docker_client)
        container_id = self._container_image_id(container)
        return bool(current_id and container_id and current_id == container_id)

    def _forget(self, user_id: str) -> None:
        self._registry.pop(user_id, None)
        self._ports.pop(user_id, None)
        self._last_used.pop(user_id, None)

    def _remove_container(self, user_id: str, container=None) -> None:
        try:
            docker_client = self._get_docker()
            target = container or docker_client.containers.get(self.container_name(user_id))
            target.remove(force=True)
        except Exception as exc:
            logger.warning("Failed to remove worker for user %s: %s", user_id, exc)
        self._forget(user_id)

    def _register_existing_worker(self, user_id: str, container) -> str:
        """Start and register a discovered worker container."""
        port = None
        if self.connect_mode == "host-port":
            bindings = container.ports.get(f"{self.adk_port()}/tcp") or []
            if not bindings:
                raise RuntimeError("Existing host-port worker has no ADK port binding")
            port = int(bindings[0]["HostPort"])
            self._ports[user_id] = port
        target = self._target_url(user_id, port)
        self._registry[user_id] = target
        if container.status != "running":
            container.start()
        self._last_used[user_id] = time.time()
        return target

    def ensure_running(self, user_id: str) -> str:
        """Start or create the user's persistent worker and return its ADK URL."""
        import docker

        with self._lock:
            name = self.container_name(user_id)
            docker_client = self._get_docker()
            if user_id in self._registry:
                target = self._registry[user_id]
                try:
                    container = docker_client.containers.get(name)
                    if not self._uses_current_image(docker_client, container):
                        self._remove_container(user_id, container)
                    else:
                        if container.status != "running":
                            container.start()
                        self._last_used[user_id] = time.time()
                        return target
                except docker.errors.NotFound:
                    self._forget(user_id)

            try:
                container = docker_client.containers.get(name)
                if not self._uses_current_image(docker_client, container):
                    self._remove_container(user_id, container)
                    container = None
                port = None
                if container is not None and self.connect_mode == "host-port":
                    bindings = container.ports.get(f"{self.adk_port()}/tcp") or []
                    if not bindings:
                        container.remove(force=True)
                        container = None
                    else:
                        port = int(bindings[0]["HostPort"])
                        self._ports[user_id] = port
                if container is not None:
                    return self._register_existing_worker(user_id, container)
            except docker.errors.NotFound:
                pass

            port = self._next_free_port() if self.connect_mode == "host-port" else None
            container_home = self.user_home(user_id, False)
            host_home = self.user_home(user_id, True)
            container_home.mkdir(parents=True, exist_ok=True)
            environment = self.worker_environment()
            environment.update({"MATCREATOR_MODE": "server", "MATCREATOR_USER_ID": user_id})
            adk_port = self.adk_port()
            volumes = {str(host_home): {"bind": "/root/.matcreator", "mode": "rw"}}
            volumes.update(self.shared_mounts())
            run_kwargs: dict[str, Any] = {
                "image": self.image,
                "command": ["matcreator", "api-server", "--host", "0.0.0.0", "--port", str(adk_port)],
                "name": name,
                "environment": environment,
                "volumes": volumes,
                "detach": True,
                "restart_policy": {"Name": "unless-stopped"},
            }
            if port is not None:
                run_kwargs["ports"] = {f"{adk_port}/tcp": port}
            if self.network:
                run_kwargs["network"] = self.network
            if self.memory_limit:
                run_kwargs["mem_limit"] = self.memory_limit
            if self.cpus:
                run_kwargs["nano_cpus"] = int(float(self.cpus) * 1_000_000_000)
            if self.pids_limit:
                run_kwargs["pids_limit"] = int(self.pids_limit)
            try:
                docker_client.containers.run(**run_kwargs)
            except docker.errors.APIError as exc:
                # Another control-plane process can create the deterministic
                # per-user container after our lookup and before this create.
                # Docker reports that benign race as HTTP 409; adopt it.
                if getattr(exc.response, "status_code", None) != 409:
                    raise
                try:
                    container = docker_client.containers.get(name)
                except docker.errors.NotFound:
                    raise exc
                return self._register_existing_worker(user_id, container)
            target = self._target_url(user_id, port)
            self._registry[user_id] = target
            if port is not None:
                self._ports[user_id] = port
            self._last_used[user_id] = time.time()
            return target

    def stop(self, user_id: str) -> None:
        try:
            self._get_docker().containers.get(self.container_name(user_id)).stop(timeout=10)
        except Exception as exc:
            logger.warning("Failed to stop worker for user %s: %s", user_id, exc)

    def remove(self, user_id: str) -> None:
        with self._lock:
            self._remove_container(user_id)

    def list_workers(self) -> list[dict[str, Any]]:
        try:
            import docker

            docker_client = self._get_docker()
            results = []
            with self._lock:
                workers = list(self._registry.items())
            for user_id, target in workers:
                try:
                    status = docker_client.containers.get(self.container_name(user_id)).status
                except docker.errors.NotFound:
                    status = "missing"
                results.append({
                    "user_id": user_id,
                    "container": self.container_name(user_id),
                    "target": target,
                    "port": self._ports.get(user_id),
                    "status": status,
                    "last_used": self._last_used.get(user_id),
                })
            return results
        except Exception as exc:
            return [{"error": str(exc)}]

    def target_for(self, user_id: str) -> str | None:
        """Return a previously registered worker target without starting it."""
        with self._lock:
            return self._registry.get(user_id)

    def idle_users(self, timeout_seconds: int, *, now: float | None = None) -> list[str]:
        if timeout_seconds <= 0:
            return []
        cutoff = (time.time() if now is None else now) - timeout_seconds
        with self._lock:
            return [user_id for user_id, last_used in self._last_used.items() if last_used < cutoff]