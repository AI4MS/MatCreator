# Ticket 12 — Managed VASP AIMD

The VASP workflow routes prepared AIMD inputs through the shared Batch Job and Sandbox boundaries. Input preparation supports a fresh fixed-cell constant-temperature NVT run and records explicit temperature, time step, step count and random seed. Matching uses the exact staged input revision and verified initial seed and effective MD parameters. A requested curve requires a complete temperature series from the same run. The receiver verifies the integration count and final frame's electronic labels, then saves one labeled frame. The temperature plot and table use observed values from OUTCAR.

## Acceptance evidence — 2026-11-13

The managed Web and ADK routes handled a natural-language 300 K NVT request with a 1 fs step and three steps. The isolated provider replayed the original Si XML/OUTCAR and used a synthetic POTCAR header. The configured planner was scripted, and the installed AIDB used a private SQLite database. One local job handoff and collection archived and read back record `d1902f6daaa8135bde86ae63afd6a4c3`, frame 2, at −10.85400315 eV/cell.

Chromium opened, collapsed and reopened Details on that completion in the saved normal chat. Two EXTXYZ downloads passed ASE readback. The original XML and OUTCAR downloads were byte-identical, and the downloaded curve contained 300.00, 297.38 and 289.76 K. Reload resolved the same job and record. Private evidence is in `.workspace/aidb-normal-chat-replay/aimd-h6gpai18/`.

Automated regressions exercise a changed initial seed, missing temperature data, incomplete integration, truncated XML and a database write fault. The H₂O input test covers 300 K NVT, 1 fs and 20 steps. The available historical water pair is a three-step NVE run. The 20-step H₂O case was subsequently verified in the run below.

## Authorized real H₂O calculation — 2026-11-13

The first authorized job, `250942405b674346bdb9451296e7f06c`, stopped while loading Intel oneAPI. The acceptance launcher inherited `set -u` and the image referenced an unset variable. It produced no completed VASP outputs and wrote no record. With separate authorization, the second launcher disabled nounset during Intel initialization and restored it before the calculation. It used the same 10 Å water cell, four input files, VASP 6.3.0, Bohrium project and 16-core machine. The normal chat used a local scripted planner.

The second job, Bohrium `9c173718f56d4c58af21e57b94e70f05` and MatCreator `7291000b9a474d32b7d70187e8adcfea`, completed 20 steps of 300 K NVT with a 1 fs step. The original paired outputs exposed two verification issues. VASP XML reported a small stress asymmetry within OUTCAR's printed precision, and XML rounded the staged EDIFF to an equivalent decimal. Fixes `5ce3784` and `433a591` handle those cases and preserve independent output verification.

The original output was recollected in a loopback-only managed chat without submitting or transferring the provider job again. Run `8ef0ce824269436e8d72c92fc60ef6cc` archived and read back `178927878a403e4db69ce3696b414a9d`, frame 19, at −14.22876871 eV/cell. The observed temperature curve contains 20 points. Chromium opened, collapsed and reopened Details, downloaded EXTXYZ twice and checked the two downloads with ASE. The curve and raw XML/OUTCAR downloads were byte-identical to the workspace files. Reload recovered the same completion. The isolated archive held one row.

Private evidence is in `.workspace/aidb-normal-chat-replay/h2o-corrected-live/`. The first job's failure log is in `h2o-authorized-live/failure-output/`.
