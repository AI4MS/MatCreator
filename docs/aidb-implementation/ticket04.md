# Ticket 04 — save existing MD frames and mixed results

The normal chat, Flash and step executor use the same AIDB archive operation for an explicit save of existing relaxation, static SCF or MD output. The response retains each directory's archive receipt and gives counts for verified new records, existing records, failed items and unverified archive states. Results that passed readback expose the common Details and Export actions.

For MD, the planned integration count must agree with the number of XML calculations and OUTCAR force frames. The XML must close, OUTCAR must contain the final accounting footer, and the saved electronic step must pass convergence. The label-bearing structure is taken from the last XML calculation. The retained Si fixture's separate XML finalpos has different coordinates after integration. Energy comes from the last electronic step's e_0_energy. OUTCAR independently corroborates the stored frame's atom order, lattice, positions, energy, forces and available stress. One frame is archived. The XML initialpos and optional POSCAR evidence preserve the starting-structure role. Sampling retains actual runtime parameters, configured temperature endpoints, step length and count, and the instantaneous OUTCAR temperature when present. Values that cannot be established have an explicit reason.

## Normal-chat acceptance — 2026-11-13

The managed MatCreator chat ran with the installed AIDB skill and a ticket-specific ASE/SQLite database. The single run started before the Ticket 02 merge. The mixed and retry runs used the merged code at 854ac25.

| Managed run | Result |
| --- | --- |
| ba2406914cea4c7c8b5ce227960d5e6b | The original Si MD result was saved and reread as 9651013fd22dc86154260cf73314bc95. The answer reported 300 K NVT, 289.76 K observed, 1 fs, 3 steps and −10.85400315 eV/cell. The initial lookup failed while the skill directory was a symlink. A private skill copy and normal skill refresh recovered the run. |
| e7ae6c551d9644babc13720a784a5282 | One tool call processed five directories. Three were newly saved, one was already present and one failed. |
| fabcc093e6204e4b917ee489c9568eb8 | The three original directories all returned existing with the same IDs. The isolated database still held four rows, including the temperature fault replay. |

| Mixed batch directory | Evidence | Outcome |
| --- | --- | --- |
| si-relax-results | Original Bohrium Si PBE relaxation | New, 97bb8f08332f083a0ba0de2c7aa26693 |
| si-md-short | Replay of Si MD, with the planned XML and OUTCAR counts increased from 3 to 4 while the closed output still contains 3 completed steps | Rejected before write with invalid_output and an explicit 4-versus-3 explanation |
| nacl-static-results | Original compressed NaCl static SCF outputs | New, 19ee4361ffc4e0266fc915a459c4d5f4 |
| si-md-results | Original compressed Atomate Si MD fixture, byte-identical to the single save | Existing, 9651013fd22dc86154260cf73314bc95 |
| si-md-missing-temperature | Replay of Si MD with the last instantaneous temperature marker removed from OUTCAR | New, 10c1bea1f798306f88e2fa92272086e2. The archive was verified and the actual temperature was unavailable, with an explicit reason in the assistant reply and the record. |

The runs replayed historical outputs from the tracked fixtures. Their tool events include no VASP submission or calculation. The early-stop and temperature variants are parser-boundary fault injections and represent no independent VASP execution.

## Browser and file evidence

Chromium opened the ordinary MatCreator session with the mixed batch. Clicking Details on the authentic MD row made a fresh record-ID request and showed both structure roles, 300 K, 289.76 K and three planned and observed steps. The missing-temperature row exposed the reason in its actual-temperature field. Clicking Export on the authentic row automatically downloaded EXTXYZ, and the companion links downloaded POSCAR and HTML. The detail, export and all three download requests returned HTTP 200. ASE reopened both structure formats and confirmed the same ordered pair of Si atoms, cell and coordinates. The EXTXYZ readback yielded −10.85400315 eV, the expected first force vector and an eV virial. The HTML contains the initial structure, sampling conditions, energy definition and output evidence.

Artifacts remain under .workspace/aidb-ticket04 in the ticket worktree. The logs directory contains single-success.jsonl, batch-success.jsonl, batch-retry.jsonl, md-original-detail.png, md-unknown-detail.png, md-original-export.png, HTTP request JSON and the suite/build logs. The downloads directory contains the three browser files. Per-directory archive receipts and independently read-back records remain below workspace/.aidb-sessions. The browser.cjs script can reopen the saved batch in the isolated runtime on port 15473.

## Automated verification

- PYTHONPATH=src:. ../integration/.venv/bin/python -B -m pytest -q tests/aidb tests/test_web_aidb_api.py tests/test_adk_compat.py passed 63 tests and 3 subtests. The added cases cover the actual paired MD output, mixed partial success, per-source deduplication, incomplete integration despite complete file endings, final SCF failure and an unavailable observed temperature carried through the public detail tool.
- npm test in web/vite-frontend passed 224 tests, including the MD detail view's configured temperature program, step count and evidence reason. npm run build succeeded with the existing large-chunk warning.
