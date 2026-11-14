# Ticket 03 — save existing relaxation and static results

`aidb_archive_vasp(directories=[...])` is available to normal chat, Flash and the step executor. It processes each explicitly requested workspace directory independently. Success returns a shared AIDB result row plus a persisted per-directory receipt. The operation never submits VASP. `matcreator.aidb.archive.archive_vasp_result(session, directory)` is the shared entry point for later workflow integration.

## Evidence and failure behavior

- XML and OUTCAR must have the same VASP version and start time. The XML must parse through its closing tag and OUTCAR must have complete final accounting. The final atomic order, lattice, periodic coordinates, energy, forces and available stress are cross-checked. Static SCF requires electronic convergence. Relaxation also requires the final VASP ionic convergence marker. Optional input evidence reports what the XML actually corroborates.
- Decoded XML and OUTCAR SHA-256 together with the zero-based frame index identify the stored result. Compressed copies converge on the same record ID. Earlier records with matching hashes and an explicit frame can be reused under their original IDs. An older row with incomplete same-source evidence is surfaced for inspection. The installed adapter lock protects lookup and write across sessions.
- Writing uses the installed AIDB normalizer and local workspace API. The regular session detail operation independently reads the record by ID and checks fields and provenance. A failed write, unavailable readback and mismatched readback have distinct statuses. The session's archive directory retains verified source evidence, the intended record, a write-attempt receipt and any returned record. A later call checks storage before writing again.
- Original XML/OUTCAR stay in the user's workspace. The retained proof records decoded file hashes as well as compressed container hashes. Historical replay, injected faults and real independent source files are identified below.

## Verification

`PYTHONPATH=src:. <integration>/.venv/bin/python -B -m pytest -q tests/aidb tests/test_web_aidb_api.py` passed 44 tests and 3 subtests at `a562ff4`. The archive tests use isolated real ASE/SQLite storage and installed AIDB query. Real sources include the retained Si PBE relaxation (`−43.38227814 eV/cell`), NaCl static SCF (`−27.96859470 eV/cell`) and a separate Si r2SCAN relaxation. Tests include historical prototype-ID reuse, gzip repack, mismatched pairing, truncated output, missing electronic and ionic completion markers, a real SQLite write-trigger failure, and injected failures or mismatches at the installed readback boundary. A separate replay preserves all Si calculation values and changes only the paired start timestamp to exercise the same-settings identity case. That replay does not represent a newly executed VASP job.

## Normal-chat acceptance — 2026-11-13

Integration code `c482c979` ran the ordinary MatCreator managed chat with the installed AIDB skill and a new isolated ASE/SQLite database. The prompts named workspace result directories. Both tool actions and actual output were captured.

| Run | Result |
| --- | --- |
| `a3cddd6f483f41619a9348ed5cb359b7` | Original Bohrium Si PBE relaxation saved and read back as `97bb8f08332f083a0ba0de2c7aa26693`. |
| `4be8755ad72b47d39264327c18877617` | Byte-identical result in another directory returned `existing`; the same record ID was read back. |
| `9a6b09abcba0440383ad4e9da3561c79` | Real NaCl static output saved and read back as `19ee4361ffc4e0266fc915a459c4d5f4`. |
| `4acc02c94b8f4c2590c8da5a2b3182c9` | Independently run Si r2SCAN relaxation saved and read back as `e62603e55f9ebee8d96d38d9e7245e9e`. |
| `ticket03-invalid-save.jsonl` | XML/OUTCAR mismatch reported `source_conflict`; truncated XML reported `invalid_output`. Neither has a record ID. |

No submit tool ran. The final sidecar contains three entries. Managed run transcripts are in `.workspace/aidb-acceptance/logs/ticket03-*.jsonl`; the source copies, per-directory receipts and independently read-back JSON are under `.workspace/aidb-acceptance/workspace/`. The final branch passed 42 AIDB tests and 3 subtests in integration, with 45 tests and 3 subtests in the implementer worktree when including `tests/test_web_aidb_api.py`. Nonconvergence and database faults are verified at the parser and database boundary by the automated cases above. 04 extends the same save path for MD. 06 connects automatic archive and accepts its result-row operations.
