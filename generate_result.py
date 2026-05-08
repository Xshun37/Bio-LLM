import argparse
import json
import os
import re

import pandas as pd

# ── 基因名标准化 ──────────────────────────────────────────────
# 常见转录因子的同义名映射表（关键词 → 标准 Gene Symbol）
_SYNONYM_MAP = {
    # AP-2 family
    "AP-2": "TFAP2A", "AP2": "TFAP2A", "AP-2ALPHA": "TFAP2A",
    "AP-2BETA": "TFAP2B", "AP-2GAMMA": "TFAP2C",
    # C/EBP family
    "C/EBPALPHA": "CEBPA", "C/EBP-ALPHA": "CEBPA", "C/EBP A": "CEBPA",
    "C/EBPBETA": "CEBPB", "C/EBP-BETA": "CEBPB", "C/EBP B": "CEBPB",
    "C/EBPDELTA": "CEBPD", "C/EBP-DELTA": "CEBPD", "C/EBP D": "CEBPD",
    "C/EBPGAMMA": "CEBPG", "C/EBP-EPSILON": "CEBPE", "C/EBPZETA": "CEBPZ",
    # NF-kB family
    "NF-KB": "NFKB1", "NFKB": "NFKB1", "NF-KAPPA-B": "NFKB1",
    "NF-KB1": "NFKB1", "NF-KB P50": "NFKB1", "P50": "NFKB1",
    "NF-KB P65": "RELA", "P65": "RELA", "NFKB3": "RELA",
    "NF-KB2": "NFKB2", "P52": "NFKB2",
    "RELB": "RELB",
    "C-REL": "REL",
    # STAT family
    "STAT3": "STAT3", "STAT1": "STAT1", "STAT5": "STAT5A",
    # p53
    "P53": "TP53", "TP53": "TP53",
    # MYC
    "C-MYC": "MYC", "CMYC": "MYC", "MYCC": "MYC",
    "N-MYC": "MYCN", "L-MYC": "MYCL",
    # AP-1
    "C-JUN": "JUN", "CJUN": "JUN",
    "C-FOS": "FOS", "CFOS": "FOS",
    # 其他常见别名
    "LIVER-ENRICHED ACTIVATOR PROTEIN": "CEBPB",  # LAP = C/EBPbeta isoform
    "LIVER-ENRICHED INHIBITORY PROTEIN": "CEBPB",  # LIP = C/EBPbeta isoform
    "LAP": "CEBPB", "LIP": "CEBPB",
    "ER-ALPHA": "ESR1", "ER-BETA": "ESR2",
    "PPAR-GAMMA": "PPARG", "PPAR-ALPHA": "PPARA",
    "HIF-1ALPHA": "HIF1A", "HIF-2ALPHA": "HIF2A",
    "SP1": "SP1", "SP3": "SP3",
    "EGR-1": "EGR1",
    "OCT-1": "POU2F1", "OCT-4": "POU5F1", "OCT4": "POU5F1",
    "SOX2": "SOX2",
    "NANOG": "NANOG",
    "KLF4": "KLF4",
    "GATA1": "GATA1", "GATA3": "GATA3",
    "TBET": "TBX21",
    "FOXP3": "FOXP3",
    "ROR-GAMMA-T": "RORC",
    "BCL-6": "BCL6",
    # Y-box / cold shock
    "YB-1": "YBX1", "YB1": "YBX1",
    # ETS family
    "TEL1": "ETV6", "TEL": "ETV6",
    "YAN": "ETS1",    # Drosophila Yan → human ETS1
    "POINTEDP2": "ETS1",  # Drosophila PointedP2 → human ETS1
    # KLF family
    "KLF8": "KLF8",
    # MBD family
    "MBD1": "MBD1", "MBD2": "MBD2", "MECP2": "MECP2",
    # USF
    "USF1": "USF1", "USF2": "USF2",
    # ATF
    "ATF4": "ATF4", "ATF6": "ATF6",
    # HDAC (not TFs themselves but often reported as regulators)
    "HDAC1": "HDAC1", "HDAC3": "HDAC3",
}

# 目标基因别名
_TARGET_SYNONYM_MAP = {
    "DPRL": "PRL",  # decidual prolactin = prolactin (same gene, alternative promoter)
    "PRL1": "PRL",
    "BCL-2": "BCL2", "BCL-XL": "BCL2L1",
    "CDKN1A": "CDKN1A", "P21": "CDKN1A",
    "CDKN2A": "CDKN2A", "P16": "CDKN2A",
    "GUSB": "GUSB", "BETA-GLUC": "GUSB", "BETA-GLUCURONIDASE": "GUSB",
    "MUC5B": "MUC5B", "MUC5AC": "MUC5AC",
    "VWF": "VWF", "VON WILLEBRAND FACTOR": "VWF",
    # 磷脂酶 / 环氧合酶
    "CPLA2": "PLA2G4A", "COX-2": "PTGS2", "COX2": "PTGS2",
    # S100 family
    "MRP14": "S100A9",
    # 血清蛋白酶抑制剂
    "SERPINE1": "SERPINE1", "PAI-1": "SERPINE1", "PAI1": "SERPINE1",
    "SERPBP1": "SERPBP1",
    # 其他
    "AGGF1": "AGGF1",
    "SIRT1": "SIRT1",
    "EPSTI1": "EPSTI1",
    "ALOX5": "ALOX5", "5-LIPOXYGENASE": "ALOX5",
    "APOM": "APOM",
    "SLC6A4": "SLC6A4",
    "DRD2": "DRD2",
    "VEGFA": "VEGFA",
    "CCNA2": "CCNA2",
    "CDH1": "CDH1",
}


def _normalize_name(raw_name: str, alias_map: dict) -> str:
    """对基因名做基础规范化：大写、查别名表、去符号。"""
    if not raw_name:
        return ""
    name = str(raw_name).strip().upper()
    # 去掉括号内的内容，如 "NF-KB (P50)"
    name = re.sub(r"\(.*\)", "", name).strip()
    # 第一轮：保留符号直接查表
    if name in alias_map:
        return alias_map[name]
    # 第二轮：去掉分隔符后查表
    stripped = re.sub(r"[\s/\-]+", "", name)
    if stripped in alias_map:
        return alias_map[stripped]
    return stripped


def normalize_tf(raw_name: str) -> str:
    """将 TF 名称映射到标准 Gene Symbol。"""
    return _normalize_name(raw_name, _SYNONYM_MAP)


def normalize_target(raw_name: str) -> str:
    """将靶基因名称映射到标准 Gene Symbol。"""
    return _normalize_name(raw_name, _TARGET_SYNONYM_MAP)


def parse_abstracts_file(abstracts_path):
    """返回 {pmid: {"abstract": str, "trrust_tf": str, "trrust_target": str, "trrust_dir": str}}"""
    if not os.path.exists(abstracts_path):
        return {}

    with open(abstracts_path, "r", encoding="utf-8") as f:
        content = f.read()

    blocks = re.split(r"={10,}", content)
    result = {}
    for block in blocks:
        pmid_m = re.search(r"PMID:\s*(\d+)", block)
        if not pmid_m:
            continue
        pmid = pmid_m.group(1).strip()

        abstract_m = re.search(r"Abstract:\s*-{3,}\s*(.*?)(?:\n(?=={10,})|\Z)", block, re.DOTALL)
        abstract_text = abstract_m.group(1).strip() if abstract_m else ""

        # 提取 TRRUST Standard: TF -> TARGET (Direction)
        trrust_m = re.search(
            r"TRRUST Standard:\s*(\S+)\s*->\s*(\S+)\s*\((\w+)\)", block
        )
        if trrust_m:
            result[pmid] = {
                "abstract": abstract_text,
                "trrust_tf": trrust_m.group(1).strip(),
                "trrust_target": trrust_m.group(2).strip(),
                "trrust_dir": trrust_m.group(3).strip(),
            }
        else:
            result[pmid] = {
                "abstract": abstract_text,
                "trrust_tf": "",
                "trrust_target": "",
                "trrust_dir": "",
            }

    return result


def load_trrust(trrust_path):
    return pd.read_csv(
        trrust_path,
        sep="	",
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


def _get_field(obj: dict, *keys: str, default: str = "") -> str:
    """从 LLM 输出字典中提取字段，兼容大小写变体。"""
    for key in keys:
        if key in obj:
            return str(obj[key])
        if key.lower() in obj:
            return str(obj[key.lower()])
        if key.upper() in obj:
            return str(obj[key.upper()])
    return default


def generate_html_report(llm_json, abstracts_file, output_file):
    with open(llm_json, "r", encoding="utf-8") as f:
        llm_data = json.load(f)

    abstracts = parse_abstracts_file(abstracts_file)

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
            .status-miss { color: red; font-weight: bold; }
            .evidence { color: #666; font-size: 0.8em; display: block; margin-top: 4px; }
            .conf-5 { background: #d4edda; }
            .conf-4 { background: #e6f3e6; }
            .conf-3 { background: #fff3cd; }
            .conf-2 { background: #ffe5cc; }
            .conf-1 { background: #f8d7da; }
        </style>
    </head>
    <body>
        <h1>TF-Target Extraction Analysis Report</h1>
    """

    for pmid, llm_results in llm_data.items():
        info = abstracts.get(str(pmid), {})
        gt_tf = normalize_tf(info.get("trrust_tf", ""))
        gt_target = normalize_target(info.get("trrust_target", ""))
        gt_dir = info.get("trrust_dir", "")

        html_content += f"""
        <div class="card">
            <div class="pmid-header">
                <span style="font-size: 1.2em; font-weight: bold;">PMID: {pmid}</span>
                <a href="https://pubmed.ncbi.nlm.nih.gov/{pmid}/" target="_blank">View on PubMed</a>
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
                            <th>TRRUST</th>
                            <th>LLM</th>
                            <th>Conf</th>
                            <th>Evidence</th>
                            <th>Status</th>
                        </tr>
        """

        llm_list = llm_results if isinstance(llm_results, list) else []
        error_message = format_error_result(llm_results)

        if error_message:
            html_content += f"<tr><td colspan='6' class='status-conflict'>{error_message}</td></tr>"
        elif not llm_list:
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
                l_tf = normalize_tf(_get_field(item, "tf", "TF"))
                l_target = normalize_target(_get_field(item, "target", "Target"))
                l_dir = _get_field(item, "direction", "Direction")
                confidence = _get_field(item, "confidence", "Confidence")
                evidence = _get_field(item, "evidence", "Evidence")

                tf_match = (l_tf == gt_tf)
                target_match = (l_target == gt_target)

                if tf_match and target_match:
                    if l_dir.lower() == gt_dir.lower():
                        status_class, status_text = "status-ok", "Consistent"
                    else:
                        status_class, status_text = "status-conflict", "Conflict"
                elif tf_match and not target_match:
                    status_class, status_text = "status-partial", "TF-Match"
                elif gt_tf:
                    status_class, status_text = "status-miss", "Mismatch"
                else:
                    status_class, status_text = "status-new", "New"

                conf_num = int(confidence) if confidence.isdigit() else 0
                conf_display = f'<span class="conf-{conf_num}">{confidence}</span>' if conf_num else "-"

                html_content += f"""
                    <tr>
                        <td>{l_tf} → {l_target}</td>
                        <td>{gt_dir}</td>
                        <td>{l_dir}</td>
                        <td>{conf_display}</td>
                        <td style="font-size:0.8em">{evidence}</td>
                        <td class="{status_class}">{status_text}</td>
                    </tr>
                """

        html_content += """
                    </table>
                </div>
            </div>
        </div>
        """

    html_content += "</body></html>"

    with open(output_file, "w", encoding="utf-8") as f:
        f.write(html_content)
    print(f"HTML report generated: {output_file}")


def main():
    parser = argparse.ArgumentParser(description="生成 TF-Target 提取结果对比报告。")
    parser.add_argument("--llm-json", default="analysis_results.json", help="LLM 输出 JSON 文件路径")
    parser.add_argument("--abstracts", default="abstracts_for_test.txt", help="包含摘要及标准答案的文本路径")
    parser.add_argument("--output", default="report.html", help="生成的 HTML 报告文件名")
    args = parser.parse_args()

    generate_html_report(args.llm_json, args.abstracts, args.output)


if __name__ == "__main__":
    main()
