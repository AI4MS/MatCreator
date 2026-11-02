"""The default local entry point starts an account broker, not a shared ADK."""
from __future__ import annotations

from pathlib import Path
import shutil
import subprocess

import pytest


ROOT = Path(__file__).resolve().parents[1]


def test_powershell_launcher_has_explicit_internal_worker_mode_and_no_token_prompt():
    source = (ROOT / "script" / "start_local_review.ps1").read_text(encoding="utf-8")
    assert "[switch]$TokenWorker" in source
    assert "if ($reviewTokenWorker -and -not $WebOnly) { $adk =" in source
    assert "mode=if($reviewTokenWorker){'token-worker'}else{'local-accounts'}" in source
    assert "Register or sign in to a local account" in source
    assert "paste the token to unlock" not in source
    assert "Write-Output \"Access token file:" not in source


def test_shell_entrypoint_does_not_require_root_adk_without_worker_marker():
    source = (ROOT / "script" / "start_matcreator.sh").read_text(encoding="utf-8")
    assert 'MATCREATOR_LOCAL_PROFILE_WORKER="${MATCREATOR_LOCAL_PROFILE_WORKER:-0}"' in source
    assert "else ('WEB', 'FRONTEND')" in source
    assert 'if [[ "$MATCREATOR_LOCAL_PROFILE_WORKER" == "1" ]]; then\n    echo "Starting internal ADK' in source
    assert 'WAIT_PIDS=("$PID_WEB" "$PID_VITE")' in source
    assert 'wait -n "${WAIT_PIDS[@]}"' in source
    assert "enter the instance token" not in source


def test_powershell_script_parses_without_spawning_services():
    shell = shutil.which("pwsh") or shutil.which("powershell")
    if shell is None:
        pytest.skip("PowerShell parser not installed")
    script = ROOT / "script" / "start_local_review.ps1"
    command = (
        "$parseTokens=$null; $parseErrors=$null; "
        "[Management.Automation.Language.Parser]::ParseFile($args[0],[ref]$parseTokens,[ref]$parseErrors) | Out-Null; "
        "if($parseErrors){$parseErrors | ForEach-Object {$_.Message}; exit 1}"
    )
    # A single literal argument is passed directly; this invokes only parsing.
    escaped = str(script).replace("'", "''")
    command = command.replace("$args[0]", "'" + escaped + "'")
    result = subprocess.run([shell, "-NoProfile", "-NonInteractive", "-Command", command],
                            capture_output=True, text=True, timeout=15)
    assert result.returncode == 0, result.stdout + result.stderr


def test_powershell_launcher_uses_portable_profile_and_executable_defaults():
    source = (ROOT / "script" / "start_local_review.ps1").read_text(encoding="utf-8")
    assert "C:\\Users\\" not in source
    assert "Join-Path $reviewRoot '.local-runtime/home'" in source
    assert "Join-Path $RuntimeHome 'workspace'" in source
    assert "Join-Path $reviewRoot '.venv/Scripts/python.exe'" in source
    assert "Get-Command -Name $Command -CommandType Application" in source
    assert "sys.version_info >= (3, 12)" in source
    assert "$env:OAW_XRD_ROOT =" not in source
    assert "$env:MATCREATOR_XRD_PYTHON =" not in source
    assert "[ValidateSet('process','srt')]" in source
    assert "$ContainerCommand" not in source
    assert "$ContainerImage" not in source
    assert "MATCREATOR_LOCAL_CONTAINER_" not in source
    # Preflight must use the selected checkout, even from another working dir.
    assert "Path(sys.argv[1])).preflight()' $reviewRoot" in source


def test_powershell_executable_resolution_preserves_explicit_and_local_venv_choices(tmp_path):
    shell = shutil.which("pwsh") or shutil.which("powershell")
    if shell is None:
        pytest.skip("PowerShell parser not installed")
    script = ROOT / "script" / "start_local_review.ps1"
    explicit = tmp_path / "explicit interpreter.exe"
    candidate = tmp_path / "local interpreter.exe"
    explicit.touch()
    candidate.touch()

    def literal(value):
        return "'" + str(value).replace("'", "''") + "'"

    # Define just the parsed discovery function. Do not evaluate launcher code,
    # start an executable, reserve ports, initialize identities or start MC.
    command = (
        "$parseTokens=$null; $parseErrors=$null; "
        f"$tree=[Management.Automation.Language.Parser]::ParseFile({literal(script)},[ref]$parseTokens,[ref]$parseErrors); "
        "$definition=$tree.Find({param($node) $node -is [Management.Automation.Language.FunctionDefinitionAst] "
        "-and $node.Name -eq 'Resolve-ReviewExecutable'},$true); "
        ". ([scriptblock]::Create($definition.Extent.Text)); "
        "function Get-Command {param($Name,$CommandType,$ErrorAction) "
        "if($Name -eq 'missing-tool'){return $null}; [pscustomobject]@{Source='resolved-on-path'} }; "
        f"$explicit=Resolve-ReviewExecutable {literal(explicit)} @({literal(candidate)}) 'python' 'Python'; "
        f"$venv=Resolve-ReviewExecutable '' @({literal(candidate)}) 'python' 'Python'; "
        "$fallback=Resolve-ReviewExecutable '' @() 'node' 'Node'; "
        "$missing=$false; try {Resolve-ReviewExecutable 'missing-tool' @() 'python' 'Python' | Out-Null} catch {$missing=$true}; "
        "@{explicit=$explicit;venv=$venv;fallback=$fallback;missing=$missing} | ConvertTo-Json -Compress"
    )
    result = subprocess.run([shell, "-NoProfile", "-NonInteractive", "-Command", command],
                            capture_output=True, text=True, timeout=15)
    assert result.returncode == 0, result.stdout + result.stderr
    import json
    values = json.loads(result.stdout)
    assert Path(values["explicit"]) == explicit.resolve()
    assert Path(values["venv"]) == candidate.resolve()
    assert values["fallback"] == "resolved-on-path"
    assert values["missing"] is True
