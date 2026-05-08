import pandas as pd
from Bio import Entrez
import time

def fetch_abstracts(pmid_list, email="your_email@example.com"):
    Entrez.email = email
    unique_pmids = list(set([str(p) for p in pmid_list if pd.notna(p)]))
    pmid_string = ",".join(unique_pmids)
    
    try:
        with Entrez.efetch(db="pubmed", id=pmid_string, retmode="xml") as handle:
            results = Entrez.read(handle)
        
        abstract_dict = {}
        for article in results['PubmedArticle']:
            pmid = str(article['MedlineCitation']['PMID'])
            article_data = article['MedlineCitation']['Article']
            if 'Abstract' in article_data:
                abstract_text = " ".join([str(part) for part in article_data['Abstract']['AbstractText']])
                abstract_dict[pmid] = abstract_text
            else:
                abstract_dict[pmid] = "No abstract available"
        return abstract_dict
    except Exception as e:
        print(f"Error fetching data from NCBI: {e}")
        return {}

# 1. 读取数据
df = pd.read_csv('trrust_rawdata.human.tsv', sep='\t', header=None, 
                 names=['tf', 'target', 'direction', 'pmid'])

# 2. 随机选出 5 个样例进行感性认识
sample_df = df.sample(5)
sample_pmids = sample_df['pmid'].tolist()
abstracts = fetch_abstracts(sample_pmids)

# 3. 打印到 txt 文件
output_file = "abstracts_for_test.txt"

with open(output_file, "w", encoding="utf-8") as f:
    f.write("=== TF-Target Analysis Test Data ===\n")
    f.write(f"Generated at: {time.strftime('%Y-%m-%d %H:%M:%S')}\n\n")

    for pmid in sample_pmids:
        pmid_str = str(pmid)
        if pmid_str in abstracts:
            f.write(f"{'='*50}\n")
            f.write(f"PMID: {pmid_str}\n")
            
            # 写入 TRRUST 里的标准答案
            known_relations = df[df['pmid'] == pmid]
            f.write("TRRUST Standard Relations:\n")
            for _, row in known_relations.iterrows():
                f.write(f"  - {row['tf']} -> {row['target']} ({row['direction']})\n")
            
            f.write("\nAbstract Content:\n")
            f.write(f"{abstracts[pmid_str]}\n\n")

print(f"任务完成！摘要已保存至: {output_file}")