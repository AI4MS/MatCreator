"""Serve one account's complete Web/ADK stack inside its SRT namespace.

Anonymous stdin/stdout pipes carry bounded ingress frames. All network sockets
are created in this worker's network namespace and target its own Web server.
"""
from __future__ import annotations

import os
from pathlib import Path, PurePosixPath
import signal
import subprocess
import sys
import threading
import uuid


class SrtWorkerError(RuntimeError):
    pass


def protocol_streams():
    """Save stdout for protocol bytes before application imports print logs."""
    import stat
    if not stat.S_ISFIFO(os.fstat(0).st_mode) or not stat.S_ISFIFO(os.fstat(1).st_mode):
        raise SrtWorkerError("SRT ingress requires anonymous stdin/stdout pipes")
    reader = os.fdopen(os.dup(0), "rb", buffering=0)
    writer = os.fdopen(os.dup(1), "wb", buffering=0)
    sys.stdout.flush()
    os.dup2(2, 1)
    return reader, writer


def worker_environment(environment: dict[str, str], source_root: str) -> dict[str, str]:
    account_id = environment.get("MATCREATOR_LOCAL_ACCOUNT_ID", "")
    try:
        if str(uuid.UUID(account_id)) != account_id:
            raise ValueError
    except (ValueError, AttributeError) as exc:
        raise SrtWorkerError("A verified account identity is required") from exc
    home_text = environment.get("MATCREATOR_HOME", "")
    if not home_text.startswith("/") or home_text == "/" or ".." in home_text.split("/"):
        raise SrtWorkerError("A private Linux profile home is required")
    home = PurePosixPath(home_text)
    root = PurePosixPath(source_root)
    if not root.is_absolute():
        raise SrtWorkerError("Shared application source must use an absolute Linux path")
    removed = {"NODE_OPTIONS", "NODE_PATH", "LD_PRELOAD", "LD_LIBRARY_PATH", "PYTHONSTARTUP",
               "PYTHONHOME", "PYTHONUSERBASE", "MATCREATOR_LOCAL_SRT_SPEC", "MATCREATOR_LOCAL_SRT_STATE"}
    env = {key: value for key, value in environment.items()
           if key not in removed and not key.startswith(("MATCREATOR_CONTAINER_BOOT_", "MC_SRT_BOOT_"))}
    env.update({
        "HOME": str(home), "PYTHONPATH": str(root / "src") + ":" + str(root),
        "PYTHONNOUSERSITE": "1", "PYTHONDONTWRITEBYTECODE": "1", "PYTHONUNBUFFERED": "1",
        "MATCREATOR_MODE": "local", "MATCREATOR_LOCAL_RUNTIME": "process",
        "MATCREATOR_LOCAL_PROFILE_WORKER": "1", "MATCREATOR_LOCAL_SRT_WORKER": "1",
        "MATCREATOR_LOCAL_ACCOUNT_ID": account_id, "MATCREATOR_LOCAL_ACCOUNT_ROOT": "/mc-broker-boundary",
        "MATCREATOR_HOME": str(home), "MATCREATOR_CONFIG_PATH": str(home / "config.yaml"),
        "MATCLAW_WORKSPACE": str(home / "workspace"),
        "MATCREATOR_ADK_HOST": "127.0.0.1", "MATCREATOR_ADK_PORT": "8000", "ADK_LOCAL_PORT": "8000",
        "MATCREATOR_WEB_HOST": "127.0.0.1", "MATCREATOR_WEB_PORT": "8001", "MATCREATOR_FRONTEND_PORT": "8001",
        "TMPDIR": str(home / "tmp"), "CLAUDE_CODE_TMPDIR": str(home / "tmp"),
        "XDG_CACHE_HOME": str(home / ".cache"), "MPLCONFIGDIR": str(home / ".cache" / "matplotlib"),
    })
    return env


def stop_adk(process: subprocess.Popen, grace_seconds: float = 8.0) -> None:
    if process.poll() is None:
        try:
            if os.name == "posix":
                os.killpg(process.pid, signal.SIGTERM)
            else:
                process.terminate()
        except ProcessLookupError:
            pass
        try:
            process.wait(timeout=grace_seconds)
        except subprocess.TimeoutExpired:
            if os.name == "posix":
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
            else:
                process.kill()
            process.wait(timeout=5)


def run_stack(channel, environment: dict[str, str], source_root: str,
              *, _server_factory=None, _adk_command=None) -> int:
    env = worker_environment(environment, source_root)
    os.environ.clear()
    os.environ.update(env)
    command = _adk_command or [sys.executable, "-m", "matcreator.scripts.start_agent", "api-server",
                               "--host", "127.0.0.1", "--port", "8000", "--workspace",
                               str(PurePosixPath(env["MATCREATOR_HOME"]) / "workspace"), "--log-level", "info"]
    adk = subprocess.Popen(command, env=env, cwd=source_root, stdin=subprocess.DEVNULL,
                           close_fds=True, start_new_session=os.name == "posix")
    try:
        if _server_factory is None:
            import uvicorn
            server = uvicorn.Server(uvicorn.Config("web.main:app", host="127.0.0.1", port=8001,
                                                  loop="asyncio", log_level="info"))
        else:
            server = _server_factory()
        def watch_adk():
            adk.wait()
            server.should_exit = True
        watcher = threading.Thread(target=watch_adk, name="mc-srt-adk", daemon=True)
        watcher.start()
        def watch_channel():
            channel.stop_event.wait()
            server.should_exit = True
        threading.Thread(target=watch_channel, name="mc-srt-channel", daemon=True).start()
        channel.start()
        server.run()
        code = adk.poll()
        return code if code is not None and code > 0 else (1 if code is not None or channel.error else 0)
    finally:
        stop_adk(adk)
        channel.close()


def main() -> int:
    try:
        if os.name != "posix" or os.geteuid() == 0:
            raise SrtWorkerError("SRT account workers must run as an unprivileged Linux account")
        source_root = str(Path(__file__).resolve().parents[3])
        env = worker_environment(dict(os.environ), source_root)
        home = Path(env["MATCREATOR_HOME"])
        if home.is_symlink() or home.stat().st_uid != os.geteuid():
            raise SrtWorkerError("Profile ownership does not match the runtime account")
        from matcreator.scripts.local_srt_channel import MuxChannel
        reader, writer = protocol_streams()
        try:
            channel = MuxChannel(reader, writer, worker=True)
            return run_stack(channel, env, source_root)
        finally:
            reader.close()
            writer.close()
    except (SrtWorkerError, OSError) as exc:
        print(f"SRT account worker refused: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
