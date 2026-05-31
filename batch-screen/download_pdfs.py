#!/usr/bin/env python3
"""从 relevant_pmids.txt 读取 PMID，提取 DOI，通过 Sci-Hub + Unpaywall 下载 PDF。

PDF 以 PMID 命名 (xxxxx.pdf)，方便统一记录。
"""

import time
import random
import argparse
import threading
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed

import requests
import bs4
from Bio import Entrez

# Sci-Hub 镜像（按优先级，2026-05 测试可用）
SCI_HUB_MIRRORS = [
    "https://sci-hub.ru",
    "https://sci-hub.mksa.top",
]

ENTREZ_EMAIL = "sanae0307@stu.pku.edu.cn"
UNPAYWALL_EMAIL = "sanae0307@stu.pku.edu.cn"

# 浏览器 User-Agent
HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
}


def extract_dois(pmids, batch_size=200):
    """批量从 PubMed 提取 DOI，返回 dict[pmid] -> doi。"""
    Entrez.email = ENTREZ_EMAIL
    result = {}
    for i in range(0, len(pmids), batch_size):
        batch = pmids[i:i + batch_size]
        print(f"  efetch: {len(batch)} 篇 ({i+1}-{i+len(batch)}/{len(pmids)})...")
        with Entrez.efetch(db="pubmed", id=",".join(batch), retmode="xml") as handle:
            records = Entrez.read(handle)

        for article in records.get("PubmedArticle", []):
            pmid = str(article["MedlineCitation"]["PMID"])
            doi = None
            pubmed_data = article.get("PubmedData", {})
            for article_id in pubmed_data.get("ArticleIdList", []):
                if getattr(article_id, "attributes", {}).get("IdType") == "doi":
                    doi = str(article_id).strip()
                    break
            if doi:
                result[pmid] = doi

        if i + batch_size < len(pmids):
            time.sleep(0.5)
    return result


def download_pdf(doi, mirror, output_dir, filename, verbose=False):
    """尝试从指定镜像下载 PDF，成功返回路径，失败返回 None。"""
    target_url = f"{mirror}/{doi}"
    if verbose:
        print(f"  Fetching {doi} from {mirror}...")

    try:
        res = requests.get(target_url, headers=HEADERS, timeout=30)
        res.raise_for_status()
    except Exception as e:
        if verbose:
            print(f"    * 访问失败: {e}")
        return None

    # 检查是否直接返回 PDF
    if 'application/pdf' in res.headers.get('Content-Type', ''):
        pdf_path = output_dir / filename
        with open(pdf_path, 'wb') as f:
            f.write(res.content)
        if verbose:
            print(f"    ✓ 直接下载: {pdf_path.name}")
        return pdf_path

    # 解析 HTML 找下载链接
    soup = bs4.BeautifulSoup(res.text, 'html.parser')
    download_link = None

    # 方式1: a[href*="/storage/"] (sci-hub.ru 新版)
    storage_a = soup.find('a', href=lambda h: h and '/storage/' in h)
    if storage_a:
        download_link = storage_a['href']

    # 方式2: div.download > a (旧版)
    if not download_link:
        try:
            download_link = soup.find('div', class_='download').a['href']
        except Exception:
            pass

    # 方式3: iframe src
    if not download_link:
        iframe = soup.find('iframe')
        if iframe:
            download_link = iframe.get('src', '')

    if not download_link:
        if verbose:
            print(f"    * 未收录: 无法找到 PDF 下载链接")
        return None

    if download_link.startswith('//'):
        download_link = 'https:' + download_link
    elif download_link.startswith('/'):
        download_link = mirror + download_link
    elif not download_link.startswith('http'):
        download_link = mirror + '/' + download_link

    # 下载 PDF（带重试）
    for attempt in range(5):
        try:
            pdf_res = requests.get(download_link, headers=HEADERS, timeout=60)
            pdf_res.raise_for_status()

            if 'application/pdf' not in pdf_res.headers.get('Content-Type', ''):
                if verbose:
                    print(f"    * 第 {attempt+1} 次尝试不是 PDF，重试...")
                time.sleep(1)
                continue

            pdf_path = output_dir / filename
            with open(pdf_path, 'wb') as f:
                f.write(pdf_res.content)
            if verbose:
                print(f"    ✓ 下载成功: {pdf_path.name}")
            return pdf_path
        except Exception as e:
            if verbose:
                print(f"    * 下载失败 (尝试 {attempt+1}): {e}")
            time.sleep(1)

    return None


def download_from_unpaywall(doi, output_dir, filename, verbose=False):
    """通过 Unpaywall API 获取 OA 版本的 PDF，成功返回路径，失败返回 None。"""
    if verbose:
        print(f"  [Unpaywall] 查询 {doi}...")
    try:
        url = f"https://api.unpaywall.org/v2/{doi}?email={UNPAYWALL_EMAIL}"
        r = requests.get(url, timeout=10)
        data = r.json()
    except Exception as e:
        if verbose:
            print(f"    * Unpaywall API 失败: {e}")
        return None

    if not data.get("is_oa"):
        if verbose:
            print(f"    * Unpaywall: 非 OA")
        return None

    # 找 PDF URL
    pdf_url = None
    best = data.get("best_oa_location") or {}
    pdf_url = best.get("url_for_pdf") or best.get("url")
    if not pdf_url:
        for loc in data.get("oa_locations", []):
            if loc.get("url_for_pdf"):
                pdf_url = loc["url_for_pdf"]
                break

    if not pdf_url:
        if verbose:
            print(f"    * Unpaywall: OA 但无 PDF 链接")
        return None

    if verbose:
        print(f"    → {pdf_url[:80]}")

    # 下载 PDF
    for attempt in range(3):
        try:
            pdf_res = requests.get(pdf_url, headers=HEADERS, timeout=60, allow_redirects=True)
            if pdf_res.status_code != 200:
                if verbose:
                    print(f"    * 下载失败 HTTP {pdf_res.status_code}，重试...")
                time.sleep(1)
                continue
            # 验证是 PDF
            is_pdf = pdf_res.content[:5] == b"%PDF-"
            if not is_pdf and "application/pdf" not in pdf_res.headers.get("Content-Type", ""):
                if verbose:
                    print(f"    * 第 {attempt+1} 次尝试不是 PDF，重试...")
                time.sleep(1)
                continue
            pdf_path = output_dir / filename
            with open(pdf_path, "wb") as f:
                f.write(pdf_res.content)
            if verbose:
                print(f"    ✓ Unpaywall 下载成功: {pdf_path.name}")
            return pdf_path
        except Exception as e:
            if verbose:
                print(f"    * 下载失败 (尝试 {attempt+1}): {e}")
            time.sleep(1)

    return None


def download_one(pmid, doi, output_dir):
    """下载单篇 PDF，返回 (pmid, doi, success: bool)。"""
    filename = f"{pmid}.pdf"
    pdf_path = output_dir / filename

    if pdf_path.exists():
        return (pmid, doi, True)

    # 随机延迟，避免并发打同一镜像
    time.sleep(random.uniform(0.1, 0.5))

    for mirror in SCI_HUB_MIRRORS:
        result = download_pdf(doi, mirror, output_dir, filename)
        if result:
            return (pmid, doi, True)
        time.sleep(0.5)

    result = download_from_unpaywall(doi, output_dir, filename)
    if result:
        return (pmid, doi, True)

    return (pmid, doi, False)


def main():
    parser = argparse.ArgumentParser(description="下载相关文献 PDF")
    parser.add_argument("--run-dir", default=None, help="指定运行目录（默认最新）")
    parser.add_argument("--workers", type=int, default=4, help="并行下载数")
    args = parser.parse_args()

    base_dir = Path(__file__).parent / "outputs" / "batch_screen"

    if args.run_dir:
        out_dir = Path(args.run_dir)
    else:
        runs = sorted([d for d in base_dir.iterdir() if d.is_dir() and d.name.startswith("run_")])
        if not runs:
            print("错误: 未找到运行目录，请先运行 batch_screen.py")
            return
        out_dir = runs[-1]
    print(f"使用运行目录: {out_dir.name}")

    pmids_file = out_dir / "relevant_pmids.txt"

    # 读取 PMID
    if not pmids_file.exists():
        print(f"错误: 未找到 {pmids_file.name}，请先运行 batch_screen.py")
        return
    with open(pmids_file) as f:
        pmids = [line.strip() for line in f if line.strip()]
    print(f"读取 {len(pmids)} 个 PMID\n")

    # 获取 DOI：优先从 abstracts.json 读取，否则查 PubMed
    import json as _json
    abstracts_file = out_dir / "abstracts.json"
    pmid_doi = {}
    if abstracts_file.exists():
        with open(abstracts_file) as f:
            abstracts = _json.load(f)
        pmid_doi = {pmid: info["doi"] for pmid, info in abstracts.items()
                    if pmid in pmids and info.get("doi")}
        print(f"Step 1: 从 abstracts.json 读取 {len(pmid_doi)} 个 DOI")

    missing = [p for p in pmids if p not in pmid_doi]
    if missing:
        print(f"  补充查询 {len(missing)} 个缺失 DOI...")
        extra = extract_dois(missing)
        pmid_doi.update(extra)

    print(f"  共获取 {len(pmid_doi)} 个 DOI\n")

    if not pmid_doi:
        print("未获取到任何 DOI，退出")
        return

    # 创建输出目录
    output_dir = out_dir / "papers"
    output_dir.mkdir(exist_ok=True)

    # 下载
    # 先统计已跳过的
    already_done = sum(1 for pmid in pmid_doi if (output_dir / f"{pmid}.pdf").exists())
    to_download = len(pmid_doi) - already_done
    items = sorted(pmid_doi.items())

    if already_done:
        print(f"  已下载: {already_done} 篇（自动跳过）")
    print(f"  待下载: {to_download} 篇 (workers={args.workers})\n")

    success = 0
    failed = []
    lock = threading.Lock()
    done_count = 0

    with ThreadPoolExecutor(max_workers=args.workers) as executor:
        futures = {
            executor.submit(download_one, pmid, doi, output_dir): (pmid, doi)
            for pmid, doi in items
        }
        for future in as_completed(futures):
            pmid, doi, ok = future.result()
            with lock:
                done_count += 1
                if ok:
                    success += 1
                else:
                    failed.append((pmid, doi))
                # 单行进度，\r 覆盖
                print(f"\r  进度: {done_count}/{len(items)}  "
                      f"成功: {success}  失败: {len(failed)}", end="", flush=True)

    # 换行 + 汇总
    print(f"\n\n{'=' * 60}")
    print(f"下载完成: {success}/{len(pmid_doi)} 成功")
    if failed:
        print(f"失败 {len(failed)} 篇:")
        for pmid, doi in failed:
            print(f"  PMID:{pmid}  {doi}")


if __name__ == "__main__":
    main()
