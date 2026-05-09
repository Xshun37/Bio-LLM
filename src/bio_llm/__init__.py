"""Bio-LLM package."""
import json
import os
import re


# --- Shared gene name synonym maps ---
_SYNONYM_MAP = {
    "AP-2": "TFAP2A", "AP2": "TFAP2A", "AP-2ALPHA": "TFAP2A",
    "AP-2BETA": "TFAP2B", "AP-2GAMMA": "TFAP2C",
    "C/EBPALPHA": "CEBPA", "C/EBP-ALPHA": "CEBPA", "C/EBP A": "CEBPA",
    "C/EBPBETA": "CEBPB", "C/EBP-BETA": "CEBPB", "C/EBP B": "CEBPB",
    "C/EBPDELTA": "CEBPD", "C/EBP-DELTA": "CEBPD", "C/EBP D": "CEBPD",
    "C/EBPGAMMA": "CEBPG", "C/EBP-EPSILON": "CEBPE", "C/EBPZETA": "CEBPZ",
    "NF-KB": "NFKB1", "NFKB": "NFKB1", "NF-KAPPA-B": "NFKB1",
    "NF-KB1": "NFKB1", "NF-KB P50": "NFKB1", "P50": "NFKB1",
    "NF-KB P65": "RELA", "P65": "RELA", "NFKB3": "RELA",
    "NF-KB2": "NFKB2", "P52": "NFKB2",
    "RELB": "RELB", "C-REL": "REL",
    "STAT3": "STAT3", "STAT1": "STAT1", "STAT5": "STAT5A",
    "P53": "TP53", "TP53": "TP53",
    "C-MYC": "MYC", "CMYC": "MYC", "MYCC": "MYC",
    "N-MYC": "MYCN", "L-MYC": "MYCL",
    "C-JUN": "JUN", "CJUN": "JUN",
    "C-FOS": "FOS", "CFOS": "FOS",
    "LIVER-ENRICHED ACTIVATOR PROTEIN": "CEBPB",
    "LIVER-ENRICHED INHIBITORY PROTEIN": "CEBPB",
    "LAP": "CEBPB", "LIP": "CEBPB",
    "ER-ALPHA": "ESR1", "ER-BETA": "ESR2",
    "PPAR-GAMMA": "PPARG", "PPAR-ALPHA": "PPARA",
    "HIF-1ALPHA": "HIF1A", "HIF-2ALPHA": "HIF2A",
    "SP1": "SP1", "SP3": "SP3",
    "EGR-1": "EGR1",
    "OCT-1": "POU2F1", "OCT-4": "POU5F1", "OCT4": "POU5F1",
    "SOX2": "SOX2",
    "NANOG": "NANOG",
    "KLF4": "KLF4",
    "GATA1": "GATA1", "GATA3": "GATA3",
    "TBET": "TBX21",
    "FOXP3": "FOXP3",
    "ROR-GAMMA-T": "RORC",
    "BCL-6": "BCL6",
    "ZBP-89": "ZNF148", "ZBP89": "ZNF148", "BFCOL1": "ZNF148",
    "SAF-1": "MAZ", "SAF1": "MAZ", "ZNF801": "MAZ",
    "YB-1": "YBX1", "YB1": "YBX1",
    "TEL1": "ETV6", "TEL": "ETV6",
    "KLF8": "KLF8",
    "MBD1": "MBD1", "MBD2": "MBD2", "MECP2": "MECP2",
    "USF1": "USF1", "USF2": "USF2",
    "ATF4": "ATF4", "ATF6": "ATF6",
    "HDAC1": "HDAC1", "HDAC3": "HDAC3",
    "BMAL1": "ARNTL", "BMAL-1": "ARNTL",
}

_TARGET_SYNONYM_MAP = {
    "DPRL": "PRL", "PRL1": "PRL",
    "BCL-2": "BCL2", "BCL-XL": "BCL2L1",
    "CDKN1A": "CDKN1A", "P21": "CDKN1A",
    "CDKN2A": "CDKN2A", "P16": "CDKN2A",
    "GUSB": "GUSB", "BETA-GLUC": "GUSB", "BETA-GLUCURONIDASE": "GUSB",
    "MUC5B": "MUC5B", "MUC5AC": "MUC5AC",
    "VWF": "VWF", "VON WILLEBRAND FACTOR": "VWF",
    "CPLA2": "PLA2G4A", "COX-2": "PTGS2", "COX2": "PTGS2",
    "MRP14": "S100A9",
    "SERPINE1": "SERPINE1", "PAI-1": "SERPINE1", "PAI1": "SERPINE1",
    "SERPBP1": "SERPBP1",
    "AGGF1": "AGGF1", "SIRT1": "SIRT1", "EPSTI1": "EPSTI1",
    "ALOX5": "ALOX5", "5-LIPOXYGENASE": "ALOX5",
    "APOM": "APOM", "SLC6A4": "SLC6A4", "DRD2": "DRD2",
    "VEGFA": "VEGFA", "CCNA2": "CCNA2", "CDH1": "CDH1",
}


_HGNC_ALIAS_MAP = None


def _load_hgnc_map():
    """Lazy-load the HGNC alias map (auto-generated from HGNC complete set)."""
    global _HGNC_ALIAS_MAP
    if _HGNC_ALIAS_MAP is None:
        try:
            path = os.path.join(
                os.path.dirname(os.path.dirname(os.path.dirname(__file__))),
                "data", "curated", "gene_alias_map.json",
            )
            if os.path.exists(path):
                with open(path, "r", encoding="utf-8") as f:
                    _HGNC_ALIAS_MAP = json.load(f)
            else:
                _HGNC_ALIAS_MAP = {}
        except Exception:
            _HGNC_ALIAS_MAP = {}
    return _HGNC_ALIAS_MAP


def normalize_gene_name(raw_name, alias_map):
    """Normalize a gene name through alias maps. Returns standardized symbol.

    Priority: 1) curated hardcoded map  2) HGNC comprehensive map 3) stripped name
    """
    if not raw_name:
        return ""
    name = str(raw_name).strip().upper()
    name = re.sub(r"\(.*\)", "", name).strip()

    # 1. Hardcoded curated map (highest priority)
    if name in alias_map:
        return alias_map[name]
    stripped = re.sub(r"[\s/\-]+", "", name)
    if stripped in alias_map:
        return alias_map[stripped]

    # 2. HGNC comprehensive alias map
    hgnc = _load_hgnc_map()
    if name in hgnc:
        return hgnc[name].upper()
    if stripped in hgnc:
        return hgnc[stripped].upper()

    return stripped


def normalize_tf(name):
    return normalize_gene_name(name, _SYNONYM_MAP)


def normalize_target(name):
    return normalize_gene_name(name, _TARGET_SYNONYM_MAP)


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

