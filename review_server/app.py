import os
import sqlite3
import json
import requests
from flask import Flask, render_template, request, jsonify, redirect
from dotenv import load_dotenv

load_dotenv()


PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _load_gs_review_data():
    """Load GS_50 reference data for the gs_review page. Ported from build_review_html.py."""
    gs_path = os.path.join(PROJECT_ROOT, "data", "raw", "GS_50.tsv")
    trrust_path = os.path.join(PROJECT_ROOT, "data", "raw", "trrust_rawdata.human.tsv")
    alias_path = os.path.join(PROJECT_ROOT, "data", "curated", "gene_alias_index.json")
    abstracts_path = os.path.join(PROJECT_ROOT, "data", "interim", "gs50_abstracts.json")

    # --- load GS_50.tsv: PMID order + TF->Target pairs ---
    pmids_unique = []
    seen = set()
    gs50_pairs = {}
    with open(gs_path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            parts = line.split("\t")
            if len(parts) < 3:
                continue
            pmid = parts[0].strip()
            tf = parts[1].strip().upper()
            target = parts[2].strip().upper()
            if pmid not in seen:
                seen.add(pmid)
                pmids_unique.append(pmid)
            gs50_pairs.setdefault(pmid, []).append((tf, target))

    # --- load TRRUST: {pmid: {(tf, target): direction}} ---
    trrust = {}
    if os.path.exists(trrust_path):
        with open(trrust_path, encoding="utf-8") as f:
            for line in f:
                parts = line.strip().split("\t")
                if len(parts) < 4:
                    continue
                tf = parts[0].strip().upper()
                target = parts[1].strip().upper()
                direction = parts[2].strip()
                for pmid in parts[3].split(";"):
                    pmid = pmid.strip()
                    if pmid:
                        trrust.setdefault(pmid, {})[(tf, target)] = direction

    # --- load alias reverse index: {symbol: [alias, ...]} ---
    alias_reverse = {}
    if os.path.exists(alias_path):
        with open(alias_path, encoding="utf-8") as f:
            index = json.load(f)
        for alias_key, candidates in index.get("aliases", {}).items():
            for c in candidates:
                sym = c["symbol"].strip().upper()
                alias_reverse.setdefault(sym, set()).add(alias_key.upper())
        for sym in alias_reverse:
            alias_reverse[sym].discard(sym)
        alias_reverse = {k: sorted(v) for k, v in alias_reverse.items()}

    # --- load abstracts ---
    abstracts = {}
    if os.path.exists(abstracts_path):
        with open(abstracts_path, encoding="utf-8") as f:
            abstracts = json.load(f)

    # --- build reference data ---
    ref_pairs = {}
    abstracts_out = {}
    pmid_summaries = {}
    for pmid in pmids_unique:
        pairs = []
        for tf, target in gs50_pairs.get(pmid, []):
            direction = trrust.get(pmid, {}).get((tf, target), "")
            tf_aliases = alias_reverse.get(tf, [])
            target_aliases = alias_reverse.get(target, [])
            pairs.append({
                "tf": tf, "target": target, "direction": direction,
                "tf_aliases": tf_aliases, "target_aliases": target_aliases,
            })
        ref_pairs[pmid] = pairs

        tfs = sorted(set(p['tf'] for p in pairs))
        targets = sorted(set(p['target'] for p in pairs))
        pmid_summaries[pmid] = {"tfs": tfs, "targets": targets, "pair_count": len(pairs)}

        abstract = abstracts.get(pmid, "")
        if isinstance(abstract, dict):
            abstract = "\n\n".join(f"[{k}] {v}" for k, v in abstract.items())
        abstracts_out[pmid] = abstract

    return {"pmids": pmids_unique, "ref_pairs": ref_pairs, "abstracts": abstracts_out, "pmid_summaries": pmid_summaries}


def create_app():
    app = Flask(__name__, template_folder='templates', static_folder='static')
    db_path = os.environ.get('ANNOTATION_DB', os.path.join(os.path.dirname(os.path.abspath(__file__)), 'data', 'annotations.db'))

    # Ensure DB and table exist
    conn = sqlite3.connect(db_path, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    cur.execute('''
    CREATE TABLE IF NOT EXISTS annotations (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        pubmed_id TEXT NOT NULL,
        tf_input TEXT NOT NULL,
        tf_standard TEXT,
        tf_uniprot TEXT,
        gene_input TEXT NOT NULL,
        gene_ensg TEXT,
        cellline TEXT,
        assay TEXT,
        complex TEXT,
        created_at TEXT DEFAULT (datetime('now'))
    )
    ''')
    conn.commit()

    # --- gs_review table ---
    cur.execute('''
    CREATE TABLE IF NOT EXISTS gs_review_entries (
        pubmed_id TEXT PRIMARY KEY,
        state_json TEXT NOT NULL DEFAULT '{}',
        updated_at TEXT DEFAULT (datetime('now'))
    )
    ''')
    conn.commit()

    # Load GS_50 reference data once at startup
    gs_data = _load_gs_review_data()

    # Assay options (predefined) - tag, english description, chinese name
    ASSAY_OPTIONS = [
        {'tag':'Luciferase','en':'Luciferase Reporter Assay (incl. Dual-Luciferase)','cn':'荧光素酶报告基因检测','category':'Expression/Reporter'},
        {'tag':'RT-PCR','en':'Quantitative Real-Time PCR (qRT-PCR / RT-PCR)','cn':'实时荧光定量PCR','category':'Expression/Reporter'},
        {'tag':'RNA-seq','en':'RNA sequencing (RNA-seq)','cn':'RNA测序','category':'Expression/Reporter'},
        {'tag':'Microarray','en':'DNA / RNA Microarray','cn':'基因芯片','category':'Expression/Reporter'},
        {'tag':'NB','en':'Northern Blot','cn':'核糖核酸印迹','category':'Expression/Reporter'},
        {'tag':'RPA','en':'RNase Protection Assay','cn':'RNase保护实验','category':'Expression/Reporter'},

        {'tag':'ChIP','en':'Chromatin Immunoprecipitation (incl. ChIP-seq, ChIP-chip, ChIPmentation)','cn':'染色质免疫共沉淀','category':'DNA/TF Binding'},
        {'tag':'CUT&RUN','en':'Cleavage Under Targets & Release Using Nuclease (CUT&RUN)','cn':'CUT&RUN','category':'DNA/TF Binding'},
        {'tag':'CUT&Tag','en':'Cleavage Under Targets & Tagmentation (CUT&Tag)','cn':'CUT&Tag','category':'DNA/TF Binding'},
        {'tag':'EMSA','en':'Electrophoretic Mobility Shift Assay (incl. Supershift)','cn':'电泳迁移率变动分析','category':'DNA/TF Binding'},
        {'tag':'DNase-seq','en':'DNase I hypersensitive sites sequencing','cn':'DNase-seq','category':'DNA/TF Binding'},
        {'tag':'ATAC-seq','en':'Assay for Transposase-Accessible Chromatin using sequencing','cn':'ATAC-seq','category':'DNA/TF Binding'},
        {'tag':'DAPA','en':'DNA Affinity Purification Assay','cn':'DNA亲和纯化测定','category':'DNA/TF Binding'},
        {'tag':'Footprinting','en':'DNase I Footprinting','cn':'DNase I 足迹实验','category':'DNA/TF Binding'},

        {'tag':'WB','en':'Western Blot','cn':'蛋白质印迹','category':'Protein/Interaction'},
        {'tag':'Co-IP','en':'Co-Immunoprecipitation / IP','cn':'免疫共沉淀 / 免疫沉淀','category':'Protein/Interaction'},
        {'tag':'Pull-down','en':'Protein Pull-down Assay','cn':'蛋白质体外拉下实验','category':'Protein/Interaction'},
        {'tag':'Mass_spec','en':'Mass Spectrometry (LC-MS/MS)','cn':'质谱(LC-MS/MS)','category':'Protein/Interaction'},
        {'tag':'PLA','en':'Proximity Ligation Assay','cn':'近距离连接测定(PLA)','category':'Protein/Interaction'},
        {'tag':'IHC','en':'Immunohistochemistry','cn':'免疫组织化学','category':'Protein/Interaction'},
        {'tag':'IF','en':'Immunofluorescence','cn':'免疫荧光','category':'Protein/Interaction'},

        {'tag':'RNAi_KD','en':'RNA Interference / Knockdown (siRNA/shRNA)','cn':'RNA干扰 / 基因敲低','category':'Perturbation/Genetic'},
        {'tag':'siRNA','en':'siRNA knockdown','cn':'siRNA敲低','category':'Perturbation/Genetic'},
        {'tag':'CRISPR_KO','en':'CRISPR/Cas9 Knockout','cn':'CRISPR基因敲除','category':'Perturbation/Genetic'},
        {'tag':'CRISPRi','en':'CRISPR interference (CRISPRi)','cn':'CRISPR抑制','category':'Perturbation/Genetic'},
        {'tag':'CRISPRa','en':'CRISPR activation (CRISPRa)','cn':'CRISPR激活','category':'Perturbation/Genetic'},
        {'tag':'OE','en':'Overexpression','cn':'基因过表达','category':'Perturbation/Genetic'},
        {'tag':'Mutation','en':'Site-directed Mutagenesis','cn':'位点定向突变','category':'Perturbation/Genetic'},

        {'tag':'4C-seq','en':'Circular Chromosome Conformation Capture','cn':'染色体构象捕获测定','category':'Genomic/Conformation'},
        {'tag':'Hi-C','en':'Hi-C (Chromosome conformation capture genome-wide)','cn':'Hi-C','category':'Genomic/Conformation'},

        {'tag':'Flow_Cytometry','en':'Flow Cytometry','cn':'流式细胞术','category':'Cell-based'},
        {'tag':'Cell_viability','en':'Cell viability / proliferation assays (MTT, CCK-8, EdU)','cn':'细胞活性/增殖（MTT/CCK-8/EdU）','category':'Cell-based'},
        {'tag':'Migration','en':'Cell migration / invasion assays (Transwell, wound healing)','cn':'细胞迁移/侵袭实验','category':'Cell-based'},
        {'tag':'Patch_clamp','en':'Patch clamp / electrophysiology','cn':'膜片钳/电生理','category':'Cell-based'}
        ,{'tag':'Literature','en':'Literature citation (reported in paper)','cn':'文献引用','category':'citation'}
    ]
    # group by category preserving order
    grouped_assays = {}
    order = []
    for a in ASSAY_OPTIONS:
        cat = a.get('category','Other')
        if cat not in grouped_assays:
            grouped_assays[cat] = []
            order.append(cat)
        grouped_assays[cat].append(a)

    @app.route('/')
    def index():
        return redirect('/gs_review')

    @app.route('/api/search_protein')
    def search_protein():
        q = request.args.get('q', '').strip()
        if not q:
            return jsonify([])
        try:
            params = {
                'query': f'{q} AND organism_id:9606',
                'format': 'json',
                'size': 25,
                'fields': 'accession,protein_name,genes,organism_name'
            }
            r = requests.get('https://rest.uniprot.org/uniprotkb/search', params=params, timeout=10)
            results = []
            if r.ok:
                data = r.json()
                for item in data.get('results', []):
                    acc = item.get('primaryAccession') or item.get('accession') or item.get('uniProtkbId')
                    prot_desc = item.get('proteinDescription', {})
                    name = ''
                    if 'recommendedName' in prot_desc:
                        rn = prot_desc['recommendedName']
                        fn = rn.get('fullName')
                        if isinstance(fn, dict):
                            name = fn.get('value', '')
                        else:
                            name = fn or ''
                    genes = []
                    for g in item.get('genes', []):
                        if 'geneName' in g and 'value' in g['geneName']:
                            genes.append(g['geneName']['value'])
                    results.append({'id': acc, 'name': name, 'genes': genes})

            # If no results, fall back to MyGene.info simple search
            if not results:
                mg = requests.get('https://mygene.info/v3/query', params={'q': q, 'species': 'human', 'size': 10}, timeout=5)
                if mg.ok:
                    for hit in mg.json().get('hits', []):
                        gene = hit.get('symbol') or hit.get('name')
                        ensembl = None
                        if 'ensembl' in hit:
                            e = hit['ensembl']
                            if isinstance(e, list):
                                ensembl = e[0].get('gene')
                            elif isinstance(e, dict):
                                ensembl = e.get('gene')
                        results.append({'id': hit.get('_id'), 'name': gene, 'ensembl': ensembl})

            return jsonify(results)
        except Exception as e:
            return jsonify({'error': str(e)}), 500

    @app.route('/api/search_gene')
    def search_gene():
        q = request.args.get('q', '').strip()
        if not q:
            return jsonify([])
        try:
            r = requests.get('https://mygene.info/v3/query', params={'q': q, 'species': 'human', 'fields': 'ensembl.gene,symbol,name', 'size': 20}, timeout=8)
            hits = []
            if r.ok:
                for h in r.json().get('hits', []):
                    ensg = None
                    if 'ensembl' in h:
                        e = h['ensembl']
                        if isinstance(e, list):
                            ensg = e[0].get('gene')
                        elif isinstance(e, dict):
                            ensg = e.get('gene')
                    hits.append({'query_symbol': h.get('symbol'), 'name': h.get('name'), 'ensg': ensg})
            return jsonify(hits)
        except Exception as e:
            return jsonify({'error': str(e)}), 500

    @app.route('/api/export_csv')
    def export_csv():
        import io, csv
        cur = conn.cursor()
        cur.execute('SELECT id, pubmed_id, tf_input, tf_standard, tf_uniprot, gene_input, gene_ensg, cellline, assay, complex, created_at FROM annotations ORDER BY id')
        rows = cur.fetchall()
        si = io.StringIO()
        writer = csv.writer(si)
        header = ['id','pubmed_id','tf_input','tf_standard','tf_uniprot','gene_input','gene_ensg','cellline','assay','complex','created_at']
        writer.writerow(header)
        for r in rows:
            assay_field = r['assay']
            try:
                assay_parsed = json.loads(assay_field) if assay_field else ''
                if isinstance(assay_parsed, list):
                    assay_out = ';'.join(assay_parsed)
                else:
                    assay_out = assay_parsed
            except Exception:
                assay_out = assay_field
            row = [r['id'], r['pubmed_id'], r['tf_input'], r['tf_standard'], r['tf_uniprot'], r['gene_input'], r['gene_ensg'], r['cellline'], assay_out, r['complex'], r['created_at']]
            writer.writerow(row)
        output = si.getvalue()
        return app.response_class(output, mimetype='text/csv', headers={'Content-Disposition':'attachment;filename=annotations.csv'})

    @app.route('/api/save_annotation', methods=['POST'])
    def save_annotation():
        data = request.json or {}
        try:
            cur = conn.cursor()
            # store assay as JSON string if it's a list
            assay_val = data.get('assay', '')
            if isinstance(assay_val, list):
                assay_store = json.dumps(assay_val, ensure_ascii=False)
            else:
                assay_store = assay_val

            cur.execute('''
                INSERT INTO annotations (pubmed_id, tf_input, tf_standard, tf_uniprot, gene_input, gene_ensg, cellline, assay, complex)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ''', (
                data.get('pubmed_id', ''),
                data.get('tf_input', ''),
                data.get('tf_standard', ''),
                data.get('tf_uniprot', ''),
                data.get('gene_input', ''),
                data.get('gene_ensg', ''),
                data.get('cellline', ''),
                assay_store,
                data.get('complex', '')
            ))
            conn.commit()
            return jsonify({'ok': True, 'id': cur.lastrowid})
        except Exception as e:
            return jsonify({'error': str(e)}), 500

    @app.route('/api/delete_annotation/<int:anno_id>', methods=['DELETE'])
    def delete_annotation(anno_id):
        try:
            cur = conn.cursor()
            cur.execute('DELETE FROM annotations WHERE id = ?', (anno_id,))
            conn.commit()
            return jsonify({'ok': True})
        except Exception as e:
            return jsonify({'error': str(e)}), 500

    @app.route('/api/annotations')
    def list_annotations():
        try:
            cur = conn.cursor()
            cur.execute("SELECT * FROM annotations ORDER BY datetime(created_at) DESC LIMIT 200")
            rows = cur.fetchall()
            items = []
            for r in rows:
                # try parse assay JSON back to list if stored as JSON
                assay_field = r['assay']
                try:
                    assay_parsed = json.loads(assay_field) if assay_field else []
                except Exception:
                    assay_parsed = assay_field

                items.append({
                    'id': r['id'],
                    'pubmed_id': r['pubmed_id'],
                    'tf_input': r['tf_input'],
                    'tf_standard': r['tf_standard'],
                    'tf_uniprot': r['tf_uniprot'],
                    'gene_input': r['gene_input'],
                    'gene_ensg': r['gene_ensg'],
                    'cellline': r['cellline'],
                    'assay': assay_parsed,
                    'complex': r['complex'],
                    'created_at': r['created_at']
                })
            return jsonify(items)
        except Exception as e:
            return jsonify({'error': str(e)}), 500

    # ---- gs_review routes ----

    @app.route('/gs_review')
    def gs_review_page():
        return render_template('gs_review.html',
                              gs_data=gs_data,
                              pmid_summaries=gs_data['pmid_summaries'],
                              assay_options=ASSAY_OPTIONS,
                              grouped_assays=grouped_assays,
                              assay_categories=order)

    @app.route('/api/gs_review/save', methods=['POST'])
    def gs_review_save():
        data = request.json or {}
        pmid = data.get('pubmed_id', '').strip()
        state = data.get('state')
        if not pmid:
            return jsonify({'error': 'missing pubmed_id'}), 400
        try:
            cur = conn.cursor()
            cur.execute('''
                INSERT OR REPLACE INTO gs_review_entries (pubmed_id, state_json, updated_at)
                VALUES (?, ?, datetime('now'))
            ''', (pmid, json.dumps(state, ensure_ascii=False)))
            conn.commit()
            return jsonify({'ok': True})
        except Exception as e:
            return jsonify({'error': str(e)}), 500

    @app.route('/api/gs_review/load')
    def gs_review_load():
        try:
            cur = conn.cursor()
            cur.execute('SELECT pubmed_id, state_json, updated_at FROM gs_review_entries')
            rows = cur.fetchall()
            states = {}
            for r in rows:
                try:
                    states[r['pubmed_id']] = json.loads(r['state_json'])
                except Exception:
                    states[r['pubmed_id']] = {}
            return jsonify({'states': states, 'total': len(gs_data['pmids'])})
        except Exception as e:
            return jsonify({'error': str(e)}), 500

    @app.route('/api/gs_review/progress')
    def gs_review_progress():
        try:
            cur = conn.cursor()
            cur.execute("SELECT pubmed_id, state_json FROM gs_review_entries")
            rows = cur.fetchall()
            done = 0
            for r in rows:
                try:
                    s = json.loads(r['state_json'])
                    if s.get('r'):
                        done += 1
                except Exception:
                    pass
            return jsonify({'total': len(gs_data['pmids']), 'done': done})
        except Exception as e:
            return jsonify({'error': str(e)}), 500

    @app.route('/api/gs_review/export/tsv')
    def gs_review_export_tsv():
        try:
            cur = conn.cursor()
            cur.execute('SELECT pubmed_id, state_json FROM gs_review_entries')
            rows = cur.fetchall()
            lines = []
            for r in rows:
                pmid = r['pubmed_id']
                try:
                    s = json.loads(r['state_json'])
                except Exception:
                    s = {}
                pairs = s.get('p', [])
                for pair in pairs:
                    assay = pair.get('assay', [])
                    if isinstance(assay, list):
                        assay_str = ';'.join(assay)
                    else:
                        assay_str = str(assay) if assay else ''
                    lines.append('\t'.join([
                        pmid,
                        pair.get('tf', '') or pair.get('tf_input', '') or pair.get('f', ''),
                        pair.get('gene', '') or pair.get('gene_input', '') or pair.get('t', ''),
                        pair.get('direction', '') or pair.get('d', ''),
                        pair.get('cellline', '') or pair.get('c', ''),
                        assay_str,
                        pair.get('complex', '') or '',
                    ]))
            import io
            output = io.StringIO()
            output.write('PMID\tTF\tTarget\tDirection\tCellLine\tAssay\tComplex\n')
            output.write('\n'.join(lines) + '\n')
            return app.response_class(output.getvalue(), mimetype='text/tab-separated-values',
                                      headers={'Content-Disposition': 'attachment;filename=gs_review_export.tsv'})
        except Exception as e:
            return jsonify({'error': str(e)}), 500

    return app


if __name__ == '__main__':
    app = create_app()
    app.run(host='127.0.0.1', port=5000)# only run in local
