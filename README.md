# Bio-LLM

一个面向小规模实验的生物文本抽取流水线项目。  
输入 TRRUST 数据，抽样 PubMed PMID，拉取摘要，用 LLM 提取 TF-target 关系，最后生成 HTML 对比报告。

## 项目结构

```text
Bio-LLM/
├── config/
│   └── config.yaml          # 运行参数
├── data/
│   ├── raw/                 # 原始输入数据
│   └── interim/             # 中间文件
├── outputs/                 # 最终输出
├── src/
│   └── bio_llm/
│       ├── abstracts.py     # 拉取 PubMed 摘要
│       ├── analysis.py      # 调用 LLM 抽取关系
│       └── reporting.py     # 生成 HTML 报告
├── requirements.txt
├── run.sh                   # 一键启动入口
└── snakefile                # Snakemake 工作流
```

## 流程

```text
data/raw/trrust_rawdata.human.tsv
-> data/interim/abstracts_for_test.txt
-> outputs/analysis_results.json
-> outputs/report.html
```

## 环境要求

- `conda`
- 一个名为 `bio_llm` 的 conda 环境
- [requirements.txt](/home/bioxs/Bioproduce/Bio-LLM/requirements.txt) 中的依赖
- DashScope API Key

推荐安装方式：

```bash
conda create -n bio_llm python=3.10 -y
conda activate bio_llm
pip install -r requirements.txt
```

设置 DashScope API Key：

```bash
export DASHSCOPE_API_KEY="your_api_key"
```

## 快速开始

进入项目目录后直接运行：

```bash
./run.sh
```

如果想修改抽样数量：

```bash
./run.sh 10
```

这会把 `sample_size=10` 传给整个流程。

## 配置文件

主配置文件是 [config/config.yaml](/home/bioxs/Bioproduce/Bio-LLM/config/config.yaml)。

当前主要参数：

- `sample_size`: 抽样条数
- `seed`: 随机种子
- `email`: NCBI / PubMed 请求邮箱
- `model`: DashScope 模型名
- `temperature`: LLM 温度
- `workers`: 并发数
- `ncbi_bypass_proxy`: 是否绕过代理访问 NCBI
- `ncbi_no_proxy_hosts`: 直连的 NCBI 域名列表

## 手动分步运行

如果不想跑整条流水线，可以逐步执行：

```bash
PYTHONPATH=src conda run --no-capture-output -n bio_llm python -m bio_llm.abstracts \
  --input data/raw/trrust_rawdata.human.tsv \
  --output data/interim/abstracts_for_test.txt
```

```bash
PYTHONPATH=src conda run --no-capture-output -n bio_llm python -m bio_llm.analysis \
  --input data/interim/abstracts_for_test.txt \
  --output outputs/analysis_results.json
```

```bash
PYTHONPATH=src conda run --no-capture-output -n bio_llm python -m bio_llm.reporting \
  --llm-json outputs/analysis_results.json \
  --abstracts data/interim/abstracts_for_test.txt \
  --output outputs/report.html
```

## 输出文件

- [data/interim/abstracts_for_test.txt](/home/bioxs/Bioproduce/Bio-LLM/data/interim/abstracts_for_test.txt)
  抽样后拉取到的摘要

- [outputs/analysis_results.json](/home/bioxs/Bioproduce/Bio-LLM/outputs/analysis_results.json)
  LLM 抽取结果

- [outputs/report.html](/home/bioxs/Bioproduce/Bio-LLM/outputs/report.html)
  最终 HTML 报告

## 代理 / VPN 说明

项目支持在拉取 PubMed 摘要时临时绕过环境代理。把 [config/config.yaml](/home/bioxs/Bioproduce/Bio-LLM/config/config.yaml) 改成：

```yaml
ncbi_bypass_proxy: true
ncbi_no_proxy_hosts: "eutils.ncbi.nlm.nih.gov,ncbi.nlm.nih.gov,pubmed.ncbi.nlm.nih.gov"
```

注意：

- 这只对基于 `HTTP_PROXY` / `HTTPS_PROXY` / `NO_PROXY` 的代理有效
- 如果你使用的是全局 TUN / 全局 VPN，需要在VPN客户端里做域名分流，把上面这些 NCBI 域名设为 `DIRECT`

## 常见问题

### 1. `run.sh` 没反应或看起来卡住

现在 `run.sh` 已经使用 `conda run --no-capture-output`，应该能实时看到 Snakemake 日志。  
如果还是没有输出，先确认：

- `bio_llm` 环境存在
- `snakemake` 已安装
- `DASHSCOPE_API_KEY` 已设置

### 2. 最后没有打开 HTML，而是打开了一个文件夹

在 WSL 下，脚本会优先把 Linux 路径转成 Windows 路径再交给 `explorer.exe`。  
如果仍然没有自动打开，可以手动打开：

```bash
explorer.exe "$(wslpath -w outputs/report.html)"
```

### 3. PubMed 获取失败

优先检查：

- `email` 是否配置正确
- 当前网络是否能直连 NCBI
- 是否需要开启 `ncbi_bypass_proxy`

## 说明

这个项目是实验性质的脚本化流水线，不是生产级工程。  
当前结构以“够清楚、够好跑、够容易改”为目标，没有刻意引入更重的工程层次。
