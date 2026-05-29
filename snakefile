import collections
import collections.abc

for _attr in ("Iterable", "Mapping", "MutableMapping", "Sequence", "MutableSequence",
               "Set", "MutableSet", "MappingView", "KeysView", "ItemsView", "ValuesView"):
    if not hasattr(collections, _attr):
        setattr(collections, _attr, getattr(collections.abc, _attr, None))

configfile: "config/config.yaml"

OUTDIR = config.get("output_dir", "outputs")

rule all:
    input:
        f"{OUTDIR}/report.html"

rule analyze_papers:
    input:
        gold_standard="data/raw/finalresult.tsv"
    output:
        results=f"{OUTDIR}/analysis_results.json",
        debug=f"{OUTDIR}/analysis_results_debug.json"
    params:
        model=config.get("model", "qwen3.7-max-2026-05-20"),
        temperature=config.get("temperature", 0),
        workers=config.get("workers", 4),
        text_source=config.get("text_source", "fitz"),
        sample_flag="--sample-size " + str(config["sample_size"]) if config.get("sample_size", 48) != 48 else "",
        pmid_seed_flag="--pmid-seed " + str(config["pmid_seed"]) if config.get("pmid_seed") is not None else "",
        seed_flag="--seed " + str(config["seed"]) if config.get("seed") is not None else "",
    shell:
        "PYTHONPATH=src conda run --no-capture-output -n bio_llm python -m bio_llm.analysis"
        " --gold-standard {input.gold_standard}"
        " --text-source {params.text_source}"
        " --output {output.results}"
        " --model {params.model} --temperature {params.temperature}"
        " --workers {params.workers}"
        " {params.sample_flag}"
        " {params.pmid_seed_flag}"
        " {params.seed_flag}"
        " --debug"

rule generate_report:
    input:
        llm_json=f"{OUTDIR}/analysis_results.json",
        debug_json=f"{OUTDIR}/analysis_results_debug.json",
        gold_standard="data/raw/finalresult.tsv"
    output:
        f"{OUTDIR}/report.html"
    params:
        text_source=config.get("text_source", "fitz"),
    shell:
        "PYTHONPATH=src conda run --no-capture-output -n bio_llm python -m bio_llm.reporting"
        " --llm-json {input.llm_json} --text-source {params.text_source} --output {output}"
        " --debug-json {input.debug_json} --gold-standard {input.gold_standard}"


# ── 生产 pipeline ──

rule production:
    output:
        tsv=f"{OUTDIR}/production_results.tsv",
        json=f"{OUTDIR}/production_results.json",
    params:
        workers=config.get("workers", 4),
        input_dir=config.get("production_input", "data/raw/paper_for_produce"),
        seed_flag="--seed " + str(config["seed"]) if config.get("seed") is not None else "",
    shell:
        "conda run --no-capture-output -n bio_llm python scripts/run_production.py"
        " --input {params.input_dir}"
        " --output {output.tsv}"
        " --json-output {output.json}"
        " --workers {params.workers}"
        " {params.seed_flag}"
        " --skip-existing"
