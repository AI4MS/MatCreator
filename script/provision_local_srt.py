"""First-install public snapshot builder; no downloads, updates or user migration."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import stat
import subprocess
import sys


ROOT = Path("/opt/matcreator-srt-runtime")
EXCLUDED_PARTS = {"__pycache__", ".git", ".venv", "node_modules", ".pytest_cache", ".mypy_cache",
                  "private", "secrets", "credentials", ".local-access", ".local-accounts",
                  "backups", "backup", "logs", ".runtime"}
EXCLUDED_SUFFIXES = {".pyc", ".pyo", ".db", ".sqlite", ".sqlite3", ".log", ".bak", ".old",
                     ".orig", ".tmp", ".pem", ".key", ".p12"}
EGG_FILES = {"PKG-INFO", "SOURCES.txt", "dependency_links.txt", "entry_points.txt", "requires.txt", "top_level.txt"}
SAFE_ENV = {"PATH": "/usr/sbin:/usr/bin:/sbin:/bin", "HOME": "/root", "LANG": "C.UTF-8",
            "PYTHONNOUSERSITE": "1", "PYTHONDONTWRITEBYTECODE": "1"}
WRAPPER = b'#!/bin/sh\nexec /usr/bin/bwrap --unshare-ipc --unshare-uts "$@"\n'


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def sha(path):
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1048576), b""):
            digest.update(block)
    return digest.hexdigest()


def require_admin():
    if sys.platform != "linux" or os.geteuid() != 0:
        raise RuntimeError("Provisioning requires a Linux administrator")


def safe_entry(info):
    if info.st_uid != 0 or (not stat.S_ISLNK(info.st_mode) and info.st_mode & 0o022):
        raise RuntimeError("A runtime path is not administrator-owned and immutable")
    if not (stat.S_ISREG(info.st_mode) or stat.S_ISDIR(info.st_mode) or stat.S_ISLNK(info.st_mode)):
        raise RuntimeError("A runtime path is not a regular file or directory")


def trusted_path(path, *, symlink=False):
    target = path.resolve(strict=True) if symlink else path
    for component in (target, *target.parents):
        info = component.lstat()
        safe_entry(info)
        if stat.S_ISLNK(info.st_mode):
            raise RuntimeError("An immutable path contains a symbolic link")
    if symlink:
        for component in (path, *path.parents):
            safe_entry(component.lstat())


def excluded(relative):
    return (any(part.lower() in EXCLUDED_PARTS or part.lower().startswith(".env")
                or part.lower().endswith((".egg-info", ".dist-info")) for part in relative.parts)
            or relative.suffix.lower() in EXCLUDED_SUFFIXES)


def selected_files(source):
    if any(not (source / name).is_dir() or (source / name).is_symlink() for name in ("src", "web")):
        raise RuntimeError("The source checkout must contain regular src and web directories")
    selected = []
    for path in [*(source / "src").rglob("*"), *(source / "web").glob("*.py"),
                 source / "pyproject.toml", source / "README.md"]:
        relative = path.relative_to(source)
        if excluded(relative):
            continue
        if path.is_symlink():
            raise RuntimeError("Public source cannot contain symbolic links")
        if path.is_dir():
            continue
        if not path.is_file() or any(char in relative.as_posix() for char in "\x00\n\r\\"):
            raise RuntimeError("A required public source file is missing or unsafe")
        selected.append(path)
    return sorted(selected)


def write_new(path, data, mode=0o644):
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, mode)
    with os.fdopen(descriptor, "wb") as stream:
        stream.write(data)
        stream.flush()
        os.fsync(stream.fileno())
    path.chmod(mode)


def prepare(source):
    require_admin()
    if os.path.lexists(ROOT):
        raise RuntimeError("Runtime already exists; this helper never overwrites or migrates it")
    source = source.resolve(strict=True)
    files = selected_files(source)  # Validate before creating the installation.
    trusted_path(ROOT.parent)
    previous_mask = os.umask(0o022)
    try:
        return stage(source, files)
    finally:
        os.umask(previous_mask)


def stage(source, files):
    ROOT.mkdir(mode=0o755)
    public = {}
    for source_file in files:
        relative = Path("app") / source_file.relative_to(source)
        target = ROOT / relative
        target.parent.mkdir(parents=True, exist_ok=True, mode=0o755)
        write_new(target, source_file.read_bytes())
        public[relative.as_posix()] = sha(target)
    (ROOT / "bin").mkdir(mode=0o755)
    write_new(ROOT / "bin/bwrap", WRAPPER, 0o755)
    subprocess.run([sys.executable, "-I", "-m", "venv", str(ROOT / "venv")],
                   check=True, env=SAFE_ENV, cwd="/")  # ensurepip is local; no package download.
    write_new(ROOT / "preparation.json", canonical({"version": 1, "files": public}))
    return {"prepared": True, "runtime_root": str(ROOT), "public_files": len(public), "dependencies_installed": False}


def validate_tree(tree, *, symlinks=False):
    trusted_path(tree)
    def fail(error):
        raise RuntimeError("An immutable runtime tree cannot be enumerated") from error
    for directory, names, files in os.walk(tree, followlinks=False, onerror=fail):
        for name in [*names, *files]:
            path = Path(directory) / name
            info = path.lstat()
            safe_entry(info)
            if stat.S_ISLNK(info.st_mode):
                if not symlinks:
                    raise RuntimeError("Public source cannot contain symbolic links")
                trusted_path(path, symlink=True)


def finalize():
    require_admin()
    trusted_path(ROOT)
    if os.path.lexists(ROOT / "manifest.json"):
        raise RuntimeError("A finalized runtime is immutable; updates are not supported by this helper")
    marker = ROOT / "preparation.json"
    trusted_path(marker)
    prepared = json.loads(marker.read_text(encoding="utf-8"))
    known = prepared.get("files")
    if prepared.get("version") != 1 or not isinstance(known, dict) or not known:
        raise RuntimeError("The initial preparation record is invalid")
    for tree in ("app", "bin", "venv"):
        validate_tree(ROOT / tree, symlinks=tree == "venv")
    public = {}
    for path in (ROOT / "app").rglob("*"):
        if not path.is_file():
            continue
        relative = path.relative_to(ROOT)
        name = relative.as_posix()
        egg = (len(relative.parts) == 4 and relative.parts[:3] == ("app", "src", "matcreator.egg-info")
               and path.name in EGG_FILES)
        if (excluded(relative) and not egg) or (name not in known and not egg):
            raise RuntimeError("An unexpected file exists in the public application tree")
        public[name] = sha(path)
    if any(public.get(name) != expected for name, expected in known.items()):
        raise RuntimeError("Prepared public source was changed or removed before finalization")
    if (ROOT / "bin/bwrap").read_bytes() != WRAPPER:
        raise RuntimeError("The namespace wrapper differs from the initial installation contract")
    python = ROOT / "venv/bin/python"
    trusted_path(python, symlink=python.is_symlink())
    freeze = subprocess.run([str(python), "-I", "-m", "pip", "freeze", "--all"],
                            check=True, capture_output=True, text=True, env=SAFE_ENV, cwd="/").stdout
    write_new(ROOT / "dependencies.freeze.txt", freeze.encode("utf-8"))
    public.update({name: sha(ROOT / name) for name in ("bin/bwrap", "dependencies.freeze.txt")})
    value = {"version": 1, "source_sha256": hashlib.sha256(canonical(public)).hexdigest(), "files": public}
    write_new(ROOT / "manifest.json", canonical(value))
    return {"runtime_root": str(ROOT), "source_hash": value["source_sha256"],
            "runtime_sha256": sha(ROOT / "manifest.json"), "public_files": len(public), "unit_started": False}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("prepare", "finalize"))
    parser.add_argument("--source-root", type=Path, default=Path(__file__).resolve().parents[1])
    arguments = parser.parse_args()
    try:
        value = prepare(arguments.source_root) if arguments.action == "prepare" else finalize()
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as exc:
        parser.exit(1, f"Provisioning refused: {exc}\n")
    print(json.dumps(value, sort_keys=True))


if __name__ == "__main__":
    main()
