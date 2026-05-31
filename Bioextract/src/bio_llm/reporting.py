import argparse
import html as _html
import json
import os
import re

from bio_llm import normalize_tf, normalize_target
from bio_llm.evaluation import (
    classify_llm_entry,
    compute_metrics,
    load_gold_standard,
    match_assays,
    match_cellline,
    _get_field,
)
from bio_llm.result_utils import load_json, normalize_results_map


DEFAULT_TEXT_SOURCE = "fitz"


def format_error_result(result):
    if isinstance(result, dict) and result.get("error"):
        return f"ERROR: {result['error']}"
    return None


# ───────────────────────────────────────────────────────────
#  Production 报告辅助函数
# ───────────────────────────────────────────────────────────

def _md_to_html(text):
    """简易 markdown → HTML：粗体、标题、列表。"""
    text = _html.escape(text)
    text = re.sub(r'^#{3}\s+(.+)$', r'<h4>\1</h4>', text, flags=re.MULTILINE)
    text = re.sub(r'^#{2}\s+(.+)$', r'<h3>\1</h3>', text, flags=re.MULTILINE)
    text = re.sub(r'^#\s+(.+)$', r'<h2>\1</h2>', text, flags=re.MULTILINE)
    text = re.sub(r'\*\*(.+?)\*\*', r'<strong>\1</strong>', text)
    text = re.sub(r'^={3,}$', '<hr>', text, flags=re.MULTILINE)
    text = text.replace('\n', '<br>\n')
    return text


def _format_json(text):
    try:
        parsed = json.loads(text)
        return _html.escape(json.dumps(parsed, indent=2, ensure_ascii=False))
    except (json.JSONDecodeError, TypeError):
        return _html.escape(text or "(empty)")


def _usage_badge(usage):
    if not usage:
        return ""
    inp = usage.get("input_tokens", 0)
    out = usage.get("output_tokens", 0)
    return f'<span class="badge">in:{inp:,} out:{out:,}</span>'


# ───────────────────────────────────────────────────────────
#  主函数
# ───────────────────────────────────────────────────────────

def generate_html_report(llm_json, text_source, output_file,
                         debug_json=None, gold_standard=None,
                         mode="auto"):
    """生成 HTML 报告。

    mode:
      - "gold_standard": 金标准对比报告（需要 gold_standard TSV）
      - "production": 生产提取报告（无 ground truth）
    """
    llm_data = normalize_results_map(load_json(llm_json))

    debug_data = {}
    if debug_json and os.path.exists(debug_json):
        debug_data = normalize_results_map(load_json(debug_json))

    effective_mode = mode
    if mode == "auto":
        effective_mode = "gold_standard" if gold_standard else "production"

    if effective_mode == "production":
        _generate_production_report(llm_data, debug_data, output_file)
    else:
        _generate_gs_report(llm_data, text_source, debug_data, gold_standard, output_file)


# ───────────────────────────────────────────────────────────
#  Production 模式报告
# ───────────────────────────────────────────────────────────

def _generate_production_report(llm_data, debug_data, output_file):
    papers = sorted(llm_data.keys())
    total_papers = len(papers)
    total_relations = 0
    total_input_tokens = 0
    total_output_tokens = 0
    errors = 0

    # 合并 debug_data 到 llm_data 的条目中（debug_data 有更详细信息）
    merged = {}
    for pid in papers:
        entry = {}
        if pid in debug_data:
            entry.update(debug_data[pid])
        raw = llm_data[pid]
        if isinstance(raw, list):
            entry["result"] = raw
        elif isinstance(raw, dict):
            if "result" not in entry:
                entry.update(raw)
        merged[pid] = entry

    for pid, entry in merged.items():
        result = entry.get("result", [])
        if isinstance(result, list):
            total_relations += len(result)
        if entry.get("error"):
            errors += 1
        for key in ("round1_usage", "round2_usage"):
            u = entry.get(key, {})
            total_input_tokens += u.get("input_tokens", 0)
            total_output_tokens += u.get("output_tokens", 0)

    total_tokens = total_input_tokens + total_output_tokens
    avg_tokens = total_tokens // total_papers if total_papers else 0

    h = f"""<!DOCTYPE html>
<html lang="zh">
<head>
<meta charset="UTF-8">
<title>Production Report</title>
<style>
body {{ font-family: -apple-system, sans-serif; line-height: 1.6; margin: 20px; background: #f4f4f9; color: #333; }}
h1 {{ font-size: 1.5em; margin-bottom: 5px; }}
.subtitle {{ color: #666; font-size: 0.9em; margin-bottom: 20px; }}
.card {{ background: white; border-radius: 8px; box-shadow: 0 2px 5px rgba(0,0,0,0.1); margin-bottom: 24px; padding: 20px; }}
.summary-grid {{ display: grid; grid-template-columns: repeat(4, 1fr); gap: 16px; margin-bottom: 24px; }}
.summary-card {{ background: white; border-radius: 8px; box-shadow: 0 1px 3px rgba(0,0,0,0.08); padding: 16px; text-align: center; border-top: 3px solid #007bff; }}
.summary-card .num {{ font-size: 2em; font-weight: bold; color: #007bff; }}
.summary-card .label {{ font-size: 0.85em; color: #888; margin-top: 4px; }}
.paper-header {{ display: flex; justify-content: space-between; align-items: center; border-bottom: 2px solid #333; padding-bottom: 10px; margin-bottom: 15px; }}
.paper-header h2 {{ margin: 0; font-size: 1.1em; word-break: break-all; }}
.rel-count {{ background: #007bff; color: white; border-radius: 12px; padding: 2px 10px; font-size: 0.85em; }}
.rel-count.zero {{ background: #999; }}
.rel-count.error {{ background: #dc3545; }}
table {{ width: 100%; border-collapse: collapse; font-size: 0.85em; margin-top: 10px; }}
th, td {{ border: 1px solid #ddd; padding: 8px; text-align: left; vertical-align: top; }}
th {{ background: #f2f2f2; font-weight: 600; }}
.badge {{ background: #e9ecef; border-radius: 4px; padding: 2px 8px; font-size: 0.8em; color: #555; margin-left: 8px; }}
details {{ margin-top: 16px; border: 1px solid #ddd; border-radius: 6px; background: #fafafa; }}
summary {{ padding: 10px 16px; font-weight: bold; cursor: pointer; background: #e9ecef; border-radius: 6px; user-select: none; }}
summary:hover {{ background: #dee2e6; }}
details[open] summary {{ border-radius: 6px 6px 0 0; border-bottom: 1px solid #ddd; }}
.debug-grid {{ display: grid; grid-template-columns: 1fr 1fr; gap: 16px; padding: 16px; }}
.debug-box {{ background: white; border: 1px solid #ddd; border-radius: 6px; padding: 12px; }}
.debug-box h4 {{ margin: 0 0 8px 0; font-size: 0.9em; color: #555; border-bottom: 1px solid #eee; padding-bottom: 6px; }}
.debug-box pre {{ margin: 0; font-size: 0.8em; white-space: pre-wrap; word-break: break-word; max-height: 500px; overflow-y: auto; }}
.debug-box .analysis {{ font-size: 0.85em; max-height: 600px; overflow-y: auto; line-height: 1.5; }}
.norm-table {{ margin-top: 8px; }}
.norm-table td {{ font-size: 0.85em; padding: 4px 8px; }}
.token-bar {{ display: flex; gap: 12px; padding: 12px 16px; border-top: 1px solid #eee; font-size: 0.85em; color: #666; }}
.error-box {{ background: #fff3f3; border: 1px solid #ffcdd2; border-radius: 6px; padding: 12px; color: #c62828; }}
@media (max-width: 900px) {{ .debug-grid {{ grid-template-columns: 1fr; }} .summary-grid {{ grid-template-columns: repeat(2, 1fr); }} }}
</style>
</head>
<body>

<h1>Production Report</h1>
<div class="subtitle">TF-Target 调控关系提取</div>

<div class="summary-grid">
  <div class="summary-card"><div class="num">{total_papers}</div><div class="label">论文</div></div>
  <div class="summary-card"><div class="num">{total_relations}</div><div class="label">提取关系</div></div>
  <div class="summary-card"><div class="num">{total_tokens:,}</div><div class="label">总 Tokens</div></div>
  <div class="summary-card"><div class="num">{avg_tokens:,}</div><div class="label">平均 Tokens/篇</div></div>
</div>
"""

    for pid in papers:
        entry = merged[pid]
        result = entry.get("result", [])
        is_error = bool(entry.get("error"))
        rel_count = len(result) if isinstance(result, list) else 0
        count_cls = "error" if is_error else ("zero" if rel_count == 0 else "")

        h += f'<div class="card">\n'
        h += f'<div class="paper-header"><h2>{_html.escape(pid)}</h2>'
        h += f'<span class="rel-count {count_cls}">{rel_count} 条关系</span></div>\n'

        if is_error:
            h += f'<div class="error-box">ERROR: {_html.escape(str(entry["error"]))}</div>\n'
            if "round1_analysis" not in entry:
                h += '</div>\n'
                continue

        # 结果表格
        if isinstance(result, list) and result:
            h += '<table><tr><th>TF</th><th>Target</th><th>Assay</th><th>CellLine</th></tr>\n'
            for r in result:
                h += f'<tr><td><strong>{_html.escape(r.get("TF",""))}</strong></td>'
                h += f'<td>{_html.escape(r.get("Target",""))}</td>'
                h += f'<td>{_html.escape(r.get("assay",""))}</td>'
                h += f'<td>{_html.escape(r.get("cellLine",""))}</td></tr>\n'
            h += '</table>\n'

        # Debug 详情
        has_debug = "round1_analysis" in entry or "round2_raw" in entry
        if has_debug:
            h += '<details>\n<summary>LLM 推理过程</summary>\n'
            h += '<div class="debug-grid">\n'

            r1 = entry.get("round1_analysis", "")
            r1_html = _md_to_html(r1) if r1 else "<em>(empty)</em>"
            h += f'<div class="debug-box"><h4>Round 1 分析 {_usage_badge(entry.get("round1_usage"))}</h4>'
            h += f'<div class="analysis">{r1_html}</div></div>\n'

            r2 = entry.get("round2_clean") or entry.get("round2_raw", "")
            r2_html = _format_json(r2)
            h += f'<div class="debug-box"><h4>Round 2 结构化输出 {_usage_badge(entry.get("round2_usage"))}</h4>'
            h += f'<pre>{r2_html}</pre></div>\n'

            h += '</div>\n'

            norm_log = entry.get("normalization_log", [])
            if norm_log:
                h += '<div style="padding: 0 16px 12px;"><strong>基因名标准化:</strong>'
                h += '<table class="norm-table"><tr><th>原始</th><th>标准化</th><th>类型</th><th>状态</th></tr>\n'
                for nl in norm_log:
                    h += f'<tr><td>{_html.escape(nl.get("original",""))}</td>'
                    h += f'<td><strong>{_html.escape(nl.get("normalized",""))}</strong></td>'
                    h += f'<td>{_html.escape(nl.get("type",""))}</td>'
                    h += f'<td>{_html.escape(nl.get("status",""))}</td></tr>\n'
                h += '</table></div>\n'

            h += '</details>\n'

        r1u = entry.get("round1_usage", {})
        r2u = entry.get("round2_usage", {})
        paper_total = (r1u.get("input_tokens", 0) + r1u.get("output_tokens", 0)
                       + r2u.get("input_tokens", 0) + r2u.get("output_tokens", 0))
        if paper_total:
            h += f'<div class="token-bar">Token: {paper_total:,} (R1: {r1u.get("input_tokens",0):,}+{r1u.get("output_tokens",0):,} | R2: {r2u.get("input_tokens",0):,}+{r2u.get("output_tokens",0):,})</div>\n'

        h += '</div>\n'

    if errors:
        h += f'<div class="card"><div class="error-box">⚠ {errors} 篇论文处理出错</div></div>\n'

    h += '</body>\n</html>'

    with open(output_file, "w", encoding="utf-8") as handle:
        handle.write(h)
    print(f"HTML 报告已生成: {output_file}")


# ───────────────────────────────────────────────────────────
#  Gold Standard 模式报告（原逻辑）
# ───────────────────────────────────────────────────────────

def _generate_gs_report(llm_data, text_source, debug_data, gold_standard, output_file):
    from bio_llm.analysis import load_local_fulltexts

    # Load gold standard
    gs_data = load_gold_standard(gold_standard)

    # Load full texts for display in report
    pmids = list(llm_data.keys())
    fulltexts = load_local_fulltexts(pmids, source=text_source)
    abstracts = {
        pmid: {"abstract": ft["full_text"]}
        for pmid, ft in fulltexts.items()
    }

    html_content = """
    <html>
    <head>
        <meta charset="UTF-8">
        <style>
            body { font-family: sans-serif; line-height: 1.6; margin: 20px; background: #f4f4f9; }
            .card { background: white; border-radius: 8px; box-shadow: 0 2px 5px rgba(0,0,0,0.1); margin-bottom: 30px; padding: 20px; }
            .pmid-header { border-bottom: 2px solid #333; padding-bottom: 10px; margin-bottom: 15px; display: flex; justify-content: space-between; }
            .content-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 20px; }
            .abstract-box { background: #fdfdfd; padding: 15px; border-left: 4px solid #007bff; font-size: 0.9em; white-space: pre-wrap; max-height: 500px; overflow-y: auto; }
            table { width: 100%; border-collapse: collapse; margin-top: 10px; font-size: 0.85em; }
            th, td { border: 1px solid #ddd; padding: 8px; text-align: left; vertical-align: top; }
            th { background-color: #f2f2f2; }
            .status-ok { color: green; font-weight: bold; }
            .status-partial { color: #e67e00; font-weight: bold; }
            .status-new { color: blue; font-weight: bold; }
            .status-newfound { color: #0066cc; font-weight: bold; }
            .status-miss { color: red; font-weight: bold; }
            .match-yes { color: green; }
            .match-no { color: #c00; }
            .metrics-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 20px; margin-top: 15px; }
            .metrics-card { background: white; border-radius: 6px; padding: 15px; border: 1px solid #ddd; }
            .metrics-card h3 { margin: 0 0 10px 0; font-size: 1em; padding-bottom: 8px; border-bottom: 2px solid #ddd; }
            .metrics-card.primary { border-color: #4caf50; background: #f1f8e9; }
            .metrics-card.primary h3 { border-bottom-color: #4caf50; }
            .metrics-card table { width: 100%; }
            .metrics-card td { padding: 4px 8px; font-size: 0.9em; }
            .metrics-card td:first-child { font-weight: bold; width: 60px; }
            .metrics-card td:last-child { color: #666; font-size: 0.85em; }
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
        <h1>TF-Target 调控关系提取分析报告（全文模式）</h1>
    """

    # --- Compute summary statistics ---
    metrics = compute_metrics(llm_data, gs_data,
                              normalize_tf_fn=normalize_tf,
                              normalize_target_fn=normalize_target)

    html_content += f"""
        <div class="card" style="background:#f0f8ff;">
            <h2>统计概览</h2>
            <table style="width:auto; margin-bottom:20px;">
                <tr><th>指标</th><th>数值</th></tr>
                <tr><td>论文总数</td><td>{metrics['total_pmids']}</td></tr>
                <tr><td>金标准条目数</td><td>{metrics['total_gt']}</td></tr>
                <tr><td>LLM 提取数</td><td>{metrics['total_llm']}</td></tr>
                <tr><td>完全匹配</td><td style="color:green;font-weight:bold;">{metrics['tp_full']}</td></tr>
                <tr><td>模糊匹配（部分匹配）</td><td style="color:orange;font-weight:bold;">{metrics['fp_partial']}</td></tr>
                <tr><td>新发现</td><td style="color:#0066cc;font-weight:bold;">{metrics['fp_rel_count']}</td></tr>
                <tr><td>遗漏（模糊级）</td><td style="color:red;font-weight:bold;">{metrics['total_missed_rel']}</td></tr>
                <tr><td>无金标准</td><td style="color:gray;font-weight:bold;">{metrics['total_new']}</td></tr>
            </table>

            <div class="metrics-grid">
                <div class="metrics-card primary">
                    <h3>全数据集 · 完全匹配 (TF+Target+Assay+CellLine)</h3>
                    <table>
                        <tr><td>精确率</td><td>{metrics['tp_full']}/{metrics['tp_full'] + metrics['fp_partial'] + metrics['fp_rel_count']} = <b>{metrics['precision_full']:.1f}%</b></td><td>TP / (TP + 部分 + 新发现)</td></tr>
                        <tr><td>召回率</td><td>{metrics['tp_full']}/{metrics['total_gt']} = <b>{metrics['recall_full']:.1f}%</b></td><td>TP / total_GT</td></tr>
                        <tr><td>F1</td><td><b>{metrics['f1_full']:.1f}%</b></td><td>2PR/(P+R)</td></tr>
                    </table>
                </div>

                <div class="metrics-card">
                    <h3>全数据集 · 模糊匹配 (TF+Target)</h3>
                    <table>
                        <tr><td>精确率</td><td>{metrics['tp_rel']}/{metrics['tp_rel'] + metrics['fp_rel']} = {metrics['precision_rel']:.1f}%</td><td>关系命中 / (命中 + 新发现)</td></tr>
                        <tr><td>召回率</td><td>{metrics['tp_rel']}/{metrics['total_gt']} = {metrics['recall_rel']:.1f}%</td><td>关系命中 / total_GT</td></tr>
                        <tr><td>F1</td><td>{metrics['f1_rel']:.1f}%</td><td>2PR/(P+R)</td></tr>
                    </table>
                </div>

                <div class="metrics-card">
                    <h3>去除仅Literature · 完全匹配</h3>
                    <table>
                        <tr><td>精确率</td><td>{metrics['exp_tp_full']}/{metrics['exp_tp_full'] + (metrics['exp_tp_rel'] - metrics['exp_tp_full']) + metrics['exp_fp_new_found']} = {metrics['exp_precision']:.1f}%</td><td>TP / (TP + 部分 + 新发现)</td></tr>
                        <tr><td>召回率</td><td>{metrics['exp_tp_full']}/{metrics['exp_gt']} = {metrics['exp_recall']:.1f}%</td><td>TP / exp_GT</td></tr>
                        <tr><td>F1</td><td>{metrics['exp_f1']:.1f}%</td><td>2PR/(P+R)</td></tr>
                    </table>
                </div>

                <div class="metrics-card">
                    <h3>去除仅Literature · 模糊匹配</h3>
                    <table>
                        <tr><td>精确率</td><td>{metrics['exp_tp_rel']}/{metrics['exp_tp_rel'] + metrics['exp_fp_new_found']} = {metrics['exp_precision_rel']:.1f}%</td><td>关系命中 / (命中 + 新发现)</td></tr>
                        <tr><td>召回率</td><td>{metrics['exp_tp_rel']}/{metrics['exp_gt']} = {metrics['exp_recall_rel']:.1f}%</td><td>关系命中 / exp_GT</td></tr>
                        <tr><td>F1</td><td>{metrics['exp_f1_rel']:.1f}%</td><td>2PR/(P+R)</td></tr>
                    </table>
                </div>
            </div>
        </div>
    """

    for pmid, llm_results in llm_data.items():
        info = abstracts.get(str(pmid), {})
        gt_raw = gs_data.get(str(pmid), [])
        gt_entries_norm = [
            (normalize_tf(tf), normalize_target(target), assay, cellline, ensg)
            for tf, target, assay, cellline, ensg in gt_raw
        ]

        # Build Gold Standard reference string
        gs_ref = "; ".join(
            f"{tf}→{target} [{assay}] [{cellline}]"
            for tf, target, assay, cellline, ensg in gt_raw
        ) if gt_raw else "(none)"

        html_content += f"""
        <div class="card">
            <div class="pmid-header">
                <span style="font-size: 1.2em; font-weight: bold;">PMID: {pmid}</span>
                <a href="https://pubmed.ncbi.nlm.nih.gov/{pmid}/" target="_blank">在 PubMed 查看</a>
            </div>
            <div style="background:#fffde7; padding:8px 12px; margin-bottom:15px; border-radius:4px; font-size:0.85em;">
                <strong>金标准：</strong> {_html.escape(gs_ref)}
            </div>
            <div class="content-grid">
                <div class="abstract-box">
                    <strong>全文：</strong><br>
                    {_html.escape(info.get('abstract', '未找到'))}
                </div>
                <div>
                    <strong>对比表：</strong>
                    <table>
                        <tr>
                            <th>TF → Target</th>
                            <th>GT Assay</th>
                            <th>LLM Assay</th>
                            <th>GT CellLine</th>
                            <th>LLM CellLine</th>
                            <th>状态</th>
                        </tr>
        """

        llm_list = llm_results if isinstance(llm_results, list) else []
        error_message = format_error_result(llm_results)
        matched_gt_indices = set()

        if error_message:
            html_content += f"<tr><td colspan='6' class='status-miss'>{_html.escape(error_message)}</td></tr>"
        elif not llm_list:
            for gt_tf, gt_target, gt_assay, gt_cellline, gt_ensg in gt_entries_norm:
                html_content += (
                    f"<tr>"
                    f"<td>{gt_tf} → {gt_target}</td>"
                    f"<td>{_html.escape(gt_assay)}</td>"
                    f"<td>N/A</td>"
                    f"<td>{_html.escape(gt_cellline)}</td>"
                    f"<td>N/A</td>"
                    f"<td class=\"status-miss\">遗漏</td>"
                    f"</tr>"
                )
        else:
            # Greedy 1-to-1 matching per PMID
            claimed_gt = set()
            for item in llm_list:
                if not isinstance(item, dict):
                    continue

                llm_tf = normalize_tf(_get_field(item, "tf", "TF"))
                llm_target = normalize_target(_get_field(item, "target", "Target"))
                llm_assay = _get_field(item, "assay", "Assay")
                llm_cellline = _get_field(item, "cellLine", "CellLine", "cellline")

                rel_status, full_status, gt_idx = classify_llm_entry(
                    llm_tf, llm_target, llm_assay, llm_cellline,
                    gt_entries_norm, claimed_gt)

                if full_status == "full_match":
                    matched_gt_indices.add(gt_idx)
                    claimed_gt.add(gt_idx)
                    status_class, status_text = "status-ok", "完全匹配"
                    gt_assay = gt_entries_norm[gt_idx][2]
                    gt_cellline = gt_entries_norm[gt_idx][3]
                    assay_class = "match-yes"
                    cl_class = "match-yes"
                elif full_status == "partial_match":
                    matched_gt_indices.add(gt_idx)
                    claimed_gt.add(gt_idx)
                    status_class, status_text = "status-partial", "部分匹配"
                    gt_assay = gt_entries_norm[gt_idx][2]
                    gt_cellline = gt_entries_norm[gt_idx][3]
                    assay_ok = match_assays(llm_assay, gt_assay)
                    cl_ok = match_cellline(llm_cellline, gt_cellline)
                    assay_class = "match-yes" if assay_ok else "match-no"
                    cl_class = "match-yes" if cl_ok else "match-no"
                else:
                    gt_assay, gt_cellline = "-", "-"
                    assay_class = cl_class = ""
                    if gt_entries_norm:
                        status_class, status_text = "status-newfound", "新发现"
                    else:
                        status_class, status_text = "status-new", "无金标准"

                html_content += f"""
                    <tr>
                        <td>{llm_tf} → {llm_target}</td>
                        <td style="font-size:0.8em">{_html.escape(gt_assay)}</td>
                        <td style="font-size:0.8em" class="{assay_class}">{_html.escape(llm_assay)}</td>
                        <td style="font-size:0.8em">{_html.escape(gt_cellline)}</td>
                        <td style="font-size:0.8em" class="{cl_class}">{_html.escape(llm_cellline)}</td>
                        <td class="{status_class}">{status_text}</td>
                    </tr>
                """

            # Show missed ground-truth entries
            for idx, (gt_tf, gt_target, gt_assay, gt_cellline, gt_ensg) in enumerate(gt_entries_norm):
                if idx not in matched_gt_indices:
                    html_content += (
                        f"<tr>"
                        f"<td>{gt_tf} → {gt_target}</td>"
                        f"<td style=\"font-size:0.8em\">{_html.escape(gt_assay)}</td>"
                        f"<td>N/A</td>"
                        f"<td style=\"font-size:0.8em\">{_html.escape(gt_cellline)}</td>"
                        f"<td>N/A</td>"
                        f"<td class=\"status-miss\">遗漏</td>"
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

            html_content += f"""
            <details class="debug-section">
                <summary>LLM 调试信息 — Round 1 &amp; 2</summary>
                <div class="debug-panel">
                    <div class="round-box">
                        <strong>Round 1 分析</strong>
                        <span class="token-info">{r1_tok}</span>
                        <pre>{_html.escape(r1)}</pre>
                    </div>
                    <div class="round-box">
                        <strong>Round 2 原始输出</strong>
                        <span class="token-info">{r2_tok}</span>
                        <pre>{_html.escape(r2r)}</pre>
                    </div>
                    <div class="round-box">
                        <strong>Round 2 清洗后</strong>
                        <pre>{_html.escape(r2c)}</pre>
                    </div>
                </div>
            </details>
            """

        html_content += """
        </div>
        """

    html_content += "</body></html>"
    with open(output_file, "w", encoding="utf-8") as handle:
        handle.write(html_content)
    print(f"HTML 报告已生成: {output_file}")


def build_parser():
    parser = argparse.ArgumentParser(description="生成 TF-Target 提取结果报告。")
    parser.add_argument("--llm-json", default="outputs/analysis_results.json",
                        help="LLM 输出 JSON 文件路径")
    parser.add_argument("--text-source", default=DEFAULT_TEXT_SOURCE,
                        choices=["fitz", "hybrid", "nougat"],
                        help="论文全文来源目录 (金标准模式)")
    parser.add_argument("--output", default="outputs/report.html",
                        help="生成的 HTML 报告文件名")
    parser.add_argument("--debug-json", default=None,
                        help="Debug JSON 文件路径 (optional)")
    parser.add_argument("--gold-standard", default=None,
                        help="金标准 TSV 文件路径 (金标准模式)")
    parser.add_argument("--mode", default="auto",
                        choices=["auto", "gold_standard", "production"],
                        help="报告模式: auto / gold_standard (对比评估) / production (纯展示)")
    return parser


def main():
    args = build_parser().parse_args()
    generate_html_report(args.llm_json, args.text_source, args.output,
                         debug_json=args.debug_json,
                         gold_standard=args.gold_standard,
                         mode=args.mode)


if __name__ == "__main__":
    main()
