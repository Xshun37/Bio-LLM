import os
import re
import json
import dashscope
from dashscope import Generation

# --- 配置区 ---
dashscope.api_key = os.getenv("DASHSCOPE_API_KEY")
INPUT_FILE = "abstracts_for_test.txt"
OUTPUT_JSON = "analysis_results.json"
MODEL_NAME = 'qwen-max' 

def parse_test_file(file_path):
    """提取 txt 中的 PMID 和 Abstract"""
    if not os.path.exists(file_path):
        print(f"错误: 找不到输入文件 {file_path}")
        return []
    with open(file_path, 'r', encoding='utf-8') as f:
        content = f.read()
    pattern = r"PMID: (\d+).*?Abstract Content:\n(.*?)(?==|$)"
    matches = re.findall(pattern, content, re.DOTALL)
    return [{"pmid": p.strip(), "abstract": a.strip()} for p, a in matches]

def analyze_tf_interaction(abstract_text):
    """调用大模型并提取纯净 JSON"""
    prompt = f"""
    你是一位专业的生物信息学专家。请阅读以下摘要，并提取转录因子(TF)与靶基因(Target)的调控关系。
    
    要求：
    1. 仅提取文中具有明确实验证据的调控关系。
    2. 基因名必须使用标准的 Gene Symbol。
    3. 调控方向(direction)必须是: Activation, Repression, 或 Unknown。
    4. 必须输出 JSON 列表格式，严禁包含任何前言、解释文字或 Python 风格的注释。
    
    摘要文本：
    {abstract_text}
    """
    
    response = Generation.call(model=MODEL_NAME, prompt=prompt, result_format='message')
    
    if response.status_code == 200:
        content = response.output.choices[0]['message']['content']
        
        # 1. 尝试匹配 Markdown 代码块中的内容
        json_match = re.search(r'```json\s*(.*?)\s*```', content, re.DOTALL)
        if json_match:
            clean_json = json_match.group(1)
        else:
            # 2. 如果没代码块，尝试匹配最外层的方括号 [ ]
            bracket_match = re.search(r'\[.*\]', content, re.DOTALL)
            clean_json = bracket_match.group() if bracket_match else content
        
        # 3. 移除可能存在的 Python/配置文件风格注释（# 及其后的内容）
        clean_json = re.sub(r'#.*$', '', clean_json, flags=re.MULTILINE)
        
        try:
            return json.loads(clean_json)
        except Exception as e:
            # 如果解析依然失败，可能是因为模型返回了非 JSON 内容
            print(f"JSON 解析失败，返回原始结果。错误: {e}")
            return {"error": "parse_fail", "content": content}
    else:
        return {"error": f"API_Error: {response.code} - {response.message}"}

if __name__ == "__main__":
    tasks = parse_test_file(INPUT_FILE)
    if not tasks:
        print("未发现待处理任务。")
    else:
        results = {}
        print(f"开始分析 {len(tasks)} 条摘要...")
        for task in tasks:
            pmid = task['pmid']
            print(f"正在处理 PMID: {pmid} ...")
            results[pmid] = analyze_tf_interaction(task['abstract'])
        
        with open(OUTPUT_JSON, 'w', encoding='utf-8') as f:
            json.dump(results, f, ensure_ascii=False, indent=4)
        print(f"\n分析完成！结果已存至: {OUTPUT_JSON}")