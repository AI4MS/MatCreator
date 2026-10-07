# Local VASP runtime diagnosis — 2026-10-07

Issue #3 remains unaccepted until a real silicon workflow succeeds. This report
corrects the earlier environment report; it does not establish VASP completion.

## Reproduction and causal probes

From the WSL repository, with the project venv and NVM directory on PATH:

```bash
PYTHONPATH=src .venv/bin/python .workspace/silicon-t2/debug-runtime/repro.py
```

Two runs before the fix failed identically: `run_python` used project Python
3.12.13, whereas `run_bash` resolved `~/.local/bin/python` (3.10.12) and raised
`ModuleNotFoundError: No module named 'ase'`. The Bash login profile prepended
`~/.local/bin`, replacing the intended interpreter. Independently, a shadow
`python` on PATH made both Python tools execute the wrong interpreter. Three
regression cases failed before the fix and pass afterward.

Python tools now use `sys.executable`; Bash uses a non-login shell with inherited
configuration and the runtime interpreter directory prepended to PATH. Shell
profile initialization must happen at harness startup, rather than separately
inside each tool. The original reproduction passes after the fix.

The provider PATH probe reproduces `The 'bohr' CLI is not installed or not on
PATH` with `/usr/local/bin:/usr/bin:/bin`. Changing only PATH to include
`~/.nvm/versions/node/v24.14.1/bin` makes the same provider call return 2.6.100.
This was a launcher environment error, not a missing installation. Future local
launches need the NVM directory on PATH; the tools now retain that startup PATH.

## Environment comparison

| Boundary | bohr | Python chosen by tools | Config and authentication |
| --- | --- | --- | --- |
| Interactive WSL | NVM bohr 2.6.100 | bare `python`: user shim 3.10.12; explicit project interpreter: 3.12.13 | bohr project query succeeds without either credential environment variable |
| MatCreator `run_python` | same path/version | project 3.12.13 | MatCreator config loads; project query succeeds |
| MatCreator `run_bash`, before fix | same path/version with corrected launcher | user shim 3.10.12; ASE missing | same HOME/config; project query succeeds |
| MatCreator `run_bash`, after fix | same path/version | project 3.12.13; ASE import succeeds | inherited configuration retained |
| Actual `run_bohr_json` provider | same path/version | parent project runtime | version and project query succeed with provider flags |

MatCreator reads `/home/shik-mechrevo-wsl/.matcreator/config.yaml`. A file-open
trace shows bohr reads `/home/shik-mechrevo-wsl/.bohr-cli/config.yaml`. These are
different configurations. The CLI reports the default configuration while its
named profile list is empty; `profile show default` fails, which does not negate
the successful project authentication. In an isolated temporary HOME, both
`BOHRIUM_ACCESS_KEY` alone and adding `ACCESS_KEY` fail with `AUTH_REQUIRED` in
this installed version. Existing CLI config is the verified working credential
source. Credential values were neither displayed nor copied into artifacts.

## PBE potentials

Minimal independent check in the real project interpreter:

```python
from pymatgen.core import Lattice, Structure
from pymatgen.io.vasp.sets import MPRelaxSet
MPRelaxSet(Structure.from_spacegroup(
    "Fd-3m", Lattice.cubic(5.43), ["Si"], [[0, 0, 0]]
)).potcar
```

It raises `PmgVaspPspDirError: Set PMG_VASP_PSP_DIR=<directory-path> in
.pmgrc.yaml (needed to find POTCARs)`. INCAR generation succeeds. Interactive
and tool environments, MatCreator's `env` configuration, and pymatgen's loaded
settings contain no PSP directory. Neither default pymatgen YAML file exists;
there is no `PMG_CONFIG_FILE` override. Thus this is a genuinely absent
configuration, not a configured value silently ignored by the tool.

The bounded home search (depth four, excluding caches, virtual environments,
Node dependencies and repositories' `.git`) found only
`~/.matcreator/workspace/vasp/relax_001/POTCAR.note`, an instruction file, not a
potential. `/opt` contains containerd/docker-desktop; `/data` is absent and
`/srv` empty. This establishes no usable source in the inspected scope, not
global absence on all host disks or inside remote images. No dummy POTCAR was
substituted.

## VASP image source

MatCreator's `compute.vasp_image` is empty and `BOHRIUM_VASP_IMAGE` unset in all
compared environments. The current account-private catalog nevertheless has
image **24280**, `vasp:6.3.0`, at
`registry.dp.tech/dptech/prod-28882/vasp:6.3.0`; `bohr image get 24280` succeeds.
The earlier diagnostic did not query this catalog and overstated the blocker.
The resource exists; its selection is not configured. No tag was invented.

`bohr image search vasp` returns gvasp and VASPKIT, which do not prove presence
of the intended executable. `bohr image list -t VASP` is an invalid category in
2.6.100 and even exits zero with non-JSON diagnostic text. The private catalog
query is the correct discovery command. MatCreator independently queried the
image metadata and `bohr image dockerfile 24280`; no Dockerfile is stored
(`RESOURCE_NOT_FOUND`). Neither executable/MPI paths nor bundled potentials
can be established from that metadata. No extra paid probe job was created.

The image-detail response reports zero CNY balance and `sufficient:false`.
This alone does not establish whether the selected project's photon payment
can fund the workflow; payment is not yet tested by a real submission.

## Retained execution and remaining information

The existing MatCreator session **silicon-t2-20261007-env2** was resumed with the
fixed tools. MatCreator owns the calculation plan and independently reproduced
the potential error, inspected the existing image, and recorded its decision
in `.workspace/silicon-t2/matcreator-runtime-diagnosis.json`. It stopped before
input preparation/submission. Development A remains active: query failures
must pause; the retained earlier real query succeeded with zero candidates.

Retained evidence under `.workspace/silicon-t2/`:

- `events-diagnosis.jsonl`, existing `events-env2.jsonl` and `trajectories/`;
- the existing `.aidb/runs/material-Si/1791354576-ebf79559/` report and audit;
- `debug-runtime/` clearly marked diagnostic harnesses, redacted environment
  comparisons, PATH/config-source probes, and test log;
- the MatCreator report's historical legacy job ID **22947186**, group
  **16319214**. This is not a new tracked T2 job or evidence of success.

No new job was submitted; there is no new durable job ID or batchjob ID to
report. Any eventual submission must save both IDs and query existing status
before retrying. Issue #3 has not been closed or marked successful.

The user-facing missing-information request is consolidated to a real PBE Si
potential source (local directory, documented in-image path, or accessible host
entry point), plus special VASP/MPI startup instructions if this image needs
them. Existing project and calculation authorization remains valid; no repeat
approval is required. Program availability and payment must be verified by the
eventual authorized workflow rather than assumed from the catalog entry.

Validation after the fix: **231 passed** across workspace tools, silicon
workflow, remote-job tools and Bohrium Batch Job adapter tests. The original
Python environment reproduction passes. The independent POTCAR reproduction
still fails due to the unresolved resource configuration.

Follow-up Standards and Spec reviews found zero new issues. They preserve the
same acceptance limitation: the real silicon calculation has not succeeded.
