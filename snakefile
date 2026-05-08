import collections
import collections.abc

for _attr in ("Iterable", "Mapping", "MutableMapping", "Sequence", "MutableSequence",
               "Set", "MutableSet", "MappingView", "KeysView", "ItemsView", "ValuesView"):
    if not hasattr(collections, _attr):
        setattr(collections, _attr, getattr(collections.abc, _attr, None))

configfile: "config.yaml"

rule all:
    input:
        "report.html"

rule generate_abstracts:
    input:
        "trrust_rawdata.human.tsv"
    output:
        "abstracts_for_test.txt"
    params:
        sample_size=config.get("sample_size", 5),
        seed=config.get("seed", 42),
        email=config.get("email", "your_email@example.com")
    shell:
        "conda run -n bio_llm python IDtoAbstract.py --input {input} --output {output}"
        " --sample-size {params.sample_size} --seed {params.seed} --email '{params.email}'"

rule analyze_abstracts:
    input:
        "abstracts_for_test.txt"
    output:
        "analysis_results.json"
    params:
        model=config.get("model", "qwen-max"),
        temperature=config.get("temperature", 0),
        workers=config.get("workers", 16)
    shell:
        "conda run -n bio_llm python main.py --input {input} --output {output}"
        " --model {params.model} --temperature {params.temperature}"
        " --workers {params.workers}"

rule generate_report:
    input:
        llm_json="analysis_results.json",
        abstracts="abstracts_for_test.txt"
    output:
        "report.html"
    shell:
        "conda run -n bio_llm python generate_result.py"
        " --llm-json {input.llm_json} --abstracts {input.abstracts} --output {output}"
