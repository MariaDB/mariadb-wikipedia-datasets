"""Dataset L — All of Finnish Wikipedia, now.

Every article on Finnish Wikipedia (~573,000): current text as clean Markdown, from FineWiki
(August 2025 snapshot). No vectors: embedding all of it is a GPU job (see the README).

    python dataset_l.py

Safe to stop and run again: finished steps are kept (cache/ and output/l/).

Steps:
  1. FineWiki (Hugging Face) holds Finnish Wikipedia as 2 Parquet files (1.65 GB). DuckDB reads
     only the columns we need, straight from Hugging Face — the raw wikitext, about two thirds of
     each file, is never downloaded. ~5 minutes; longer if Hugging Face limits requests.
  2. FineWiki sometimes has several versions of one article: keep the newest.
  3. output/l/README.md and output/l/SHA256SUMS.
"""
import hashlib, os, time
import duckdb, requests

OUT, CACHE = "output/l", "cache/l"
FINEWIKI = "HuggingFaceFW/finewiki"
HEADERS = {"User-Agent": "mariadb-wikipedia-datasets/1.0 (https://github.com/MariaDB/mariadb-wikipedia-datasets)"}
COLUMNS = "'fi' AS lang, page_id, version AS rev_id, wikidata_id, title, url, date_modified, text"
os.makedirs(OUT, exist_ok=True)
os.makedirs(CACHE, exist_ok=True)

db = duckdb.connect()
db.sql("SET enable_progress_bar = false")
r = requests.get(f"https://huggingface.co/api/datasets/{FINEWIKI}/tree/main/data/fiwiki", headers=HEADERS)
r.raise_for_status()
parts = []
for path in sorted(f["path"] for f in r.json() if f["path"].endswith(".parquet")):
    part = f"{CACHE}/fi-{os.path.basename(path)}"
    parts.append(part)
    while not os.path.exists(part):
        try:
            start = time.time()
            db.sql(f"""COPY (SELECT {COLUMNS} FROM read_parquet('https://huggingface.co/datasets/{FINEWIKI}/resolve/main/{path}'))
                       TO '{part}.tmp' (FORMAT parquet)""")
            os.rename(part + ".tmp", part)
            print(f"1/3 {path}: {time.time() - start:.0f} s", flush=True)
        except duckdb.HTTPException as e:                           # Hugging Face: "too many requests"
            if "429" not in str(e):
                raise
            print("    Hugging Face says 'too many requests'. Waiting 5 minutes ...", flush=True)
            time.sleep(300)

out = f"{OUT}/articles-fi.parquet"
db.sql(f"""COPY (SELECT * FROM read_parquet({parts})
                 QUALIFY row_number() OVER (PARTITION BY page_id ORDER BY rev_id DESC) = 1
                 ORDER BY page_id) TO '{out}' (FORMAT parquet, COMPRESSION zstd)""")
n, mb = db.sql(f"SELECT count(*), sum(length(text)) / 1e6 FROM '{out}'").fetchone()
print(f"2/3 {out}: {n:,} articles, {mb:,.0f} MB of text, {os.path.getsize(out) / 1e6:,.0f} MB file", flush=True)

with open(f"{OUT}/README.md", "w") as f:
    f.write(f"""# Dataset L — All of Finnish Wikipedia, now

{n:,} Finnish Wikipedia articles, the current text of each as clean Markdown (FineWiki, August 2025
snapshot): {mb:,.0f} MB of text. Built by `dataset_l.py` in https://github.com/MariaDB/mariadb-wikipedia-datasets.

articles-fi.parquet: lang, page_id, rev_id, wikidata_id, title, url, date_modified, text — the
same columns as dataset S, so code written for S runs on L.

Source: FineWiki (https://huggingface.co/datasets/HuggingFaceFW/finewiki), made from Wikimedia's
HTML dumps. Licence: Wikipedia text is CC BY-SA 4.0 (https://creativecommons.org/licenses/by-sa/4.0/);
credit Wikipedia and link to the article (url column).
""")
with open(f"{OUT}/SHA256SUMS", "w") as f:
    for name in sorted(os.listdir(OUT)):
        if name != "SHA256SUMS":
            h = hashlib.sha256()
            with open(f"{OUT}/{name}", "rb") as data:
                for block in iter(lambda: data.read(1 << 20), b""):
                    h.update(block)
            f.write(f"{h.hexdigest()}  {name}\n")
print(f"3/3 Done: {OUT}/")
