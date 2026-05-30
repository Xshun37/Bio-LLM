import collections
import collections.abc

for _attr in ("Iterable", "Mapping", "MutableMapping", "Sequence", "MutableSequence",
               "Set", "MutableSet", "MappingView", "KeysView", "ItemsView", "ValuesView"):
    if not hasattr(collections, _attr):
        setattr(collections, _attr, getattr(collections.abc, _attr, None))

configfile: "config/config.yaml"

OUTDIR = config.get("output_dir", "outputs")
MODE = config.get("mode", "gold_standard")

rule all:
    input:
        f"{OUTDIR}/report.html"

rule analyze_papers:
    output:
        results=f"{OUTDIR}/analysis_results.json",
        debug=f"{OUTDIR}/analysis_results_debug.json",
    params:
        model=config.get("model", "qwen3.7-max"),
        temperature=config.get("temperature", 0),
        workers=config.get("workers", 4),
        text_source=config.get("text_source", "fitz"),
        mode=MODE,
        # 金标准模式参数
        gold_standard=config.get("gold_standard", "data/raw/finalresult.tsv"),
        sample_flag="--sample-size " + str(config["sample_size"]) if config.get("sample_size") else "",
        pmid_seed_flag="--pmid-seed " + str(config["pmid_seed"]) if config.get("pmid_seed") and config.get("mode", "gold_standard") == "gold_standard" else "",
        # 生产模式参数
        production_input=config.get("production_input", "data/raw/paper_for_produce"),
        seed_flag="--seed " + str(config["seed"]) if config.get("seed") is not None else "",
    shell:
        "PYTHONPATH=src conda run --no-capture-output -n bio_llm python -m bio_llm.analysis"
        " --output {output.results}"
        " --model {params.model} --temperature {params.temperature}"
        " --workers {params.workers}"
        " {params.seed_flag}"
        " --debug"
        " $(if [ '{params.mode}' = 'production' ]; then"
        "   echo '--production-input {params.production_input} {params.sample_flag}';"
        " else"
        "   echo '--gold-standard {params.gold_standard} --text-source {params.text_source} {params.sample_flag} {params.pmid_seed_flag}';"
        " fi)"

rule generate_report:
    input:
        llm_json=f"{OUTDIR}/analysis_results.json",
        debug_json=f"{OUTDIR}/analysis_results_debug.json",
    output:
        f"{OUTDIR}/report.html",
    params:
        mode=MODE,
        text_source=config.get("text_source", "fitz"),
        gold_standard=config.get("gold_standard", "data/raw/finalresult.tsv"),
    shell:
        "PYTHONPATH=src conda run --no-capture-output -n bio_llm python -m bio_llm.reporting"
        " --llm-json {input.llm_json} --output {output}"
        " --debug-json {input.debug_json}"
        " --mode {params.mode}"
        " $(if [ '{params.mode}' = 'gold_standard' ]; then"
        "   echo '--gold-standard {params.gold_standard} --text-source {params.text_source}';"
        " fi)"
