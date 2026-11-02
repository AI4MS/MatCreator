"""Run account-isolation regressions without reading a developer's real home."""
from __future__ import annotations

import os
from pathlib import Path
import sys
import tempfile


SUITES = [
    "test_local_accounts_security.py", "test_local_account_paths.py",
    "test_local_account_runtime.py", "test_local_account_launchers.py",
    "test_local_account_identity.py", "test_local_account_srt.py",
    "test_local_access_security_review.py", "test_local_instance_data_paths.py",
    "test_local_instance_launcher.py", "test_local_instance_web_routing.py",
    "test_local_srt_host.py", "test_local_srt_policy.py", "test_local_srt_worker.py",
    "test_local_srt_provisioning.py",
    "test_local_srt_web_proof.py", "test_ssh_srt_proxy.py",
    "test_web_session_access.py", "test_web_managed_runs.py", "test_web_remote_job_wakeups.py",
    "test_matcreator_constants.py", "test_show_plot_workspace.py",
    "test_ports_config.py", "test_matcreator_cli_ports.py", "test_start_matcreator_ports.py", "test_web_ports.py",
]


def main() -> int:
    root = Path(__file__).resolve().parents[1]
    os.chdir(root)
    sys.path[:0] = [str(root), str(root / "src"), str(root / "tests")]
    with tempfile.TemporaryDirectory(prefix="mc-isolation-check-") as temporary:
        home = Path(temporary)
        Path.home = classmethod(lambda cls: home)
        for key in tuple(os.environ):
            if key.startswith(("MATCREATOR_", "MATCLAW_", "OAW_XRD_")):
                os.environ.pop(key, None)
        os.environ.update(HOME=str(home), USERPROFILE=str(home), MATCREATOR_MODE="local",
                          MATCREATOR_HOME=str(home / ".matcreator"), MATCLAW_WORKSPACE=str(home / "workspace"))
        import pytest
        arguments = ["tests/" + name for name in SUITES]
        # Existing Windows server bind-mount drive parsing is outside this PR.
        if os.name == "nt":
            arguments += ["-k", "not test_server_worker_shared_mounts_parse_extra_binds"]
        return pytest.main([*arguments, *sys.argv[1:]])


if __name__ == "__main__":
    raise SystemExit(main())
