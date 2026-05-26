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
        r1 = "(round1.txt not found)"
    try:
        r2 = load_prompt("round2.txt", PROMPT_DIR)
    except FileNotFoundError:
        r2 = "(round2.txt not found)"
    return r1, r2


def _save_prompts(r1, r2):
    """Save prompts to files."""
    os.makedirs(PROMPT_DIR, exist_ok=True)
    with open(os.path.join(PROMPT_DIR, "round1.txt"), "w", encoding="utf-8") as f:
        f.write(r1)
    with open(os.path.join(PROMPT_DIR, "round2.txt"), "w", encoding="utf-8") as f:
        f.write(r2)
    return "✓ Prompts saved to config/prompts/"


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
        choices.append(f"{pmid} ({n} entries)")
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
        return f"⚠ No abstract available for PMID {pmid}. Run the pipeline first or paste an abstract below.", "", "", "", ""

    # Run analysis
    try:
        result = analyze_tf_interaction(
            abstract_text,
            debug=True,
            round1_prompt=r1_prompt,
            round2_prompt=r2_prompt,
        )
    except Exception as e:
        return f"❌ Error: {e}", "", "", "", ""

    # Format outputs
    r1_text = result.get("round1_analysis", "(not available)")
    r2_raw = result.get("round2_raw", "(not available)")

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
        parsed_text = "(no parsed result)"

    # GT comparison
    gt_comparison = ""
    gt_entries = gs_data.get(pmid, [])
    if gt_entries:
        gt_comparison += f"Gold Standard for PMID {pmid} ({len(gt_entries)} entries):\n\n"
        for tf, target, assay, cellline, ensg in gt_entries:
            gt_comparison += f"  {tf} → {target}  [{assay}]  [{cellline}]\n"

        if "result" in result and isinstance(result["result"], list):
            gt_comparison += "\nMatching:\n"
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
                    gt_comparison += f"  ? {llm_tf}→{llm_target} (New Found)\n"
            for i, (tf, target, assay, cellline, ensg) in enumerate(gt_norm):
                if i not in matched:
                    gt_comparison += f"  ✗ {tf}→{target} (Missed)\n"
    else:
        gt_comparison = f"No gold standard entries for PMID {pmid}"

    return r1_text, r2_raw, parsed_text, tokens, gt_comparison


def build_ui():
    """Build and return the Gradio interface."""
    r1_default, r2_default = _load_prompts()
    gs_data = _load_gs()
    pmid_choices = _get_pmid_choices(gs_data)

    with gr.Blocks(
        title="Bio-LLM Prompt Debugger",
        theme=gr.themes.Soft(),
    ) as demo:
        gr.Markdown("# Bio-LLM Prompt Debugger\nEdit prompts and test against gold standard abstracts.")

        with gr.Row():
            # --- Left: Prompt editing ---
            with gr.Column(scale=1):
                gr.Markdown("## Prompts")
                r1_box = gr.Textbox(
                    value=r1_default,
                    label="Round 1 Prompt (free-text analysis)",
                    lines=20,
                    max_lines=50,
                )
                r2_box = gr.Textbox(
                    value=r2_default,
                    label="Round 2 Prompt (structured JSON output)",
                    lines=15,
                    max_lines=40,
                )
                with gr.Row():
                    save_btn = gr.Button("💾 Save Prompts", variant="primary")
                    reload_btn = gr.Button("🔄 Reload from Files")
                save_status = gr.Textbox(label="Status", interactive=False, max_lines=1)

            # --- Right: Testing ---
            with gr.Column(scale=1):
                gr.Markdown("## Test")
                pmid_dropdown = gr.Dropdown(
                    choices=pmid_choices,
                    value=pmid_choices[0] if pmid_choices else None,
                    label="Select PMID",
                )
                abstract_override = gr.Textbox(
                    label="Or paste abstract text (overrides PMID selection)",
                    lines=5,
                    max_lines=15,
                )
                run_btn = gr.Button("🚀 Run Analysis", variant="primary")

                gr.Markdown("### Results")
                with gr.Tabs():
                    with gr.Tab("Round 1"):
                        r1_output = gr.Textbox(label="Round 1 Analysis", lines=15, max_lines=30)
                    with gr.Tab("Round 2 Raw"):
                        r2_output = gr.Textbox(label="Round 2 Raw Output", lines=10, max_lines=20)
                    with gr.Tab("Parsed JSON"):
                        parsed_output = gr.Textbox(label="Parsed Result", lines=10, max_lines=20)
                    with gr.Tab("GT Comparison"):
                        gt_output = gr.Textbox(label="Gold Standard Comparison", lines=15, max_lines=25)
                token_info = gr.Textbox(label="Token Usage", interactive=False, max_lines=3)

        # --- Event handlers ---
        def save(r1, r2):
            return _save_prompts(r1, r2)

        def reload():
            r1, r2 = _load_prompts()
            return r1, r2, "✓ Reloaded from config/prompts/"

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
        print(f"Warning: {e}")
        print("Set DASHSCOPE_API_KEY environment variable before running analysis.")

    demo = build_ui()
    demo.launch(server_name="0.0.0.0", server_port=7860)


if __name__ == "__main__":
    main()
