#!/usr/bin/env python3
"""Build a MatterGen-style CSV with inline CIF text from numbered CIF files.

Input expectations:
- a directory containing CIF files such as ``0.cif`` and ``11.cif``
- a CSV file with at least three columns:
  1. structure identifier
  2. placeholder column, often ``0``
  3. target property value

Output format:
- ``material_id,<property_name>,cif``
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
from pathlib import Path


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Replace a placeholder CSV column with CIF contents."
    )
    parser.add_argument(
        "data_dir",
        help="Directory containing numbered CIF files such as 0.cif and 11.cif.",
    )
    parser.add_argument(
        "input_csv",
        help="Input CSV path with id, placeholder, property columns.",
    )
    parser.add_argument(
        "property_name",
        help="Header name for the property column, such as dft_band_gap.",
    )
    parser.add_argument(
        "--output-csv",
        default=None,
        help="Optional output CSV path. Default: <data_dir>/mattergen_property.csv.",
    )
    return parser


def _read_cif_text(data_dir: Path, material_id: str) -> str:
    cif_path = data_dir / f"{material_id}.cif"
    if not cif_path.exists():
        raise FileNotFoundError(f"CIF file not found for id '{material_id}': {cif_path}")
    return cif_path.read_text(encoding="utf-8")


def main() -> int:
    parser = _build_parser()
    args = parser.parse_args()

    data_dir = Path(args.data_dir).expanduser().resolve()
    input_csv = Path(args.input_csv).expanduser().resolve()
    if not data_dir.is_dir():
        raise NotADirectoryError(f"data_dir does not exist: {data_dir}")
    if not input_csv.exists():
        raise FileNotFoundError(f"input_csv does not exist: {input_csv}")
    if args.output_csv:
        output_csv = Path(args.output_csv).expanduser().resolve()
    else:
        output_csv = data_dir / "mattergen_property.csv"

    output_csv.parent.mkdir(parents=True, exist_ok=True)

    rows_written = 0
    with input_csv.open("r", encoding="utf-8", newline="") as f_in, output_csv.open(
        "w", encoding="utf-8", newline=""
    ) as f_out:
        reader = csv.reader(f_in)
        writer = csv.writer(f_out, quoting=csv.QUOTE_MINIMAL)
        writer.writerow(["material_id", args.property_name, "cif"])

        for line_no, row in enumerate(reader, start=1):
            if not row or all(not cell.strip() for cell in row):
                continue

            if len(row) < 3:
                raise ValueError(
                    f"Row {line_no} has only {len(row)} columns, but at least 3 are required."
                )

            material_id = row[0].strip()
            property_value = row[2].strip()
            cif_text = _read_cif_text(data_dir, material_id)
            writer.writerow([material_id, property_value, cif_text])
            rows_written += 1

    print(
        json.dumps(
            {
                "status": "success",
                "data_dir": str(data_dir),
                "input_csv": str(input_csv),
                "output_csv": str(output_csv),
                "property_name": args.property_name,
                "rows_written": rows_written,
            },
            ensure_ascii=True,
        )
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
