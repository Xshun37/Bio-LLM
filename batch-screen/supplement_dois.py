#!/usr/bin/env python3
"""补全无 DOI 的 PMID：先 CrossRef 反查，再 PMC 查 PMC ID，最后用 PMID 直搜 Sci-Hub。"""

import json
import time
import requests
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed

HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
}


def lookup_crossref(pmid, title):
    """用标题在 CrossRef 反查 DOI。"""
    try:
        r = requests.get('https://api.crossref.org/works',
                         params={'query.bibliographic': title, 'rows': 1},
                         timeout=10)
        items = r.json().get('message', {}).get('items', [])
        if items and items[0].get('DOI'):
            return {'pmid': pmid, 'doi': items[0]['DOI'], 'source': 'crossref'}
    except Exception:
        pass
    return None


def lookup_pmc(pmids):
    """批量查 PMC ID，返回 dict[pmid] -> pmcid。"""
    result = {}
    for i in range(0, len(pmids), 200):
        batch = ','.join(pmids[i:i + 200])
        try:
            r = requests.get(
                f'https://www.ncbi.nlm.nih.gov/pmc/utils/idconv/v1.0/?ids={batch}&format=json',
                timeout=15)
            for rec in r.json().get('records', []):
                if rec.get('pmcid'):
                    result[rec['pmid']] = rec['pmcid']
        except Exception:
            pass
        if i + 200 < len(pmids):
            time.sleep(0.5)
    return result


def download_pmc_pdf(pmcid, output_dir, pmid):
    """从 PMC 下载 PDF。"""
    url = f"https://www.ncbi.nlm.nih.gov/pmc/articles/{pmcid}/pdf/"
    try:
        r = requests.get(url, headers=HEADERS, timeout=60, allow_redirects=True)
        if r.status_code == 200 and (r.content[:5] == b"%PDF-" or 'application/pdf' in r.headers.get('Content-Type', '')):
            path = output_dir / f"{pmid}.pdf"
            with open(path, 'wb') as f:
                f.write(r.content)
            return path
    except Exception:
        pass
    return None


def download_scihub_by_pmid(pmid, output_dir, mirrors):
    """直接用 PMID 在 Sci-Hub 搜索。"""
    for mirror in mirrors:
        try:
            url = f"{mirror}/{pmid}"
            r = requests.get(url, headers=HEADERS, timeout=30)
            if r.status_code != 200:
                continue

            if 'application/pdf' in r.headers.get('Content-Type', ''):
                path = output_dir / f"{pmid}.pdf"
                with open(path, 'wb') as f:
                    f.write(r.content)
                return path

            import bs4
            soup = bs4.BeautifulSoup(r.text, 'html.parser')
            download_link = None

            storage_a = soup.find('a', href=lambda h: h and '/storage/' in h)
            if storage_a:
                download_link = storage_a['href']
            if not download_link:
                iframe = soup.find('iframe')
                if iframe:
                    download_link = iframe.get('src', '')

            if download_link:
                if download_link.startswith('//'):
                    download_link = 'https:' + download_link
                elif download_link.startswith('/'):
                    download_link = mirror + download_link
                pdf_r = requests.get(download_link, headers=HEADERS, timeout=60)
                if pdf_r.status_code == 200 and 'application/pdf' in pdf_r.headers.get('Content-Type', ''):
                    path = output_dir / f"{pmid}.pdf"
                    with open(path, 'wb') as f:
                        f.write(pdf_r.content)
                    return path
        except Exception:
            continue
        time.sleep(0.5)
    return None


def main():
    import argparse
    parser = argparse.ArgumentParser(description="补全无 DOI 的 PMID")
    parser.add_argument("--run-dir", required=True)
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--mirrors", nargs='+',
                        default=["https://sci-hub.ru", "https://sci-hub.mksa.top"])
    args = parser.parse_args()

    run_dir = Path(args.run_dir)
    output_dir = run_dir / "papers"
    output_dir.mkdir(exist_ok=True)

    with open(run_dir / "abstracts.json") as f:
        abstracts = json.load(f)
    with open(run_dir / "relevant_pmids.txt") as f:
        pmids = [l.strip() for l in f if l.strip()]

    no_doi = [p for p in pmids if p in abstracts and not abstracts[p].get('doi')]
    print(f"无 DOI: {len(no_doi)} 篇\n")

    # 跳过已下载的
    no_doi = [p for p in no_doi if not (output_dir / f"{p}.pdf").exists()]
    print(f"待处理（排除已下载）: {len(no_doi)} 篇\n")

    # Step 1: CrossRef 反查 DOI
    print("Step 1: CrossRef 反查 DOI...")
    doi_map = {}
    with ThreadPoolExecutor(max_workers=args.workers) as executor:
        futures = {
            executor.submit(lookup_crossref, pmid, abstracts[pmid]['title']): pmid
            for pmid in no_doi
        }
        for future in as_completed(futures):
            result = future.result()
            if result:
                doi_map[result['pmid']] = result['doi']

    print(f"  CrossRef 找到 {len(doi_map)} 个 DOI")

    # 保存补全的 DOI 到 abstracts.json
    for pmid, doi in doi_map.items():
        abstracts[pmid]['doi'] = doi
    with open(run_dir / "abstracts.json", 'w') as f:
        json.dump(abstracts, f, ensure_ascii=False, indent=2)
    print(f"  已更新 abstracts.json\n")

    # Step 2: PMC 查 PMC ID（对 CrossRef 没找到的）
    remaining = [p for p in no_doi if p not in doi_map]
    print(f"Step 2: PMC 查询（剩余 {len(remaining)} 篇）...")
    pmc_map = lookup_pmc(remaining)
    print(f"  找到 {len(pmc_map)} 个 PMC ID")

    # Step 3: 下载 PMC PDF
    pmc_success = 0
    for pmid, pmcid in pmc_map.items():
        if (output_dir / f"{pmid}.pdf").exists():
            pmc_success += 1
            continue
        path = download_pmc_pdf(pmcid, output_dir, pmid)
        if path:
            pmc_success += 1
            print(f"  ✓ PMC: {pmid} ({pmcid})")
    print(f"  PMC 下载 {pmc_success}/{len(pmc_map)} 成功\n")

    # Step 4: Sci-Hub 用 PMID 直搜（对剩余的）
    still_remaining = [p for p in remaining
                       if p not in pmc_map
                       and not (output_dir / f"{p}.pdf").exists()]
    print(f"Step 3: Sci-Hub PMID 直搜（剩余 {len(still_remaining)} 篇）...")

    scihub_success = 0
    scihub_failed = []
    with ThreadPoolExecutor(max_workers=args.workers) as executor:
        futures = {
            executor.submit(download_scihub_by_pmid, pmid, output_dir, args.mirrors): pmid
            for pmid in still_remaining
        }
        for future in as_completed(futures):
            pmid = futures[future]
            result = future.result()
            if result:
                scihub_success += 1
                print(f"  ✓ Sci-Hub: {pmid}")
            else:
                scihub_failed.append(pmid)

    print(f"  Sci-Hub 下载 {scihub_success}/{len(still_remaining)} 成功\n")

    # 汇总
    total_recovered = len(doi_map) + pmc_success + scihub_success
    still_missing = [p for p in no_doi if not (output_dir / f"{p}.pdf").exists()]

    print("=" * 60)
    print(f"补全结果:")
    print(f"  CrossRef DOI: {len(doi_map)} 个（已写入 abstracts.json，重新跑 download_pdfs.py 可下载）")
    print(f"  PMC 直接下载: {pmc_success} 篇")
    print(f"  Sci-Hub PMID 直搜: {scihub_success} 篇")
    print(f"  仍缺失: {len(still_missing)} 篇")

    if still_missing:
        missing_file = run_dir / "missing_pmids.txt"
        with open(missing_file, 'w') as f:
            for pmid in still_missing:
                f.write(f"{pmid}\n")
        print(f"  已保存缺失列表: {missing_file}")


if __name__ == "__main__":
    main()
