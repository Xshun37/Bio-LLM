import argparse
import json
import os
import re

import pandas as pd

from bio_llm import normalize_tf, normalize_target
from bio_llm.evaluation import (
    normalize_direction,
    fuzzy_gene_match,
    classify_llm_entry,
    classify_missed_gt,
    compute_metrics,
    _get_field,
)


def load_trrust_by_pmid(tsv_path):
    """Load TRRUST data grouped by PMID.

    Returns dict: pmid -> [(tf, target, direction), ...]
    """
    if not tsv_path or not os.path.exists(tsv_path):
        return {}
    result = {}
    with open(tsv_path, "r", encoding="utf-8") as f:
        next(f)  # skip header
        for line in f:
            parts = line.strip().split("\t")
            if len(parts) < 3:
                continue
            pmid = parts[0].strip()
            entries = []
            for rel in parts[2].split("; "):
                rel = rel.strip()
                if not rel:
                    continue
                m = re.match(r"(\S+)->(\S+)\((\w+)\)", rel)
                if m:
                    direction = m.group(3)
                    if direction.lower() == "unknown":
                        continue
                    entries.append((m.group(1), m.group(2), direction))
            result[pmid] = entries
    return result


def parse_abstracts_file(abstracts_path):
    if not os.path.exists(abstracts_path):
        return {}

    with open(abstracts_path, "r", encoding="utf-8") as handle:
        content = handle.read()

    blocks = re.split(r"={10,}", content)
    result = {}
    for block in blocks:
        pmid_match = re.search(r"PMID:\s*(\d+)", block)
        if not pmid_match:
            continue

        pmid = pmid_match.group(1).strip()
        abstract_match = re.search(
            r"Abstract:\s*-{3,}\s*(.*?)(?:\n(?=={10,})|\Z)",
            block,
            re.DOTALL,
        )
        abstract_text = abstract_match.group(1).strip() if abstract_match else ""
        trrust_matches = re.findall(r"TRRUST Standard:\s*(\S+)\s*->\s*(\S+)\s*\((\w+)\)", block)
        result[pmid] = {
            "abstract": abstract_text,
            "trrust_entries": [
                (m[0].strip(), m[1].strip(), m[2].strip()) for m in trrust_matches
            ],
        }
    return result


def load_trrust(trrust_path):
    return pd.read_csv(
        trrust_path,
        sep="\t",
        header=None,
        names=["tf", "target", "direction", "pmid"],
        dtype={"pmid": str},
    )


def build_pair_map(rows):
    return {
        (normalize_tf(str(row.tf)), normalize_target(str(row.target))): str(row.direction)
        for row in rows.itertuples(index=False)
    }


def format_error_result(result):
    if isinstance(result, dict) and result.get("error"):
        return f"ERROR: {result['error']}"
    return None


def generate_html_report(llm_json, abstracts_file, output_file, debug_json=None, trrust_by_pmid=None):
    with open(llm_json, "r", encoding="utf-8") as handle:
        llm_data = json.load(handle)

    debug_data = {}
    if debug_json and os.path.exists(debug_json):
        with open(debug_json, "r", encoding="utf-8") as handle:
            debug_data = json.load(handle)

    abstracts = parse_abstracts_file(abstracts_file)
    trrust_data = load_trrust_by_pmid(trrust_by_pmid)
    html_content = """
    <html>
    <head>
        <meta charset="UTF-8">
        <style>
            body { font-family: sans-serif; line-height: 1.6; margin: 20px; background: #f4f4f9; }
            .card { background: white; border-radius: 8px; box-shadow: 0 2px 5px rgba(0,0,0,0.1); margin-bottom: 30px; padding: 20px; }
            .pmid-header { border-bottom: 2px solid #333; padding-bottom: 10px; margin-bottom: 15px; display: flex; justify-content: space-between; }
            .content-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 20px; }
            .abstract-box { background: #fdfdfd; padding: 15px; border-left: 4px solid #007bff; font-style: italic; font-size: 0.9em; white-space: pre-wrap; }
            table { width: 100%; border-collapse: collapse; margin-top: 10px; font-size: 0.85em; }
            th, td { border: 1px solid #ddd; padding: 8px; text-align: left; vertical-align: top; }
            th { background-color: #f2f2f2; }
            .status-ok { color: green; font-weight: bold; }
            .status-conflict { color: orange; font-weight: bold; }
            .status-partial { color: #996600; font-weight: bold; }
            .status-new { color: blue; font-weight: bold; }
            .status-newfound { color: #0066cc; font-weight: bold; }
            .status-miss { color: red; font-weight: bold; }
            .conf-5 { background: #d4edda; }
            .conf-4 { background: #e6f3e6; }
            .conf-3 { background: #fff3cd; }
            .conf-2 { background: #ffe5cc; }
            .conf-1 { background: #f8d7da; }
            .debug-section { margin-top: 20px; border: 1px solid #ddd; border-radius: 6px; padding: 0; background: #fafafa; }
            .debug-section summary { padding: 12px 16px; font-weight: bold; cursor: pointer; background: #e9ecef; border-radius: 6px; user-select: none; }
            .debug-section summary:hover { background: #dee2e6; }
            .debug-section[open] summary { border-radius: 6px 6px 0 0; border-bottom: 1px solid #ddd; }
            .debug-panel { display: grid; grid-template-columns: 1fr 1fr 1fr; gap: 12px; padding: 16px; }
            .round-box { background: white; border: 1px solid #e0e0e0; border-radius: 4px; padding: 12px; }
            .round-box strong { display: block; margin-bottom: 4px; color: #333; }
            .round-box pre { font-size: 0.8em; white-space: pre-wrap; max-height: 400px; overflow-y: auto; margin: 8px 0 0 0; line-height: 1.5; }
            .token-info { font-size: 0.75em; color: #888; margin-left: 8px; }
            @media (max-width: 900px) { .debug-panel { grid-template-columns: 1fr; } }
        </style>
    </head>
    <body>
        <h1>TF-Target Extraction Analysis Report</h1>
    """

    # --- Compute summary statistics ---
    metrics = compute_metrics(llm_data, trrust_data, abstracts,
                              normalize_tf_fn=normalize_tf,
                              normalize_target_fn=normalize_target)

    html_content += f"""
        <div class="card" style="background:#f0f8ff;">
            <h2>Summary Statistics</h2>
            <table style="width:auto;">
                <tr><th>Metric</th><th>Value</th><th>Note</th></tr>
                <tr><td>Total PMIDs</td><td>{metrics['total_pmids']}</td><td></td></tr>
                <tr><td>Ground-truth relationships</td><td>{metrics['total_gt']}</td><td>TRRUST entries for sampled PMIDs</td></tr>
                <tr><td>LLM extracted relationships</td><td>{metrics['total_llm']}</td><td>Total predictions</td></tr>
                <tr><td>Recall</td><td>{metrics['total_matched_gt']}/{metrics['total_gt']} = {metrics['recall']:.1f}%</td><td>Unique GT entries found by LLM</td></tr>
                <tr><td>Overall Precision</td><td>{metrics['total_consistent'] + metrics['total_conflict']}/{metrics['total_llm']} = {metrics['overall_precision']:.1f}%</td><td>LLM results matching any GT pair</td></tr>
                <tr style="background:#e8f5e9;"><td><b>Evaluable Precision</b></td><td><b>{metrics['total_consistent'] + metrics['total_conflict']}/{metrics['total_llm'] - metrics['total_new_found'] - metrics['total_new']} = {metrics['evaluable_precision']:.1f}%</b></td><td>Excl. New Found — among evaluable predictions, % matching GT</td></tr>
                <tr style="background:#e8f5e9;"><td><b>Direction Accuracy</b></td><td><b>{metrics['total_consistent']}/{metrics['total_consistent'] + metrics['total_conflict']} = {metrics['direction_accuracy']:.1f}%</b></td><td>Among GT-matched pairs, % with correct direction</td></tr>
                <tr><td colspan="3"></td></tr>
                <tr><td>Consistent (full match)</td><td style="color:green;font-weight:bold;">{metrics['total_consistent']}</td><td>TF + Target + Direction all match</td></tr>
                <tr><td>Conflict (direction mismatch)</td><td style="color:orange;font-weight:bold;">{metrics['total_conflict']}</td><td>Pair matches GT, but direction differs</td></tr>
                <tr><td>New Found (not in TRRUST)</td><td style="color:#0066cc;font-weight:bold;">{metrics['total_new_found']}</td><td>LLM discoveries — no GT to compare</td></tr>
                <tr><td>Missed (GT not found)</td><td style="color:red;font-weight:bold;">{metrics['total_missed']}</td><td>TRRUST entries the LLM did not find</td></tr>
                <tr><td>New (no GT for PMID)</td><td style="color:blue;font-weight:bold;">{metrics['total_new']}</td><td>PMIDs without any TRRUST entry</td></tr>
            </table>
        </div>
    """

    for pmid, llm_results in llm_data.items():
        info = abstracts.get(str(pmid), {})
        # Ground truth: prefer trrust_by_pmid, fall back to embedded lines
        gt_raw = trrust_data.get(str(pmid)) or info.get("trrust_entries", [])
        gt_entries_norm = [
            (normalize_tf(tf), normalize_target(target), dr)
            for tf, target, dr in gt_raw
        ]

        # Build TRRUST reference string
        trrust_ref = "; ".join(
            f"{tf}→{target} ({dr})" for tf, target, dr in gt_raw
        ) if gt_raw else "(none)"

        html_content += f"""
        <div class="card">
            <div class="pmid-header">
                <span style="font-size: 1.2em; font-weight: bold;">PMID: {pmid}</span>
                <a href="https://pubmed.ncbi.nlm.nih.gov/{pmid}/" target="_blank">View on PubMed</a>
            </div>
            <div style="background:#fffde7; padding:8px 12px; margin-bottom:15px; border-radius:4px; font-size:0.85em;">
                <strong>TRRUST Reference:</strong> {trrust_ref}
            </div>
            <div class="content-grid">
                <div class="abstract-box">
                    <strong>Abstract:</strong><br>
                    {info.get('abstract', 'Not found')}
                </div>
                <div>
                    <strong>Comparison Table:</strong>
                    <table>
                        <tr>
                            <th>TF → Target</th>
                            <th>TRRUST Dir</th>
                            <th>LLM Dir</th>
                            <th>Conf</th>
                            <th>Evidence</th>
                            <th>Status</th>
                        </tr>
        """

        llm_list = llm_results if isinstance(llm_results, list) else []
        error_message = format_error_result(llm_results)
        matched_gt_indices = set()

        if error_message:
            html_content += f"<tr><td colspan='6' class='status-conflict'>{error_message}</td></tr>"
        elif not llm_list:
            for idx, (gt_tf, gt_target, gt_dir) in enumerate(gt_entries_norm):
                html_content += (
                    f"<tr>"
                    f"<td>{gt_tf} → {gt_target}</td>"
                    f"<td>{gt_dir}</td>"
                    f"<td>N/A</td><td>-</td><td>-</td>"
                    f"<td class=\"status-miss\">Missed</td>"
                    f"</tr>"
                )
        else:
            for item in llm_list:
                if not isinstance(item, dict):
                    continue

                llm_tf = normalize_tf(_get_field(item, "tf", "TF"))
                llm_target = normalize_target(_get_field(item, "target", "Target"))
                llm_dir = _get_field(item, "direction", "Direction")
                confidence = _get_field(item, "confidence", "Confidence")
                evidence = _get_field(item, "evidence", "Evidence")

                status, gt_dir, gt_idx = classify_llm_entry(
                    llm_tf, llm_target, llm_dir, gt_entries_norm)

                if gt_idx >= 0:
                    matched_gt_indices.add(gt_idx)
                    if status == "Consistent":
                        status_class, status_text = "status-ok", "Consistent"
                    else:
                        status_class, status_text = "status-conflict", "Conflict"
                    gt_display = gt_dir
                elif gt_entries_norm:
                    status_class, status_text = "status-newfound", "New Found"
                    gt_display = "-"
                else:
                    status_class, status_text = "status-new", "New"
                    gt_display = "-"

                conf_num = int(confidence) if confidence.isdigit() else 0
                conf_display = f'<span class="conf-{conf_num}">{confidence}</span>' if conf_num else "-"

                html_content += f"""
                    <tr>
                        <td>{llm_tf} → {llm_target}</td>
                        <td>{gt_display}</td>
                        <td>{llm_dir}</td>
                        <td>{conf_display}</td>
                        <td style="font-size:0.8em">{evidence}</td>
                        <td class="{status_class}">{status_text}</td>
                    </tr>
                """

            # Show missed ground-truth entries
            for idx, (gt_tf, gt_target, gt_dir) in enumerate(gt_entries_norm):
                if idx not in matched_gt_indices:
                    html_content += (
                        f"<tr>"
                        f"<td>{gt_tf} → {gt_target}</td>"
                        f"<td>{gt_dir}</td>"
                        f"<td>N/A</td><td>-</td><td>-</td>"
                        f"<td class=\"status-miss\">Missed</td>"
                        f"</tr>"
                    )

        html_content += """
                    </table>
                </div>
            </div>
        """

        pmid_debug = debug_data.get(str(pmid), {})
        if pmid_debug and "round1_analysis" in pmid_debug:
            r1 = pmid_debug.get("round1_analysis", "")
            r1u = pmid_debug.get("round1_usage", {})
            r2r = pmid_debug.get("round2_raw", "")
            r2c = pmid_debug.get("round2_clean", "")
            r2u = pmid_debug.get("round2_usage", {})
            r1_tok = f"in:{r1u.get('input_tokens',0)} out:{r1u.get('output_tokens',0)}"
            r2_tok = f"in:{r2u.get('input_tokens',0)} out:{r2u.get('output_tokens',0)}"

            import html as _html
            html_content += f"""
            <details class="debug-section">
                <summary>LLM Debug — Round 1 & 2</summary>
                <div class="debug-panel">
                    <div class="round-box">
                        <strong>Round 1 Analysis</strong>
                        <span class="token-info">{r1_tok}</span>
                        <pre>{_html.escape(r1)}</pre>
                    </div>
                    <div class="round-box">
                        <strong>Round 2 Raw</strong>
                        <span class="token-info">{r2_tok}</span>
                        <pre>{_html.escape(r2r)}</pre>
                    </div>
                    <div class="round-box">
                        <strong>Round 2 Cleaned</strong>
                        <pre>{_html.escape(r2c)}</pre>
                    </div>
                </div>
            </details>
            """

        html_content += """
        </div>
        """

    # --- Excluded PMIDs section ---
    from bio_llm import load_anomalies
    anomalies = load_anomalies()
    if anomalies:
        html_content += """
        <div class="card" style="background:#fff5f5;">
            <h2 style="color:#c00;">Excluded PMIDs (Curated Anomalies)</h2>
            <p style="font-size:0.85em; color:#666;">
                These PMIDs were excluded from sampling due to known issues
                recorded in <code>data/curated/trrust_anomalies.jsonl</code>.
            </p>
            <table>
                <tr><th>PMID</th><th>Type</th><th>TRRUST Entry</th><th>Issue</th></tr>
        """
        for pmid in sorted(anomalies.keys(), key=int):
            for entry in anomalies[pmid]:
                html_content += (
                    f"<tr>"
                    f"<td><a href=\"https://pubmed.ncbi.nlm.nih.gov/{pmid}/\" "
                    f"target=\"_blank\">{pmid}</a></td>"
                    f"<td style=\"color:#c00;\">{entry.get('anomaly_type', '?')}</td>"
                    f"<td>{entry.get('trrust_entry', '?')}</td>"
                    f"<td style=\"font-size:0.85em;\">{entry.get('issue', '')}</td>"
                    f"</tr>"
                )
        html_content += "</table></div>"

    html_content += "</body></html>"
    with open(output_file, "w", encoding="utf-8") as handle:
        handle.write(html_content)
    print(f"HTML report generated: {output_file}")


def build_parser():
    parser = argparse.ArgumentParser(description="生成 TF-Target 提取结果对比报告。")
    parser.add_argument("--llm-json", default="outputs/analysis_results.json", help="LLM 输出 JSON 文件路径")
    parser.add_argument("--abstracts", default="data/interim/abstracts_for_test.txt", help="包含摘要及标准答案的文本路径")
    parser.add_argument("--output", default="outputs/report.html", help="生成的 HTML 报告文件名")
    parser.add_argument("--debug-json", default=None, help="Debug JSON 文件路径 (optional)")
    parser.add_argument("--trrust-by-pmid", default="data/raw/trrust_by_pmid.tsv",
                        help="TRRUST by-PMID TSV 文件路径 (default: outputs/trrust_by_pmid.tsv)")
    return parser


def main():
    args = build_parser().parse_args()
    generate_html_report(args.llm_json, args.abstracts, args.output,
                         debug_json=args.debug_json,
                         trrust_by_pmid=args.trrust_by_pmid)


if __name__ == "__main__":
    main()

