# Review Server

GS_50 Gold Standard review + manual annotation server. Flask + SQLite.

## Structure

```
review_server/
├── app.py              # Flask app (all routes, data loading, DB)
├── requirements.txt
├── templates/
│   ├── gs_review.html  # GS Review page (sidebar + one-PMID-per-page)
│   ├── index.html      # Legacy annotation form (deprecated, use /gs_review)
│   └── ai.html         # AI audit page
├── static/
│   ├── gs_review.js    # GS Review frontend logic
│   ├── app.js          # Legacy form JS
│   └── ai.js           # AI audit JS
├── tools/
│   ├── standardize_gs_review.py  # Migration: final.tsv -> DB/CSV
│   └── build_ensg_map.py         # Build gene->ENSG mapping from HGNC
└── data/
    ├── annotations.db            # SQLite database (auto-created)
    ├── gs50_standardized.csv     # Standardized GS_50 output
    ├── final.tsv                 # Curated GS_50 input for migration
    └── gs_review_legacy_readme.md
```

## Run

```bash
cd review_server
pip install -r requirements.txt
python app.py
```

Open `http://127.0.0.1:5000/gs_review`

## Features

- **GS Review**: 50 PMID gold standard review with TRRUST reference data
  - Left sidebar with all PMIDs, color-coded by status
  - One-PMID-per-page with TF/Gene pairs table
  - TF search via UniProt API, Gene search via MyGene.info API
  - Categorized assay multi-select chips (35 options, 8 categories)
  - Complex field, notes, reviewed checkbox
  - Search modal overlay for large candidate display
  - Keyboard shortcuts (j/k/n/p/Enter/g)
  - Dual persistence: localStorage + SQLite

- **New Annotation**: Standalone annotation form (merged from old index.html)

- **AI Audit**: Heuristic + optional AI-powered audit at `/ai`

- **Export**: CSV at `/api/export_csv`, TSV at `/api/gs_review/export/tsv`

## API Endpoints

| Route | Method | Purpose |
|-------|--------|---------|
| `/gs_review` | GET | GS Review page |
| `/` | GET | Legacy annotation form |
| `/ai` | GET | AI audit page |
| `/api/search_protein` | GET | UniProt protein search |
| `/api/search_gene` | GET | MyGene gene/ENSG search |
| `/api/save_annotation` | POST | Save annotation to `annotations` table |
| `/api/delete_annotation/<id>` | DELETE | Delete annotation |
| `/api/annotations` | GET | List recent annotations |
| `/api/gs_review/save` | POST | Save PMID review state |
| `/api/gs_review/load` | GET | Load all saved review states |
| `/api/gs_review/progress` | GET | Done/total count |
| `/api/gs_review/export/tsv` | GET | Export reviews as TSV |
| `/api/export_csv` | GET | Export annotations as CSV |
| `/api/ai/audit` | POST | Run AI audit |
| `/api/ai/results` | GET | Get audit results |

## Migration

```bash
python tools/standardize_gs_review.py --output data/gs50_standardized.csv --db data/annotations.db
```
