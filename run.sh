#!/bin/bash
set -e
ROOT="$(cd "$(dirname "$0")" && pwd)"
rm -f "$ROOT/data/interim/abstracts_for_test.txt" "$ROOT/outputs/analysis_results.json" "$ROOT/outputs/report.html"
conda run --no-capture-output -n bio_llm snakemake -s "$ROOT/snakefile" -d "$ROOT" --unlock >/dev/null 2>&1 || true
conda run --no-capture-output -n bio_llm snakemake -s "$ROOT/snakefile" -d "$ROOT" -j16 --config sample_size="${1:-5}"
echo "Report generated: $ROOT/outputs/report.html"
echo "LLM results: $ROOT/outputs/analysis_results.json"

# Open report in browser
if command -v explorer.exe >/dev/null 2>&1; then
  if command -v wslpath >/dev/null 2>&1; then
    explorer.exe "$(wslpath -w "$ROOT/outputs/report.html")" >/dev/null 2>&1 &
  else
    explorer.exe "$ROOT/outputs/report.html" >/dev/null 2>&1 &
  fi
elif command -v xdg-open >/dev/null 2>&1; then
  xdg-open "$ROOT/outputs/report.html" >/dev/null 2>&1 &
fi
