"""Select account execution without importing an unused isolation backend."""
from __future__ import annotations

import os
from pathlib import Path

from .local_account_runtime import LocalAccountRuntimeManager, LocalRuntimeConflict


def create_local_runtime_manager(home: Path, source_root: Path):
    backend = os.environ.get("MATCREATOR_LOCAL_RUNTIME", "process").strip().lower()
    if backend == "process":
        return LocalAccountRuntimeManager(home, source_root)
    if backend == "srt":
        from .local_account_srt import SrtAccountRuntimeManager
        return SrtAccountRuntimeManager(home, source_root)
    raise LocalRuntimeConflict("Unknown local account runtime backend; select process or srt explicitly")
