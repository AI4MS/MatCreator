#!/usr/bin/env python3
"""Generic SSH/SFTP CLI built on paramiko.

Scheduler-agnostic primitives for talking to any SSH-accessible server:
connect, run a command, transfer files/directories, list, mkdir. Anything
server-specific (job schedulers, module systems, ...) belongs in this skill's
``references/<server-name>.md`` as ``exec`` examples, not in this script.

Auth/connection flags fall back to generic environment variables:
    SSH_HOST, SSH_PORT, SSH_USER, SSH_PASSWORD, SSH_KEY_FILE,
    SSH_KEY_CONTENT, SSH_WORKDIR
"""

from __future__ import annotations

import argparse
import io
import json
import os
import stat
import sys
from pathlib import Path
from typing import Optional

import paramiko


class SSHRemote:
    """Thin wrapper around a paramiko SSH + SFTP connection."""

    def __init__(
        self,
        host: Optional[str] = None,
        port: Optional[int] = None,
        username: Optional[str] = None,
        password: Optional[str] = None,
        key_file: Optional[str] = None,
        key_content: Optional[str] = None,
        workdir: Optional[str] = None,
        timeout: int = 30,
    ):
        self.host = host or os.environ.get("SSH_HOST")
        self.port = int(port or os.environ.get("SSH_PORT", 22))
        self.username = username or os.environ.get("SSH_USER")
        self.password = password or os.environ.get("SSH_PASSWORD")
        self.key_file = key_file or os.environ.get("SSH_KEY_FILE")
        self.key_content = key_content or os.environ.get("SSH_KEY_CONTENT")
        self.workdir = workdir or os.environ.get("SSH_WORKDIR")
        self.timeout = timeout

        self.ssh: Optional[paramiko.SSHClient] = None
        self.sftp: Optional[paramiko.SFTPClient] = None
        self._connected = False

        if not self.host:
            raise ValueError("SSH_HOST environment variable or --host is required")
        if not self.username:
            raise ValueError("SSH_USER environment variable or --user is required")
        if not any([self.password, self.key_file, self.key_content]):
            raise ValueError(
                "One of SSH_PASSWORD, SSH_KEY_FILE, or SSH_KEY_CONTENT "
                "(or --password/--key-file/--key-content) must be set"
            )

    @staticmethod
    def _parse_key_content(key_content: str) -> paramiko.PKey:
        """Parse a private key from its string content, trying each key type."""
        key_classes = [paramiko.Ed25519Key, paramiko.RSAKey, paramiko.ECDSAKey]
        if hasattr(paramiko, "DSSKey"):
            key_classes.append(paramiko.DSSKey)
        last_error = None
        for key_cls in key_classes:
            try:
                return key_cls.from_private_key(io.StringIO(key_content))
            except (paramiko.SSHException, ValueError) as exc:
                last_error = exc
                continue
        raise ConnectionError(f"Failed to parse key content as any supported key type: {last_error}")

    def connect(self) -> None:
        if self._connected:
            return
        self.ssh = paramiko.SSHClient()
        self.ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())

        connect_kwargs = {
            "hostname": self.host,
            "port": self.port,
            "username": self.username,
            "timeout": self.timeout,
            "allow_agent": False,
            "look_for_keys": False,
        }
        if self.key_content:
            connect_kwargs["pkey"] = self._parse_key_content(self.key_content)
        elif self.key_file:
            connect_kwargs["key_filename"] = str(Path(self.key_file).expanduser())
        else:
            connect_kwargs["password"] = self.password

        try:
            self.ssh.connect(**connect_kwargs)
            self._connected = True
        except paramiko.AuthenticationException as exc:
            raise ConnectionError(f"Authentication failed: {exc}") from exc
        except Exception as exc:  # noqa: BLE001 - surface as ConnectionError for the CLI
            raise ConnectionError(f"SSH connection failed: {exc}") from exc

    def disconnect(self) -> None:
        if self.sftp:
            try:
                self.sftp.close()
            except Exception:
                pass
            self.sftp = None
        if self.ssh:
            try:
                self.ssh.close()
            except Exception:
                pass
            self.ssh = None
        self._connected = False

    def __enter__(self) -> "SSHRemote":
        self.connect()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        self.disconnect()

    def _ensure_sftp(self) -> paramiko.SFTPClient:
        self.connect()
        if self.sftp is None:
            self.sftp = self.ssh.open_sftp()
        return self.sftp

    # -- remote execution --------------------------------------------------

    def execute(self, cmd: str, timeout: Optional[int] = None):
        self.connect()
        _, stdout, stderr = self.ssh.exec_command(cmd, timeout=timeout or self.timeout * 10)
        exit_code = stdout.channel.recv_exit_status()
        return (
            stdout.read().decode("utf-8", errors="replace"),
            stderr.read().decode("utf-8", errors="replace"),
            exit_code,
        )

    # -- file operations -----------------------------------------------------

    def upload_file(self, local_path: str, remote_path: str) -> None:
        sftp = self._ensure_sftp()
        local_path = str(Path(local_path).resolve())
        if not os.path.exists(local_path):
            raise FileNotFoundError(f"Local file not found: {local_path}")
        sftp.put(local_path, remote_path)

    def download_file(self, remote_path: str, local_path: str) -> None:
        sftp = self._ensure_sftp()
        local_path = str(Path(local_path).resolve())
        if os.path.dirname(local_path):
            os.makedirs(os.path.dirname(local_path), exist_ok=True)
        sftp.get(remote_path, local_path)

    def upload_directory(self, local_dir: str, remote_dir: str) -> None:
        sftp = self._ensure_sftp()
        local_dir_path = Path(local_dir).resolve()
        if not local_dir_path.is_dir():
            raise NotADirectoryError(f"Not a directory: {local_dir_path}")
        try:
            sftp.stat(remote_dir)
        except FileNotFoundError:
            sftp.mkdir(remote_dir)
        for item in local_dir_path.iterdir():
            remote_item = f"{remote_dir}/{item.name}"
            if item.is_dir():
                self.upload_directory(str(item), remote_item)
            else:
                sftp.put(str(item), remote_item)

    def download_directory(self, remote_dir: str, local_dir: str) -> None:
        sftp = self._ensure_sftp()
        os.makedirs(local_dir, exist_ok=True)
        for attr in sftp.listdir_attr(remote_dir):
            remote_path = f"{remote_dir}/{attr.filename}"
            local_path = os.path.join(local_dir, attr.filename)
            if attr.st_mode is not None and stat.S_ISDIR(attr.st_mode):
                self.download_directory(remote_path, local_path)
            else:
                sftp.get(remote_path, local_path)

    def list_dir(self, remote_path: Optional[str] = None) -> list[dict]:
        sftp = self._ensure_sftp()
        path = remote_path or self.workdir
        if not path:
            raise ValueError("No path given and SSH_WORKDIR is not set")
        return [
            {
                "filename": attr.filename,
                "size": attr.st_size,
                "permissions": stat.filemode(attr.st_mode) if attr.st_mode else "unknown",
            }
            for attr in sftp.listdir_attr(path)
        ]

    def mkdir(self, path: str) -> None:
        sftp = self._ensure_sftp()
        try:
            sftp.mkdir(path)
        except IOError:
            pass  # already exists


def _fail(message: str) -> int:
    print(json.dumps({"status": "error", "error": message}))
    return 1


def main() -> int:
    parser = argparse.ArgumentParser(description="Generic paramiko SSH/SFTP CLI")
    parser.add_argument("--host", default=None, help="SSH host (or SSH_HOST)")
    parser.add_argument("--port", type=int, default=None, help="SSH port (or SSH_PORT)")
    parser.add_argument("--user", default=None, help="SSH user (or SSH_USER)")
    parser.add_argument("--password", default=None, help="SSH password/token (or SSH_PASSWORD)")
    parser.add_argument("--key-file", default=None, dest="key_file", help="Private key file path (or SSH_KEY_FILE)")
    parser.add_argument("--key-content", default=None, dest="key_content", help="Private key content (or SSH_KEY_CONTENT)")
    parser.add_argument("--workdir", default=None, help="Default remote directory (or SSH_WORKDIR)")
    parser.add_argument("--timeout", type=int, default=30, help="Connection timeout in seconds")

    subparsers = parser.add_subparsers(dest="command")
    subparsers.add_parser("connect", help="Test the SSH connection")

    exec_parser = subparsers.add_parser("exec", help="Execute a remote command")
    exec_parser.add_argument("cmd", nargs="+", help="Command and arguments")

    upload_parser = subparsers.add_parser("upload", help="Upload a file or directory")
    upload_parser.add_argument("local", help="Local path")
    upload_parser.add_argument("remote", help="Remote destination path")
    upload_parser.add_argument("-r", "--recursive", action="store_true", help="Upload a directory recursively")

    download_parser = subparsers.add_parser("download", help="Download a file or directory")
    download_parser.add_argument("remote", help="Remote path")
    download_parser.add_argument("local", help="Local destination path")
    download_parser.add_argument("-r", "--recursive", action="store_true", help="Download a directory recursively")

    ls_parser = subparsers.add_parser("ls", help="List a remote directory")
    ls_parser.add_argument("path", nargs="?", default=None, help="Remote path (default: --workdir/SSH_WORKDIR)")

    mkdir_parser = subparsers.add_parser("mkdir", help="Create a remote directory")
    mkdir_parser.add_argument("path", help="Remote directory path")

    args = parser.parse_args()
    if not args.command:
        parser.print_help()
        return 1

    try:
        remote = SSHRemote(
            host=args.host,
            port=args.port,
            username=args.user,
            password=args.password,
            key_file=args.key_file,
            key_content=args.key_content,
            workdir=args.workdir,
            timeout=args.timeout,
        )
    except ValueError as exc:
        return _fail(str(exc))

    try:
        if args.command == "connect":
            remote.connect()
            print(json.dumps({
                "status": "ok",
                "host": remote.host,
                "user": remote.username,
                "workdir": remote.workdir,
            }))

        elif args.command == "exec":
            cmd = " ".join(args.cmd)
            stdout, stderr, exit_code = remote.execute(cmd)
            print(json.dumps({
                "status": "ok" if exit_code == 0 else "error",
                "exit_code": exit_code,
                "stdout": stdout,
                "stderr": stderr,
            }))
            if exit_code != 0:
                return 1

        elif args.command == "upload":
            if args.recursive:
                remote.upload_directory(args.local, args.remote)
            else:
                remote.upload_file(args.local, args.remote)
            print(json.dumps({"status": "ok", "local": args.local, "remote": args.remote}))

        elif args.command == "download":
            if args.recursive:
                remote.download_directory(args.remote, args.local)
            else:
                remote.download_file(args.remote, args.local)
            print(json.dumps({"status": "ok", "remote": args.remote, "local": args.local}))

        elif args.command == "ls":
            entries = remote.list_dir(args.path)
            print(json.dumps({"status": "ok", "path": args.path or remote.workdir, "entries": entries}))

        elif args.command == "mkdir":
            remote.mkdir(args.path)
            print(json.dumps({"status": "ok", "path": args.path}))

        else:
            return _fail(f"Unknown command: {args.command}")

    except Exception as exc:  # noqa: BLE001 - report all failures as JSON to the caller
        return _fail(f"{type(exc).__name__}: {exc}")
    finally:
        remote.disconnect()

    return 0


if __name__ == "__main__":
    sys.exit(main())
