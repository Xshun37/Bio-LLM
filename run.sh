#!/bin/bash
set -e
rm -f abstracts_for_test.txt analysis_results.json report.html
conda run -n bio_llm snakemake -j16 --config sample_size="${1:-5}"
explorer.exe report.html 2>/dev/null &
