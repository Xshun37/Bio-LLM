# Bio-LLM

用于 TF-Target 调控关系提取的三项目工程集合。

## Projects

```text
Bio-LLM/
├── Bioextract/      # 主工程：从论文到 LLM 抽取、过滤、报告
├── batch-screen/    # 文献检索、摘要筛选、PDF 下载
└── review-server/   # 金标准文献人工审核与 TF-Target 标注
```

## Bioextract

主 pipeline，支持金标准评估和生产批处理。

```bash
cd Bioextract
cp config/config.example.yaml config/config.yaml
./run.sh --sample-size 5
./run.sh --production --input data/raw/paper_for_produce
```

后处理脚本支持独立输入输出，例如对子集 JSON 生成报告：

```bash
cd Bioextract
PYTHONPATH=src python -m bio_llm.reporting \
  --llm-json outputs/subset.json \
  --output outputs/subset_report.html \
  --mode production
```

## batch-screen

用于 PubMed 检索、LLM 摘要筛选和 PDF 下载。

```bash
cd batch-screen
cp config.example.yaml config.yaml
python batch_screen.py --limit-tfs 5
python download_pdfs.py
```

`data/` 和 `outputs/` 保留在工程内，便于复现实验过程。`config.yaml` 是本地配置，不提交；API key 优先从 `DASHSCOPE_API_KEY` 读取。

## review-server

用于阅读金标准文献、记录 TF-Target 对并导出 TSV。

```bash
cd review-server
pip install -r requirements.txt
python app.py
```

打开 `http://127.0.0.1:5000/gs_review`。

## Repository Notes

- 三个子项目互相独立，默认从各自目录运行。
- `data/` 和 `outputs/` 按当前整理目标保留，不做全局忽略。
- 本地 secret、缓存、虚拟环境和编译产物由 `.gitignore` 排除。
