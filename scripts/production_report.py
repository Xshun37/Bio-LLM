#!/usr/bin/env python3
"""生成生产运行的 HTML 可视化报告。

读取 production_results_debug.json，生成带卡片布局的 HTML 报告，
展示每篇论文的提取结果、LLM 推理过程和 token 用量。

用法:
    python scripts/production_report.py outputs/production_20260530_024222
    # → 同目录生成 production_report.html
"""

import argparse
import html as _html
import json
import os
import re
import sys

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _md_to_html(text):
    """简易 markdown → HTML：粗体、标题、表格、列表。"""
    text = _html.escape(text)
    # 标题
    text = re.sub(r'^#{3}\s+(.+)$', r'<h4>\1</h4>', text, flags=re.MULTILINE)
    text = re.sub(r'^#{2}\s+(.+)$', r'<h3>\1</h3>', text, flags=re.MULTILINE)
    text = re.sub(r'^#\s+(.+)$', r'<h2>\1</h2>', text, flags=re.MULTILINE)
    # 粗体
    text = re.sub(r'\*\*(.+?)\*\*', r'<strong>\1</strong>', text)
    # 分隔线
    text = re.sub(r'^={3,}$', '<hr>', text, flags=re.MULTILINE)
    # 换行
    text = text.replace('\n', '<br>\n')
    return text


def _format_json(text):
    """格式化 JSON 字符串，美化显示。"""
    try:
        parsed = json.loads(text)
        return _html.escape(json.dumps(parsed, indent=2, ensure_ascii=False))
    except (json.JSONDecodeError, TypeError):
        return _html.escape(text or "(empty)")


def _usage_badge(usage):
    """生成 token 用量 badge。"""
    if not usage:
        return ""
    inp = usage.get("input_tokens", 0)
    out = usage.get("output_tokens", 0)
    return f'<span class="badge">in:{inp:,} out:{out:,}</span>'


def generate_report(debug_json_path, output_path):
    with open(debug_json_path, "r", encoding="utf-8") as f:
        data = json.load(f)

    papers = sorted(data.keys())
    total_papers = len(papers)
    total_relations = 0
    total_input_tokens = 0
    total_output_tokens = 0
    errors = 0

    for pid in papers:
        entry = data[pid]
        result = entry.get("result", [])
        if isinstance(result, list):
            total_relations += len(result)
        if "error" in entry:
            errors += 1
        for key in ("round1_usage", "round2_usage"):
            u = entry.get(key, {})
            total_input_tokens += u.get("input_tokens", 0)
            total_output_tokens += u.get("output_tokens", 0)

    total_tokens = total_input_tokens + total_output_tokens
    avg_tokens = total_tokens // total_papers if total_papers else 0

    # ── Build HTML ──
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
<div class="subtitle">{os.path.basename(os.path.dirname(debug_json_path))}</div>

<div class="summary-grid">
  <div class="summary-card"><div class="num">{total_papers}</div><div class="label">论文</div></div>
  <div class="summary-card"><div class="num">{total_relations}</div><div class="label">提取关系</div></div>
  <div class="summary-card"><div class="num">{total_tokens:,}</div><div class="label">总 Tokens</div></div>
  <div class="summary-card"><div class="num">{avg_tokens:,}</div><div class="label">平均 Tokens/篇</div></div>
</div>
"""

    for pid in papers:
        entry = data[pid]
        result = entry.get("result", [])
        is_error = "error" in entry
        rel_count = len(result) if isinstance(result, list) else 0
        count_cls = "error" if is_error else ("zero" if rel_count == 0 else "")

        h += f'<div class="card">\n'
        h += f'<div class="paper-header"><h2>{_html.escape(pid)}</h2>'
        h += f'<span class="rel-count {count_cls}">{rel_count} 条关系</span></div>\n'

        if is_error:
            h += f'<div class="error-box">ERROR: {_html.escape(str(entry["error"]))}</div>\n'
            # 部分错误可能仍有 round1
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

            # Round 1
            r1 = entry.get("round1_analysis", "")
            r1_html = _md_to_html(r1) if r1 else "<em>(empty)</em>"
            h += f'<div class="debug-box"><h4>Round 1 分析 {_usage_badge(entry.get("round1_usage"))}</h4>'
            h += f'<div class="analysis">{r1_html}</div></div>\n'

            # Round 2
            r2 = entry.get("round2_clean") or entry.get("round2_raw", "")
            r2_html = _format_json(r2)
            h += f'<div class="debug-box"><h4>Round 2 结构化输出 {_usage_badge(entry.get("round2_usage"))}</h4>'
            h += f'<pre>{r2_html}</pre></div>\n'

            h += '</div>\n'  # debug-grid

            # Normalization log
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

        # Token bar
        r1u = entry.get("round1_usage", {})
        r2u = entry.get("round2_usage", {})
        paper_total = (r1u.get("input_tokens", 0) + r1u.get("output_tokens", 0)
                       + r2u.get("input_tokens", 0) + r2u.get("output_tokens", 0))
        if paper_total:
            h += f'<div class="token-bar">Token: {paper_total:,} (R1: {r1u.get("input_tokens",0):,}+{r1u.get("output_tokens",0):,} | R2: {r2u.get("input_tokens",0):,}+{r2u.get("output_tokens",0):,})</div>\n'

        h += '</div>\n'  # card

    if errors:
        h += f'<div class="card"><div class="error-box">⚠ {errors} 篇论文处理出错</div></div>\n'

    h += '</body>\n</html>'

    with open(output_path, "w", encoding="utf-8") as f:
        f.write(h)
    print(f"Report: {output_path}")


def main():
    parser = argparse.ArgumentParser(description="生成生产运行 HTML 报告")
    parser.add_argument("output_dir", help="生产输出目录 (如 outputs/production_20260530_024222)")
    parser.add_argument("--debug-json", default=None, help="debug JSON 路径 (默认: <output_dir>/production_results_debug.json)")
    args = parser.parse_args()

    out_dir = os.path.join(PROJECT_ROOT, args.output_dir) if not os.path.isabs(args.output_dir) else args.output_dir

    if args.debug_json:
        debug_path = args.debug_json
    else:
        debug_path = os.path.join(out_dir, "production_results_debug.json")

    if not os.path.exists(debug_path):
        print(f"未找到 debug JSON: {debug_path}", file=sys.stderr)
        sys.exit(1)

    output_path = os.path.join(out_dir, "production_report.html")
    generate_report(debug_path, output_path)


if __name__ == "__main__":
    main()
