#!/usr/bin/env python3
"""Bio-LLM Prompt Debugger — Gradio Web UI.

Interactive tool for editing LLM prompts and testing them against
gold standard abstracts. Launch with:

    PYTHONPATH=src python scripts/prompt_debugger.py
"""

import json
import os
import sys

import gradio as gr
import pandas as pd

# Ensure project root is on sys.path
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(PROJECT_ROOT, "src"))

from bio_llm.analysis import (
    analyze_tf_interaction,
    load_prompt,
    parse_test_file,
)
from bio_llm.evaluation import (
    load_gold_standard,
    classify_llm_entry,
    match_assays,
    match_cellline,
    fuzzy_gene_match,
)
from bio_llm import normalize_tf, normalize_target

PROMPT_DIR = os.path.join(PROJECT_ROOT, "config", "prompts")
GOLD_STANDARD_PATH = os.path.join(PROJECT_ROOT, "data", "raw", "finalresult.tsv")
ABSTRACTS_PATH = os.path.join(PROJECT_ROOT, "data", "interim", "abstracts_for_test.txt")


def _load_prompts():
    """Load current prompts from files."""
    try:
        r1 = load_prompt("round1.txt", PROMPT_DIR)
    except FileNotFoundError:
        r1 = "（未找到 round1.txt）"
    try:
        r2 = load_prompt("round2.txt", PROMPT_DIR)
    except FileNotFoundError:
        r2 = "（未找到 round2.txt）"
    return r1, r2


def _save_prompts(r1, r2):
    """Save prompts to files."""
    os.makedirs(PROMPT_DIR, exist_ok=True)
    with open(os.path.join(PROMPT_DIR, "round1.txt"), "w", encoding="utf-8") as f:
        f.write(r1)
    with open(os.path.join(PROMPT_DIR, "round2.txt"), "w", encoding="utf-8") as f:
        f.write(r2)
    return "✓ 提示词已保存到 config/prompts/"


def _load_abstracts():
    """Load available abstracts for PMID dropdown."""
    if not os.path.exists(ABSTRACTS_PATH):
        return {}
    tasks = parse_test_file(ABSTRACTS_PATH)
    return {t["pmid"]: t for t in tasks}


def _load_gs():
    """Load gold standard data."""
    return load_gold_standard(GOLD_STANDARD_PATH)


def _get_pmid_choices(gs_data):
    """Build PMID choices with counts."""
    choices = []
    for pmid in sorted(gs_data.keys(), key=int):
        n = len(gs_data[pmid])
        choices.append(f"{pmid} ({n} 条)")
    return choices


def _run_analysis(pmid_label, abstract_override, r1_prompt, r2_prompt):
    """Run LLM analysis and return results."""
    gs_data = _load_gs()
    abstracts = _load_abstracts()

    # Extract PMID from label
    pmid = pmid_label.split(" ")[0] if pmid_label else ""

    # Get abstract
    if abstract_override and abstract_override.strip():
        abstract_text = abstract_override.strip()
    elif pmid in abstracts:
        abstract_text = abstracts[pmid]["abstract"]
    else:
        return f"⚠ PMID {pmid} 无可用摘要。请先运行流水线或在下方粘贴摘要。", "", "", "", ""
    # Run analysis
    try:
        result = analyze_tf_interaction(
            abstract_text,
            debug=True,
            round1_prompt=r1_prompt,
            round2_prompt=r2_prompt,
        )
    except Exception as e:
        return f"❌ 错误: {e}", "", "", "", ""

    # Format outputs
    r1_text = result.get("round1_analysis", "（不可用）")
    r2_raw = result.get("round2_raw", "（不可用）")

    # Token info
    tokens = ""
    for key, label in [("round1_usage", "Round 1"), ("round2_usage", "Round 2")]:
        usage = result.get(key, {})
        if usage:
            tokens += f"{label}: in={usage.get('input_tokens', '?')} out={usage.get('output_tokens', '?')}\n"

    # Parsed results
    if "error" in result:
        parsed_text = f"ERROR: {result['error']}"
    elif "result" in result:
        parsed_text = json.dumps(result["result"], indent=2, ensure_ascii=False)
    elif "round2_clean" in result:
        parsed_text = result["round2_clean"]
    else:
        parsed_text = "（无解析结果）"

    # GT comparison
    gt_comparison = ""
    gt_entries = gs_data.get(pmid, [])
    if gt_entries:
        gt_comparison += f"PMID {pmid} 金标准（{len(gt_entries)} 条）：\n\n"
        for tf, target, assay, cellline, ensg in gt_entries:
            gt_comparison += f"  {tf} → {target}  [{assay}]  [{cellline}]\n"

        if "result" in result and isinstance(result["result"], list):
            gt_comparison += "\n匹配结果：\n"
            gt_norm = [
                (normalize_tf(tf), normalize_target(target), assay, cellline, ensg)
                for tf, target, assay, cellline, ensg in gt_entries
            ]
            matched = set()
            for item in result["result"]:
                if not isinstance(item, dict):
                    continue
                llm_tf = normalize_tf(str(item.get("TF", item.get("tf", ""))))
                llm_target = normalize_target(str(item.get("Target", item.get("target", ""))))
                llm_assay = str(item.get("assay", ""))
                llm_cellline = str(item.get("cellLine", item.get("cellline", "")))
                status, gt_idx = classify_llm_entry(llm_tf, llm_target, gt_norm)
                if gt_idx >= 0:
                    matched.add(gt_idx)
                    a_ok = "✓" if match_assays(llm_assay, gt_norm[gt_idx][2]) else "✗"
                    c_ok = "✓" if match_cellline(llm_cellline, gt_norm[gt_idx][3]) else "✗"
                    gt_comparison += f"  ✓ {llm_tf}→{llm_target} (Assay:{a_ok} CellLine:{c_ok})\n"
                else:
                    gt_comparison += f"  ? {llm_tf}→{llm_target}（新发现）\n"
            for i, (tf, target, assay, cellline, ensg) in enumerate(gt_norm):
                if i not in matched:
                    gt_comparison += f"  ✗ {tf}→{target}（遗漏）\n"
    else:
        gt_comparison = f"PMID {pmid} 无金标准条目"

    return r1_text, r2_raw, parsed_text, tokens, gt_comparison


def build_ui():
    """Build and return the Gradio interface."""
    r1_default, r2_default = _load_prompts()
    gs_data = _load_gs()
    pmid_choices = _get_pmid_choices(gs_data)

    with gr.Blocks(
        title="Bio-LLM 提示词调试器",
    ) as demo:
        gr.Markdown("# Bio-LLM 提示词调试器\n编辑提示词并在金标准摘要上测试效果。")

        with gr.Row():
            # --- Left: Prompt editing ---
            with gr.Column(scale=1):
                gr.Markdown("## 提示词")
                r1_box = gr.Textbox(
                    value=r1_default,
                    label="Round 1 提示词（自由文本分析）",
                    lines=20,
                    max_lines=50,
                )
                r2_box = gr.Textbox(
                    value=r2_default,
                    label="Round 2 提示词（结构化 JSON 输出）",
                    lines=15,
                    max_lines=40,
                )
                with gr.Row():
                    save_btn = gr.Button("💾 保存提示词", variant="primary")
                    reload_btn = gr.Button("🔄 从文件重新加载")
                save_status = gr.Textbox(label="状态", interactive=False, max_lines=1)

            # --- Right: Testing ---
            with gr.Column(scale=1):
                gr.Markdown("## 测试")
                pmid_dropdown = gr.Dropdown(
                    choices=pmid_choices,
                    value=pmid_choices[0] if pmid_choices else None,
                    label="选择 PMID",
                )
                abstract_override = gr.Textbox(
                    label="或粘贴摘要文本（覆盖上方 PMID 选择）",
                    lines=5,
                    max_lines=15,
                )
                run_btn = gr.Button("🚀 运行分析", variant="primary")

                gr.Markdown("### 结果")
                with gr.Tabs():
                    with gr.Tab("Round 1"):
                        r1_output = gr.Textbox(label="Round 1 分析", lines=15, max_lines=30)
                    with gr.Tab("Round 2 原始"):
                        r2_output = gr.Textbox(label="Round 2 原始输出", lines=10, max_lines=20)
                    with gr.Tab("解析 JSON"):
                        parsed_output = gr.Textbox(label="解析结果", lines=10, max_lines=20)
                    with gr.Tab("金标准对比"):
                        gt_output = gr.Textbox(label="金标准对比", lines=15, max_lines=25)
                token_info = gr.Textbox(label="Token 用量", interactive=False, max_lines=3)

        # --- Event handlers ---
        def save(r1, r2):
            return _save_prompts(r1, r2)

        def reload():
            r1, r2 = _load_prompts()
            return r1, r2, "✓ 已从 config/prompts/ 重新加载"

        def run(pmid_label, abstract, r1, r2):
            return _run_analysis(pmid_label, abstract, r1, r2)

        save_btn.click(save, [r1_box, r2_box], save_status)
        reload_btn.click(reload, outputs=[r1_box, r2_box, save_status])
        run_btn.click(
            run,
            [pmid_dropdown, abstract_override, r1_box, r2_box],
            [r1_output, r2_output, parsed_output, token_info, gt_output],
        )

    return demo


def main():
    # Initialize API client
    from bio_llm.analysis import init_client
    try:
        init_client()
    except ValueError as e:
        print(f"警告: {e}")
        print("请设置 DASHSCOPE_API_KEY 环境变量后再运行分析。")

    demo = build_ui()
    demo.launch(server_name="0.0.0.0", server_port=7860, theme=gr.themes.Soft())


if __name__ == "__main__":
    main()
