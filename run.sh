#!/bin/bash
# Usage: ./run.sh [options]
#
# 金标准模式（默认）:
#   ./run.sh                              # 全量 48 篇
#   ./run.sh --sample-size 5              # 随机 5 篇
#   ./run.sh --sample-size 5 --pmid-seed 42
#
# 生产模式:
#   ./run.sh --production                 # 全部论文
#   ./run.sh --production --sample-size 5 # 前 5 篇
#   ./run.sh --production --workers 4     # 4 worker 并行
#
# 断点续传（绕过 snakemake，直接调 Python）:
#   ./run.sh --production --resume        # 自动找最新目录续传
#   ./run.sh --production --resume outputs/production_20260531_040226

set -e
ROOT="$(cd "$(dirname "$0")" && pwd)"

# 解析参数
PRODUCTION=false
SAMPLE_SIZE=""
PMID_SEED=""
WORKERS=""
INPUT_DIR=""
RESUME=""
RESUME_DIR=""

while [[ $# -gt 0 ]]; do
  case $1 in
    --production)
      PRODUCTION=true
      shift
      ;;
    --sample-size)
      SAMPLE_SIZE="$2"
      shift 2
      ;;
    --pmid-seed)
      PMID_SEED="$2"
      shift 2
      ;;
    --workers)
      WORKERS="$2"
      shift 2
      ;;
    --input)
      INPUT_DIR="$2"
      shift 2
      ;;
    --resume)
      RESUME=true
      if [[ -n "$2" && "$2" != --* && -d "$2" ]]; then
        RESUME_DIR="$2"
        shift 2
      else
        shift
      fi
      ;;
    *)
      if [ -z "$SAMPLE_SIZE" ]; then
        SAMPLE_SIZE="$1"
      fi
      shift
      ;;
  esac
done

# 读取 config.yaml
CONFIG="$ROOT/config/config.yaml"
MODEL=$(grep -E '^model:' "$CONFIG" 2>/dev/null | awk '{print $2}' | tr -d '"' || echo "qwen3.7-max")
TEMP=$(grep -E '^temperature:' "$CONFIG" 2>/dev/null | awk '{print $2}' || echo "0")
SEED=$(grep -E '^seed:' "$CONFIG" 2>/dev/null | awk '{print $2}' || echo "")
CFG_WORKERS=$(grep -E '^workers:' "$CONFIG" 2>/dev/null | awk '{print $2}' || echo "4")

# 使用 CLI 或 config 的 workers
W="${WORKERS:-$CFG_WORKERS}"

# ── 断点续传模式 ──
if [ -n "$RESUME" ]; then
  if [ -n "$RESUME_DIR" ]; then
    OUTDIR="$RESUME_DIR"
  else
    if [ "$PRODUCTION" = true ]; then
      OUTDIR=$(ls -td outputs/production_* 2>/dev/null | head -1)
    else
      OUTDIR=$(ls -td outputs/* 2>/dev/null | grep -v production | head -1)
    fi
    if [ -z "$OUTDIR" ]; then
      echo "错误: 未找到输出目录"
      exit 1
    fi
  fi

  echo "断点续传: $OUTDIR"
  echo ""

  # 直接调 Python（绕过 snakemake），analysis.py 自己处理续传逻辑
  ANALYSIS_ARGS="--output $OUTDIR/analysis_results.json"
  ANALYSIS_ARGS="$ANALYSIS_ARGS --model $MODEL --temperature $TEMP --workers $W"
  [ -n "$SEED" ] && ANALYSIS_ARGS="$ANALYSIS_ARGS --seed $SEED"
  ANALYSIS_ARGS="$ANALYSIS_ARGS --debug"

  if [ "$PRODUCTION" = true ]; then
    ANALYSIS_ARGS="$ANALYSIS_ARGS --production-input ${INPUT_DIR:-data/raw/paper_for_produce}"
    [ -n "$SAMPLE_SIZE" ] && ANALYSIS_ARGS="$ANALYSIS_ARGS --sample-size $SAMPLE_SIZE"
  else
    ANALYSIS_ARGS="$ANALYSIS_ARGS --gold-standard data/raw/finalresult.tsv --text-source fitz"
    [ -n "$SAMPLE_SIZE" ] && ANALYSIS_ARGS="$ANALYSIS_ARGS --sample-size $SAMPLE_SIZE"
    [ -n "$PMID_SEED" ] && ANALYSIS_ARGS="$ANALYSIS_ARGS --pmid-seed $PMID_SEED"
  fi

  PYTHONPATH="$ROOT/src" conda run --no-capture-output -n bio_llm \
    python -m bio_llm.analysis $ANALYSIS_ARGS

  # 生成报告
  REPORT_ARGS="--llm-json $OUTDIR/analysis_results.json --output $OUTDIR/report.html"
  REPORT_ARGS="$REPORT_ARGS --debug-json $OUTDIR/analysis_results_debug.json"

  if [ "$PRODUCTION" = true ]; then
    REPORT_ARGS="$REPORT_ARGS --mode production"
  else
    REPORT_ARGS="$REPORT_ARGS --mode gold_standard --gold-standard data/raw/finalresult.tsv --text-source fitz"
  fi

  PYTHONPATH="$ROOT/src" conda run --no-capture-output -n bio_llm \
    python -m bio_llm.reporting $REPORT_ARGS

  echo ""
  echo "Report: $ROOT/$OUTDIR/report.html"
  exit 0
fi

# ── 正常模式（通过 snakemake）──
SNAKE_ARGS=""

if [ "$PRODUCTION" = true ]; then
  OUTDIR="outputs/production_$(date +%Y%m%d_%H%M%S)"
  SNAKE_ARGS="--config mode=production output_dir=$OUTDIR"
  SNAKE_ARGS="$SNAKE_ARGS production_input=${INPUT_DIR:-data/raw/paper_for_produce}"
  if [ -n "$SAMPLE_SIZE" ]; then
    SNAKE_ARGS="$SNAKE_ARGS sample_size=$SAMPLE_SIZE"
  fi
  echo "Production 模式"
else
  OUTDIR="outputs/$(date +%Y%m%d_%H%M%S)"
  SNAKE_ARGS="--config mode=gold_standard output_dir=$OUTDIR"
  if [ -n "$SAMPLE_SIZE" ]; then
    SNAKE_ARGS="$SNAKE_ARGS sample_size=$SAMPLE_SIZE"
  fi
  if [ -n "$PMID_SEED" ]; then
    SNAKE_ARGS="$SNAKE_ARGS pmid_seed=$PMID_SEED"
  fi
  echo "Gold Standard 模式"
fi

if [ -n "$WORKERS" ]; then
  SNAKE_ARGS="$SNAKE_ARGS workers=$WORKERS"
fi

mkdir -p "$ROOT/$OUTDIR"

echo "输出目录: $OUTDIR"
echo ""

conda run --no-capture-output -n bio_llm snakemake -s "$ROOT/snakefile" -d "$ROOT" -j16 $SNAKE_ARGS

echo ""
echo "Report: $ROOT/$OUTDIR/report.html"
echo "LLM results: $ROOT/$OUTDIR/analysis_results.json"

# Open report in browser
if command -v explorer.exe >/dev/null 2>&1; then
  if command -v wslpath >/dev/null 2>&1; then
    explorer.exe "$(wslpath -w "$ROOT/$OUTDIR/report.html")" >/dev/null 2>&1 &
  else
    explorer.exe "$ROOT/$OUTDIR/report.html" >/dev/null 2>&1 &
  fi
elif command -v xdg-open >/dev/null 2>&1; then
  xdg-open "$ROOT/$OUTDIR/report.html" >/dev/null 2>&1 &
fi
