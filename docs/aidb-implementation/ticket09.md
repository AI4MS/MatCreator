# Ticket 09 — SCF and NSCF band workflow

The tracked SCF and NSCF submissions use the common VASP guard and receiver. NSCF verifies the upstream SCF record, the staged restart-file digest and the actual k path. The receiver checks the completed line-mode eigenvalues and Fermi energy and generates bands.json and bands.svg. Reuse verifies the retained band files and restarts against their original receipt. The result page delivers the band data and plot through the session-bound download route.

## Acceptance — 2026-11-13

Offline acceptance used real public Si NSCF XML and OUTCAR from [atomate2 at the pinned revision](https://github.com/materialsproject/atomate2/tree/38294cd74797fdab9a122260f2ce2abbcbb3fc31/tests/test_data/vasp/Si_band_structure). The upstream transport replay uses a fixture-only change to classify the atomate2 one-step SCF output as static and a synthetic CHGCAR. It establishes dependency checking and file delivery across the two stages. Genuine GaAs output and a genuine charge grid were outside this replay.

The test ran four combinations of SCF and NSCF computation and reuse against the installed isolated AIDB. The normal ADK chat exercised both-stage selection and recovered the existing band job for the result card. Missing or incorrect upstream files and removed band artifacts failed the corresponding checks. The original NSCF data yielded 164 k points, 16 bands and a 5.7018 eV Fermi energy.

Chromium reopened the saved chat, fetched details twice, downloaded EXTXYZ twice and clicked the JSON and SVG band links. All downloads resolved the same archived completion after reload. The replay copy maps the test owner to the local browser identity. Private evidence is in `.workspace/aidb-band-browser-evidence/`. The integration passed 12 isolated backend tests, 25 frontend timeline tests and a production build.
