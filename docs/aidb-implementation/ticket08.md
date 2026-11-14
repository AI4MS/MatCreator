# Ticket 08 — Managed static SCF and verified reuse

Static self-consistent inputs and results go through the same submission guard, launcher and receiver as relaxation. The staged revision includes the bytes of any restart inputs. The receiver checks the actual initial structure and settings, electronic convergence and same-frame labels. Static SCF has no ionic convergence requirement.

Requested CHGCAR and WAVECAR files enter reuse only when the tracked launcher records the output and private collected bytes pass the corresponding format and structure or header checks. Reuse rereads the record and validates the retained files before publishing the downloads and a workspace path for downstream steps. Missing or changed files invalidate the candidate or its selection. The operation leaves VASP and AIDB write counts unchanged.

## Normal-chat acceptance — 2026-11-13

The production managed Web and ADK entry points ran with a scripted planner, a replay provider and the installed AIDB skill in a private SQLite database. The original paired NaCl VASP XML and OUTCAR are from the tracked atomate2 fixture. The associated CHGCAR is a generated 2 × 2 × 2 test grid. It verifies file format, receipt, binding and delivery. A genuine charge-density grid and a genuine WAVECAR still lack replay evidence.

| Case | Managed run | Evidence |
| --- | --- | --- |
| Empty library | `2466d6276e384430afb5c8c85b222691` | One local provider submission and collection, then archive and independent readback. The static NaCl result was −27.96859470 eV/cell, frame 0. |
| Complete candidate and explicit reuse | `930d5e34ab6a4be199be0f4bac22dcee`, `15d52966773248998f620002c4ca6bc9` | Paused on the archived record, then rechecked it and delivered EXTXYZ, POSCAR and a byte-identical test CHGCAR in the workspace. No additional provider call or record. |
| Missing CHGCAR | `94743ac4bed34d0097a177eea0b352b6` | Result incomplete with a CHGCAR-specific reason. XML and OUTCAR remained available. Zero records. |
| Electronic convergence fault | `3259e100e25b4caa88c135c864ae0ef7` | A modified OUTCAR without the final convergence marker yielded an unverified computation and zero records. |
| Archive write fault | `371dd928d6eb42d4b7e79a18c8bb3ea4` | An isolated SQLite trigger blocked the write. The computation still reported success, with raw artifacts and a separate archive failure. Zero records. |

Chromium opened, collapsed and reopened the verified record in the actual chat. It fetched details twice and downloaded EXTXYZ twice. ASE read both downloads with the bound record ID, eight atoms, energy and forces. XML and OUTCAR downloads matched the original bytes and reload resolved the same completion. Browser operations left the one-row archive and provider counts stable.

Private evidence is under `.workspace/aidb-normal-chat-replay/static-dbam34uc/`, with fault receipts in `static-d_lemp72/`, `static-5cunmypj/` and `static-g6_5l4hv/` alongside it. The automatic tests also remove the retained CHGCAR after finding a candidate and confirm that selection rejects it.
