from __future__ import annotations

from pathlib import Path

import docker

from matcreator.control_plane.worker_supervisor import WorkerSupervisor


class _FakeImage:
    id = "sha256:worker-image"


class _FakeImages:
    def get(self, image: str):
        assert image == "matcreator:test"
        return _FakeImage()


class _FakeContainer:
    def __init__(self) -> None:
        self.image = _FakeImage()
        self.status = "running"
        self.ports = {}
        self.started = False

    def start(self) -> None:
        self.started = True
        self.status = "running"

    def stop(self, timeout: int) -> None:
        assert timeout == 10
        self.status = "exited"

    def remove(self, force: bool) -> None:
        assert force is True


class _FakeContainers:
    def __init__(self) -> None:
        self.created: list[dict] = []
        self.by_name: dict[str, _FakeContainer] = {}
        self.conflict_on_next_run = False

    def get(self, name: str) -> _FakeContainer:
        try:
            return self.by_name[name]
        except KeyError as exc:
            raise docker.errors.NotFound("container not found") from exc

    def run(self, **kwargs) -> _FakeContainer:
        container = _FakeContainer()
        self.by_name[kwargs["name"]] = container
        self.created.append(kwargs)
        if self.conflict_on_next_run:
            self.conflict_on_next_run = False
            response = type("Response", (), {"status_code": 409})()
            raise docker.errors.APIError("container name already in use", response=response)
        return container


class _FakeDockerClient:
    def __init__(self) -> None:
        self.images = _FakeImages()
        self.containers = _FakeContainers()


def _supervisor(tmp_path: Path, docker_client: _FakeDockerClient) -> WorkerSupervisor:
    def user_home(user_id: str, host: bool) -> Path:
        return tmp_path / ("host" if host else "container") / user_id / ".matcreator"

    supervisor = WorkerSupervisor(
        image="matcreator:test",
        network="matcreator-net",
        connect_mode="network",
        base_port=9001,
        adk_port=lambda: 8000,
        user_home=user_home,
        worker_environment=lambda: {"LLM_MODEL": "test-model"},
        shared_mounts=lambda: {"/srv/share": {"bind": "/share", "mode": "ro"}},
        memory_limit="2g",
        cpus="1.5",
        pids_limit="256",
    )
    supervisor._docker_client = docker_client
    return supervisor


def test_supervisor_creates_persistent_user_worker(tmp_path: Path) -> None:
    docker_client = _FakeDockerClient()
    supervisor = _supervisor(tmp_path, docker_client)

    target = supervisor.ensure_running("alice")

    assert target == "http://matcreator-worker-alice:8000"
    assert (tmp_path / "container" / "alice" / ".matcreator").is_dir()
    created = docker_client.containers.created[0]
    assert created["environment"] == {
        "LLM_MODEL": "test-model",
        "MATCREATOR_MODE": "server",
        "MATCREATOR_USER_ID": "alice",
    }
    assert created["volumes"] == {
        str(tmp_path / "host" / "alice" / ".matcreator"): {"bind": "/root/.matcreator", "mode": "rw"},
        "/srv/share": {"bind": "/share", "mode": "ro"},
    }
    assert created["network"] == "matcreator-net"
    assert created["mem_limit"] == "2g"
    assert created["nano_cpus"] == 1_500_000_000
    assert created["pids_limit"] == 256


def test_supervisor_reuses_worker_and_selects_idle_users(tmp_path: Path) -> None:
    docker_client = _FakeDockerClient()
    supervisor = _supervisor(tmp_path, docker_client)

    assert supervisor.ensure_running("alice") == "http://matcreator-worker-alice:8000"
    assert supervisor.ensure_running("alice") == "http://matcreator-worker-alice:8000"

    assert len(docker_client.containers.created) == 1
    assert supervisor.target_for("alice") == "http://matcreator-worker-alice:8000"
    assert supervisor.idle_users(60, now=supervisor._last_used["alice"] + 59) == []
    assert supervisor.idle_users(60, now=supervisor._last_used["alice"] + 61) == ["alice"]


def test_supervisor_adopts_worker_created_concurrently(tmp_path: Path) -> None:
    docker_client = _FakeDockerClient()
    docker_client.containers.conflict_on_next_run = True
    supervisor = _supervisor(tmp_path, docker_client)

    target = supervisor.ensure_running("alice")

    assert target == "http://matcreator-worker-alice:8000"
    assert supervisor.target_for("alice") == target
    assert len(docker_client.containers.created) == 1