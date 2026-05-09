#!/usr/bin/env python3
"""Group TRRUST data by PMID and output a TSV with all relationships per PMID."""
import argparse
import csv
import sys
from collections import defaultdict


def main():
    parser = argparse.ArgumentParser(
        description="Group TRRUST data by PMID, output one row per PMID."
    )
    parser.add_argument(
        "--input", default="data/raw/trrust_rawdata.human.tsv",
        help="Input TRRUST TSV (default: data/raw/trrust_rawdata.human.tsv)"
    )
    parser.add_argument(
        "--output", default="outputs/trrust_by_pmid.tsv",
        help="Output TSV path (default: outputs/trrust_by_pmid.tsv)"
    )
    args = parser.parse_args()

    pmid_map = defaultdict(list)  # pmid -> [(tf, target, direction), ...]

    with open(args.input, "r", encoding="utf-8") as f:
        reader = csv.reader(f, delimiter="\t")
        for row in reader:
            if len(row) < 4:
                continue
            tf, target, direction, pmid_raw = row
            # A single row may reference multiple PMIDs separated by ";"
            for pmid in pmid_raw.split(";"):
                pmid = pmid.strip()
                if pmid.isdigit():
                    pmid_map[pmid].append((tf.strip(), target.strip(), direction.strip()))

    with open(args.output, "w", encoding="utf-8", newline="") as f:
        writer = csv.writer(f, delimiter="\t")
        writer.writerow(["PMID", "Relationship_Count", "Relationships"])
        for pmid in sorted(pmid_map.keys(), key=int):
            entries = pmid_map[pmid]
            rel_strs = [f"{tf}->{target}({dr})" for tf, target, dr in entries]
            writer.writerow([pmid, len(entries), "; ".join(rel_strs)])

    print(f"Written {len(pmid_map)} PMIDs to {args.output}")


if __name__ == "__main__":
    main()
