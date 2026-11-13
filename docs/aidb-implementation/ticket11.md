# Ticket 11 — VASP EOS volume points

The EOS tools start from a verified relaxed structure and generate ordered fixed-volume static inputs. Each point uses the shared VASP query, choice and completion mechanisms. Each E–V row includes the actual volume and energy, record identity, input revision and original run identity. Partial sweeps retain usable results and identify missing points.

## Managed sweep replay — 2026-11-13

The normal ADK chat used a local scripted planner and original Si relaxation output. The static parser boundary supplied controlled energies and structures for the generated 11-point ±5% volume sweep. Point 6 reused its prior record, point 2 failed at the provider and point 9 had incomplete output. The independent points continued. The chat delivered nine verified rows, an E–V CSV and PNG, and a per-member manifest. Repeat collection kept the same jobs and retried only the incomplete transfer. The result-card tests exercised detail and direct EXTXYZ and POSCAR delivery for a selected volume point.

The fcc Al preparation test starts from a controlled relaxation at 64 Å³. It checks all eleven generated POSCARs against volumes from 60.8 to 67.2 Å³.

## Historical Si acceptance — 2026-11-13

The public atomate2 Si₂ EOS fixture supplied one relaxation and three static pairs of original VASP XML and OUTCAR. Three natural-language turns passed through the normal ADK orchestrator with a local scripted planner and installed AIDB in isolated SQLite. The archive wrote and read back four records, the query returned the three static records, and the plot tool returned the E–V PNG in the conversation. The CSV and manifest record volumes, energies, archive receipts and original output hashes. [Original atomate2 fixture at 38294cd](https://github.com/materialsproject/atomate2/tree/38294cd74797fdab9a122260f2ce2abbcbb3fc31/tests/test_data/vasp/Si_EOS_MP_GGA)

| Static point | Volume (Å³/cell) | Energy (eV/cell) | k-point mesh |
| --- | ---: | ---: | --- |
| Low | 35.05725 | −10.54776448 | 10×10×10 |
| Equilibrium | 40.88905 | −10.84935654 | 10×10×10 |
| High | 47.33419 | −10.63207954 | 9×9×9 |

All three static points use PBE and 680 eV. The end points span ±5% linear strain, which corresponds to −14.2625% and +15.7625% volume relative to equilibrium. This is a three-point curve with no equation-of-state fit. The public outputs do not carry MatCreator submission receipts, so this run verifies explicit archival and readback. The managed replay above verifies the requested 11-point volume grid and passive matching behavior.

The [offline verifier](verify-ticket11-historical.py) takes a fixture directory containing `relax`, `low`, `center` and `high`, each with `vasprun.xml.gz` and `OUTCAR.gz`, and a new evidence directory. The integration worktree retains the inputs under `.workspace/aidb-eos-fixtures/` and the transcript, manifest, CSV and PNG under `.workspace/aidb-eos-historical-evidence/`. No new job was submitted for EOS.
