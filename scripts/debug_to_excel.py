#!/usr/bin/env python3
"""清洗 analysis_results_debug.json，输出为 Excel 双 sheet。

用法：
    python scripts/debug_to_excel.py <debug.json> [-o output.xlsx]

Sheet1 "Entries": PMID, TF, Target, Assay, CellLine (每行一条条目)
Sheet2 "LLM Outputs": PMID, Round1_output, Round2_output (每篇一行)
"""

import argparse
import json
import sys

import pandas as pd


def main():
    parser = argparse.ArgumentParser(description="清洗 debug JSON 为 Excel")
    parser.add_argument("input", help="analysis_results_debug.json 路径")
    parser.add_argument("-o", "--output", default=None, help="输出 Excel 路径 (默认: 同名 .xlsx)")
    args = parser.parse_args()

    output_path = args.output or args.input.rsplit(".", 1)[0] + ".xlsx"

    with open(args.input, "r", encoding="utf-8") as f:
        data = json.load(f)

    entries = []
    outputs = []

    for pmid, entry in data.items():
        result = entry.get("result", [])
        if not isinstance(result, list):
            result = []

        round1 = entry.get("round1_analysis", "")
        round2 = entry.get("round2_clean", "")

        # Sheet1: structured entries
        if not result:
            entries.append({
                "PMID": pmid,
                "TF": "",
                "Target": "",
                "Assay": "",
                "CellLine": "",
            })
        else:
            for item in result:
                if not isinstance(item, dict):
                    continue
                entries.append({
                    "PMID": pmid,
                    "TF": item.get("TF", item.get("tf", "")),
                    "Target": item.get("Target", item.get("target", "")),
                    "Assay": item.get("assay", item.get("Assay", "")),
                    "CellLine": item.get("cellLine", item.get("CellLine", item.get("cellline", ""))),
                })

        # Sheet2: raw outputs
        outputs.append({
            "PMID": pmid,
            "Round1_output": round1,
            "Round2_output": round2,
        })

    df_entries = pd.DataFrame(entries)
    df_outputs = pd.DataFrame(outputs)

    # Write to Excel with formatting
    with pd.ExcelWriter(output_path, engine="openpyxl") as writer:
        df_entries.to_excel(writer, sheet_name="Entries", index=False)
        df_outputs.to_excel(writer, sheet_name="LLM Outputs", index=False)

        # Adjust column widths
        ws_entries = writer.sheets["Entries"]
        ws_entries.column_dimensions["A"].width = 12  # PMID
        ws_entries.column_dimensions["B"].width = 15  # TF
        ws_entries.column_dimensions["C"].width = 15  # Target
        ws_entries.column_dimensions["D"].width = 30  # Assay
        ws_entries.column_dimensions["E"].width = 30  # CellLine

        ws_outputs = writer.sheets["LLM Outputs"]
        ws_outputs.column_dimensions["A"].width = 12  # PMID
        ws_outputs.column_dimensions["B"].width = 80  # Round1
        ws_outputs.column_dimensions["C"].width = 80  # Round2

        # Enable text wrapping for output columns
        from openpyxl.styles import Alignment
        for row in ws_outputs.iter_rows(min_row=2, min_col=2, max_col=3):
            for cell in row:
                cell.alignment = Alignment(wrap_text=True, vertical="top")

    print(f"输出: {output_path}")
    print(f"  Entries: {len(entries)} 行")
    print(f"  LLM Outputs: {len(outputs)} 篇 PMID")


if __name__ == "__main__":
    main()
