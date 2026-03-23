---
name: database
description: Skills for materials dataset operations using CLI tools. Query and manage ASE databases and a normalized SQLite information database (nodes/datasets/dataset_elements schema).
tools: [database_sql_agent,run_bash,run_python_file]
tags: [general, sequential, simple]
---

# Database CLI Tools

All tools are in `db_tools.py` (same directory as this file).  
Run as: `python /path/to/db_tools.py <command> [options]`  
Every command prints a **JSON object** to stdout and exits 0 on success, 1 on error.

Set `INFO_DB_PATH` (env var) to the SQLite info database path, or pass `--info-db` explicitly.
---

## Commands

### `validate-sql` — Validate a SELECT statement (no DB access)

```bash
python db_tools.py validate-sql --sql "SELECT * FROM nodes"
```

Returns `{"valid": true, "sql": "<normalized>", "error": null}` or `{"valid": false, ...}`.  
**Always run this before `query-info`.**

---

### `query-info` — Query the SQLite information database

```bash
python db_tools.py query-info \
  --info-db /path/to/info.db \
  --sql "SELECT d.dataset_id, d.elements, d.path FROM datasets d JOIN nodes n ON d.node_id=n.node_id WHERE n.name='PBE'"
```

Returns `{"query": "...", "count": N, "datasets": [...]}`.  
The `path` field is automatically resolved to an absolute path.

**Schema reference:**
- `nodes(node_id, name, description, functional, code, cutoff_eV, pseudopot, kspacing, spin_pol, vdw, extra_params, created_at)`
- `datasets(dataset_id, node_id, elements, n_elements, system_type, field, entries, source, path, has_forces, has_stress, has_energy, energy_min, energy_max, created_at)`
- `dataset_elements(dataset_id, element)`

---

### `read-structure` — Extract compositions from structure file(s)

```bash
python db_tools.py read-structure \
  --structures /path/to/structure.cif /path/to/other.extxyz \
  --output query_atoms.extxyz
```

Supported formats: `.cif`, `.xyz`, `.extxyz`, `POSCAR`, and any format ASE can read.  
Returns `{"formulas": [...], "formulas_full": [...], "query_atoms_path": "...", "total_frames": N}`.  
Use the returned `formulas` to build a `query-compounds` selector.

---

### `query-compounds` — Query an ASE database

```bash
# By element presence
python db_tools.py query-compounds --db-path /path/to/dataset.db --selection "Si,O"

# By formula
python db_tools.py query-compounds --db-path /path/to/dataset.db --selection "formula=Si32"

# With energy filter and limit
python db_tools.py query-compounds --db-path /path/to/dataset.db \
  --selection "Si,energy<-1.0" --limit 100

# JSON list of conditions
python db_tools.py query-compounds --db-path /path/to/dataset.db \
  --selection '[["energy","<",-1.0],["pbc","=",true]]'

# Extra ASE select kwargs
python db_tools.py query-compounds --db-path /path/to/dataset.db \
  --selection "Si" --custom-args '{"sort": "-energy"}'
```

Returns `{"query": "...", "count": N, "unique_formulas": [...], "sample_ids": [first 10 ids]}`.

---

### `export-entries` — Export entries from an ASE database

```bash
# By explicit ids
python db_tools.py export-entries --db-path /path/to/dataset.db \
  --mode ids --ids 1 2 3 --fmt extxyz

# Entire database
python db_tools.py export-entries --db-path /path/to/dataset.db \
  --mode all --fmt extxyz

# Random sample
python db_tools.py export-entries --db-path /path/to/dataset.db \
  --mode random --sample-size 500 --random-seed 42 --fmt extxyz

# By selection criteria
python db_tools.py export-entries --db-path /path/to/dataset.db \
  --mode selection --selection "Si,O,energy<-2.0" --limit 1000 --fmt extxyz

# Custom output directory
python db_tools.py export-entries --db-path /path/to/dataset.db \
  --mode all --output-dir /tmp/my_export
```

Returns `{"output_file": "...", "metadata_file": "...", "counts": {"total_exported": N, "unique_formulas": M}}`.  
For ML force-field training, always use `--fmt extxyz`.

---

### `save-extxyz` — Register a user extxyz file in the database

```bash
python db_tools.py save-extxyz \
  --extxyz-path /path/to/data.extxyz \
  --info-db /path/to/info.db
```

Creates a new ASE `.db` file under `<info-db-dir>/user_data/` and registers it in the info database under the "User Upload" node.  
Returns `{"status": "ok", "ase_db_path": "...", "dataset_id": N, "entries": N, "elements": [...]}`.

---

## Typical workflow for dataset search

Use this flow for dataset search:

1. **Explore available nodes/datasets** — first compose and validate a SELECT, then run it:
   ```bash
   python db_tools.py validate-sql --sql "SELECT node_id, name, functional FROM nodes"
   ```
   ```bash
   python db_tools.py validate-sql --sql "SELECT node_id, name, functional FROM nodes"
   python db_tools.py query-info --info-db $INFO_DB_PATH \
     --sql "SELECT node_id, name, functional FROM nodes"
   ```

2. **Find datasets matching target elements** — query `dataset_elements` to locate domain datasets (e.g. `domain_SemiCond`):

   ```bash
   python db_tools.py query-info --info-db $INFO_DB_PATH \
     --sql "SELECT d.dataset_id, d.elements, d.entries, d.path FROM datasets d \
            JOIN dataset_elements de ON d.dataset_id=de.dataset_id \
            WHERE de.element='Si'"
   ```

3. **Query structures within a dataset** — use `query-compounds` with the `path` returned in step 2:

   ```bash
   python db_tools.py query-compounds \
     --db-path <path from step 2> --selection "Si,O" --limit 50
   ```

4. **Export for training** — always use `--fmt extxyz` when preparing datasets for ML force fields:

   ```bash
   python db_tools.py export-entries \
     --db-path <path> --mode selection --selection "Si,O" --fmt extxyz
   ```

