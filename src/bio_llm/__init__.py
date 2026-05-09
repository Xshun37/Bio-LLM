"""Bio-LLM package."""
import json
import os


def load_anomalies(anomalies_path=None):
    """Load curated TRRUST anomalies (PMIDs to exclude from sampling/reporting).

    Returns a dict: pmid -> list of anomaly dicts.
    """
    if anomalies_path is None:
        anomalies_path = os.path.join(
            os.path.dirname(os.path.dirname(os.path.dirname(__file__))),
            "data", "curated", "trrust_anomalies.jsonl",
        )
    if not os.path.exists(anomalies_path):
        return {}
    result = {}
    with open(anomalies_path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                entry = json.loads(line)
                pmid = entry.get("pmid", "")
                if pmid:
                    result.setdefault(pmid, []).append(entry)
            except json.JSONDecodeError:
                continue
    return result

