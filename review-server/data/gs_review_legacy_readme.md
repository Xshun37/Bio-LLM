# Legacy GS Review Workflow

This directory is kept as historical provenance for the GS_50 manual review.
New annotation work should use `review-server`.

Important files:

- `final.tsv`: curated historical input used for migration.
- `normalize_export.py`: legacy normalization logic for the old TSV workflow.
- `review.html`: legacy manual review UI output.

To migrate `final.tsv` into the current annotation server schema, run:

```bash
python tools/standardize_gs_review.py \
  --output data/gs50_standardized.csv
```
