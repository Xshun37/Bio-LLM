#!/usr/bin/env python3
"""Convert GS review TSV exports into annotation-server style CSV.

The output schema matches the review-server annotation export shape.
The conversion is offline and reads local maps under review-server/data.
"""

from __future__ import annotations

import argparse
import csv
import json
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable


EXPORT_HEADER = [
    "id",
    "pubmed_id",
    "tf_input",
    "tf_standard",
    "tf_uniprot",
    "gene_input",
    "gene_ensg",
    "cellline",
    "assay",
    "complex",
    "created_at",
    "ai_flags",
    "ai_notes",
    "ai_reviewed",
]

DEFAULT_INPUT = Path("data/gs_review_export_new.tsv")
DEFAULT_OUTPUT = Path("data/gs50_standardized.csv")


@dataclass(frozen=True)
class ConversionReport:
    rows: int
    unique_pmids: int
    unique_tfs: int
    padded_complex_rows: list[int]
    missing_ensg_symbols: list[tuple[int, str]]
    missing_tf_uniprot: list[str]


def project_root() -> Path:
    return Path(__file__).resolve().parents[1]


def load_ensg_to_symbol(root: Path) -> dict[str, str]:
    path = root / "data" / "gene_ensg_map.json"
    with path.open(encoding="utf-8") as f:
        symbol_to_ensg = json.load(f)

    ensg_to_symbol: dict[str, str] = {}
    for symbol, ensg in symbol_to_ensg.items():
        ensg_to_symbol.setdefault(str(ensg).strip(), str(symbol).strip().upper())
    return ensg_to_symbol


def load_tf_uniprot(root: Path) -> dict[str, str]:
    path = root / "data" / "hgnc_complete_set.txt"
    if not path.exists():
        return {}
    mapping: dict[str, str] = {}
    with path.open(encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f, delimiter="\t")
        for row in reader:
            symbol = (row.get("symbol") or "").strip().upper()
            uniprot_ids = (row.get("uniprot_ids") or "").strip()
            if symbol and uniprot_ids:
                mapping[symbol] = uniprot_ids.replace("|", ";")
    return mapping


def iter_gs_rows(input_path: Path) -> Iterable[tuple[int, list[str], bool]]:
    with input_path.open(encoding="utf-8", newline="") as f:
        reader = csv.reader(f, delimiter="\t")
        for line_no, row in enumerate(reader, start=1):
            if not row or not any(cell.strip() for cell in row):
                continue
            if line_no == 1 and [cell.strip() for cell in row[:6]] == [
                "PMID",
                "TF",
                "ENSG",
                "CellLine",
                "Assay",
                "complex",
            ]:
                continue
            if len(row) < 5:
                raise ValueError(f"Line {line_no}: expected at least 5 columns, got {len(row)}")
            if len(row) > 6:
                raise ValueError(f"Line {line_no}: expected at most 6 columns, got {len(row)}")

            padded = len(row) == 5
            row = [cell.strip() for cell in row]
            if padded:
                row.append("")
            yield line_no, row, padded


def convert_rows(input_path: Path, root: Path | None = None) -> tuple[list[dict[str, str]], ConversionReport]:
    root = root or project_root()
    ensg_to_symbol = load_ensg_to_symbol(root)
    tf_uniprot = load_tf_uniprot(root)

    records: list[dict[str, str]] = []
    padded_complex_rows: list[int] = []
    missing_ensg_symbols: list[tuple[int, str]] = []
    missing_tf_uniprot: set[str] = set()
    pmids: set[str] = set()
    tfs: set[str] = set()

    for idx, (line_no, row, padded) in enumerate(iter_gs_rows(input_path), start=1):
        if padded:
            padded_complex_rows.append(line_no)

        pmid, tf, ensg, cellline, assay, complex_note = row
        tf_standard = tf.upper()
        gene_symbol = ensg_to_symbol.get(ensg)
        if not gene_symbol:
            missing_ensg_symbols.append((line_no, ensg))
            gene_symbol = ""

        tf_uniprot_id = tf_uniprot.get(tf_standard, "")
        if not tf_uniprot_id:
            missing_tf_uniprot.add(tf_standard)

        pmids.add(pmid)
        tfs.add(tf_standard)
        records.append(
            {
                "id": str(idx),
                "pubmed_id": pmid,
                "tf_input": tf_standard,
                "tf_standard": tf_standard,
                "tf_uniprot": tf_uniprot_id,
                "gene_input": gene_symbol,
                "gene_ensg": ensg,
                "cellline": cellline,
                "assay": assay,
                "complex": complex_note,
                "created_at": "",
                "ai_flags": "",
                "ai_notes": "",
                "ai_reviewed": "0",
            }
        )

    if missing_ensg_symbols:
        details = ", ".join(f"line {line}: {ensg}" for line, ensg in missing_ensg_symbols[:10])
        raise ValueError(f"Cannot convert rows with unknown ENSG IDs: {details}")

    report = ConversionReport(
        rows=len(records),
        unique_pmids=len(pmids),
        unique_tfs=len(tfs),
        padded_complex_rows=padded_complex_rows,
        missing_ensg_symbols=missing_ensg_symbols,
        missing_tf_uniprot=sorted(missing_tf_uniprot),
    )
    return records, report


def write_standard_csv(records: list[dict[str, str]], output_path: Path) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=EXPORT_HEADER)
        writer.writeheader()
        writer.writerows(records)


def ensure_annotation_schema(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS annotations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            pubmed_id TEXT NOT NULL,
            tf_input TEXT NOT NULL,
            tf_standard TEXT,
            tf_uniprot TEXT,
            gene_input TEXT NOT NULL,
            gene_ensg TEXT,
            cellline TEXT,
            assay TEXT,
            complex TEXT,
            created_at TEXT DEFAULT (datetime('now'))
        )
        """
    )
    existing = {row[1] for row in conn.execute("PRAGMA table_info(annotations)").fetchall()}
    for column, definition in {
        "ai_flags": "TEXT",
        "ai_notes": "TEXT",
        "ai_reviewed": "INTEGER DEFAULT 0",
    }.items():
        if column not in existing:
            conn.execute(f"ALTER TABLE annotations ADD COLUMN {column} {definition}")
    conn.commit()


def import_records(records: list[dict[str, str]], db_path: Path, replace: bool = False) -> int:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    try:
        ensure_annotation_schema(conn)
        if replace:
            conn.execute("DELETE FROM annotations")
        conn.executemany(
            """
            INSERT INTO annotations (
                pubmed_id, tf_input, tf_standard, tf_uniprot, gene_input, gene_ensg,
                cellline, assay, complex, ai_flags, ai_notes, ai_reviewed
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                (
                    record["pubmed_id"],
                    record["tf_input"],
                    record["tf_standard"],
                    record["tf_uniprot"],
                    record["gene_input"],
                    record["gene_ensg"],
                    record["cellline"],
                    record["assay"],
                    record["complex"],
                    record["ai_flags"],
                    record["ai_notes"],
                    int(record["ai_reviewed"]),
                )
                for record in records
            ],
        )
        conn.commit()
        return len(records)
    finally:
        conn.close()


def print_report(report: ConversionReport, output_path: Path, db_path: Path | None, imported: int) -> None:
    print(f"rows: {report.rows}")
    print(f"unique_pmids: {report.unique_pmids}")
    print(f"unique_tfs: {report.unique_tfs}")
    print(f"padded_complex_rows: {report.padded_complex_rows or 'none'}")
    print(f"missing_tf_uniprot: {report.missing_tf_uniprot or 'none'}")
    print(f"csv: {output_path}")
    if db_path:
        print(f"db: {db_path}")
        print(f"imported: {imported}")


def parse_args() -> argparse.Namespace:
    root = project_root()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, default=root / DEFAULT_INPUT)
    parser.add_argument("--output", type=Path, default=root / DEFAULT_OUTPUT)
    parser.add_argument("--db", type=Path, help="Optional SQLite DB path to import into.")
    parser.add_argument(
        "--replace",
        action="store_true",
        help="Delete existing annotations before importing. Only valid with --db.",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    if args.replace and not args.db:
        raise SystemExit("--replace requires --db")

    records, report = convert_rows(args.input)
    write_standard_csv(records, args.output)

    imported = 0
    if args.db:
        imported = import_records(records, args.db, replace=args.replace)

    print_report(report, args.output, args.db, imported)


if __name__ == "__main__":
    main()
