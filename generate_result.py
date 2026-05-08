import json
import pandas as pd
import os
import re # 确保导入 re

def generate_html_report():
    # 1. 加载 TRRUST 数据
    trrust_file = 'trrust_rawdata.human.tsv'
    trrust_df = pd.read_csv(trrust_file, sep='\t', header=None, 
                         names=['tf', 'target', 'direction', 'pmid'], dtype={'pmid': str})
    
    # 2. 加载 LLM 结果
    with open('analysis_results.json', 'r', encoding='utf-8') as f:
        llm_data = json.load(f)

    # 3. 读取原始摘要 (从文本文件中读取，以便在网页显示)
    # 假设你的 abstracts_for_test.txt 还在
    abstracts = {}
    if os.path.exists('abstracts_for_test.txt'):
        with open('abstracts_for_test.txt', 'r', encoding='utf-8') as f:
            content = f.read()
            matches = re.findall(r"PMID: (\d+).*?Abstract Content:\n(.*?)(?==|$)", content, re.DOTALL)
            abstracts = {p.strip(): a.strip() for p, a in matches}

    html_content = """
    <html>
    <head>
        <meta charset="UTF-8">
        <style>
            body { font-family: sans-serif; line-height: 1.6; margin: 20px; background: #f4f4f9; }
            .card { background: white; border-radius: 8px; box-shadow: 0 2px 5px rgba(0,0,0,0.1); margin-bottom: 30px; padding: 20px; }
            .pmid-header { border-bottom: 2px solid #333; padding-bottom: 10px; margin-bottom: 15px; display: flex; justify-content: space-between; }
            .content-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 20px; }
            .abstract-box { background: #fdfdfd; padding: 15px; border-left: 4px solid #007bff; font-style: italic; font-size: 0.9em; }
            table { width: 100%; border-collapse: collapse; margin-top: 10px; font-size: 0.85em; }
            th, td { border: 1px solid #ddd; padding: 8px; text-align: left; }
            th { background-color: #f2f2f2; }
            .status-ok { color: green; font-weight: bold; }
            .status-conflict { color: orange; font-weight: bold; }
            .status-new { color: blue; font-weight: bold; }
            .status-miss { color: red; font-weight: bold; }
            .evidence { color: #666; font-size: 0.8em; display: block; margin-top: 4px; }
        </style>
    </head>
    <body>
        <h1>TF-Target Extraction Analysis Report</h1>
    """

    for pmid, llm_results in llm_data.items():
        # 获取该 PMID 对应的 TRRUST 标注
        t_rows = trrust_df[trrust_df['pmid'] == pmid]
        
        html_content += f"""
        <div class="card">
            <div class="pmid-header">
                <span style="font-size: 1.2em; font-weight: bold;">PMID: {pmid}</span>
                <a href="https://pubmed.ncbi.nlm.nih.gov/{pmid}/" target="_blank">View on PubMed</a>
            </div>
            <div class="content-grid">
                <div class="abstract-box">
                    <strong>Abstract:</strong><br>
                    {abstracts.get(pmid, 'Abstract not found in text file.')}
                </div>
                <div>
                    <strong>Comparison Table:</strong>
                    <table>
                        <tr>
                            <th>TF -> Target</th>
                            <th>TRRUST</th>
                            <th>LLM Result</th>
                            <th>Status</th>
                        </tr>
        """
        
        # 简单比对逻辑
        llm_list = llm_results if isinstance(llm_results, list) else []
        
        # 建立一个 Set 方便查找
        trrust_pairs = {(r.tf.upper(), r.target.upper()): r.direction for r in t_rows.itertuples()}
        llm_pairs = {(str(i.get('tf','')).upper(), str(i.get('target','')).upper()): (i.get('direction'), i.get('evidence')) for i in llm_list}
        
        all_pairs = set(trrust_pairs.keys()) | set(llm_pairs.keys())
        
        for tf, target in all_pairs:
            t_dir = trrust_pairs.get((tf, target), "N/A")
            l_info = llm_pairs.get((tf, target))
            l_dir = l_info[0] if l_info else "N/A"
            evidence = l_info[1] if l_info else ""
            
            status_class = ""
            status_text = ""
            
            if t_dir != "N/A" and l_dir != "N/A":
                if t_dir.capitalize() == l_dir.capitalize():
                    status_class, status_text = "status-ok", "✅ Consistent"
                else:
                    status_class, status_text = "status-conflict", "⚠️ Conflict"
            elif t_dir != "N/A":
                status_class, status_text = "status-miss", "❌ Missed"
            else:
                status_class, status_text = "status-new", "✨ New Found"
            
            html_content += f"""
                <tr>
                    <td>{tf} -> {target}</td>
                    <td>{t_dir}</td>
                    <td>{l_dir}</td>
                    <td class="{status_class}">{status_text}</td>
                </tr>
            """
            if evidence:
                html_content += f"<tr><td colspan='4' class='evidence'><strong>Evidence:</strong> {evidence}</td></tr>"

        html_content += """
                    </table>
                </div>
            </div>
        </div>
        """

    html_content += "</body></html>"
    
    with open('report.html', 'w', encoding='utf-8') as f:
        f.write(html_content)
    print("HTML Report generated: report.html")

if __name__ == "__main__":
    generate_html_report()