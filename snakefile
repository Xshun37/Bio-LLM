import collections
import collections.abc

for _attr in ("Iterable", "Mapping", "MutableMapping", "Sequence", "MutableSequence",
               "Set", "MutableSet", "MappingView", "KeysView", "ItemsView", "ValuesView"):
    if not hasattr(collections, _attr):
        setattr(collections, _attr, getattr(collections.abc, _attr, None))

configfile: "config/config.yaml"

rule all:
    input:
        "outputs/report.html"

rule generate_abstracts:
    input:
        "data/raw/trrust_rawdata.human.tsv"
    output:
        "data/interim/abstracts_for_test.txt"
    params:
        sample_size=config.get("sample_size", 5),
        seed_str="--seed " + str(config["seed"]) if "seed" in config else "",
        email=config.get("email", "your_email@example.com"),
        bypass_proxy_flag="--bypass-proxy" if config.get("ncbi_bypass_proxy", False) else "",
        no_proxy_hosts=config.get(
            "ncbi_no_proxy_hosts",
            "eutils.ncbi.nlm.nih.gov,ncbi.nlm.nih.gov,pubmed.ncbi.nlm.nih.gov",
        )
    shell:
        "PYTHONPATH=src conda run --no-capture-output -n bio_llm python -m bio_llm.abstracts"
        " --input {input} --output {output}"
        " --sample-size {params.sample_size} {params.seed_str} --email '{params.email}'"
        " {params.bypass_proxy_flag} --ncbi-no-proxy-hosts '{params.no_proxy_hosts}'"

rule analyze_abstracts:
    input:
        "data/interim/abstracts_for_test.txt"
    output:
        results="outputs/analysis_results.json",
        debug="outputs/analysis_results_debug.json"
    params:
        model=config.get("model", "deepseek-chat"),
        temperature=config.get("temperature", 0),
        workers=config.get("workers", 4)
    shell:
        "PYTHONPATH=src conda run --no-capture-output -n bio_llm python -m bio_llm.analysis"
        " --input {input} --output {output.results}"
        " --model {params.model} --temperature {params.temperature}"
        " --workers {params.workers}"
        " --debug"

rule generate_report:
    input:
        llm_json="outputs/analysis_results.json",
        debug_json="outputs/analysis_results_debug.json",
        abstracts="data/interim/abstracts_for_test.txt",
        trrust_by_pmid="data/raw/trrust_by_pmid.tsv"
    output:
        "outputs/report.html"
    shell:
        "PYTHONPATH=src conda run --no-capture-output -n bio_llm python -m bio_llm.reporting"
        " --llm-json {input.llm_json} --abstracts {input.abstracts} --output {output}"
        " --debug-json {input.debug_json} --trrust-by-pmid {input.trrust_by_pmid}"
