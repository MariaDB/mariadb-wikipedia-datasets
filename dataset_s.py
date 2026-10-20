"""Dataset S — History of Finland, now, in Finnish, Swedish and English, with vectors.

The ~19,750 Finnish Wikipedia articles on the history of Finland, and the Swedish and English
articles on the same topics: current text (clean Markdown from FineWiki, August 2025), their
categories, and one bge-m3 vector per article.

    python dataset_s.py                    # text from FineWiki + the ready-made vectors
    python dataset_s.py --vectors embed    # compute the vectors yourself (Ollama + bge-m3, ~5 h on an M4)
    python dataset_s.py --vectors none     # text only
    python dataset_s.py --refresh-lists    # walk Wikipedia's categories again instead of using lists/

Safe to stop and run again: finished steps are kept (cache/ and output/s/).

Steps:
  1. The article list. lists/ in this repo holds it: every article in the category
     Luokka:Suomen historia and three levels of subcategories, plus the main article of each named
     category one level deeper (Luokka:Urho Kekkonen -> Urho Kekkonen). --refresh-lists rebuilds it
     with the Wikipedia API (~10 minutes).
  2. The text. FineWiki (Hugging Face) holds Wikipedia per language as Parquet. DuckDB reads only
     the columns we need, straight from Hugging Face: Finnish articles by page_id, Swedish and English
     ones by wikidata_id (same topic, same Wikidata id). Finnish ~5 min, Swedish ~10 min, English
     ~80 min (38 GB to scan) — longer when Hugging Face limits requests; the script then waits.
  3. The vectors: downloaded (119 MB), or computed with bge-m3 in Ollama.
  4. output/s/README.md and output/s/SHA256SUMS.
"""
import argparse, csv, glob, hashlib, json, os, shutil, struct, sys, time
import duckdb, requests
import pyarrow as pa, pyarrow.parquet as pq

OUT, CACHE = "output/s", "cache/s"
FINEWIKI = "HuggingFaceFW/finewiki"
RELEASE = "https://github.com/robertsilen/mariadb-wikipedia-datasets/releases/download/s-2026-10"
HEADERS = {"User-Agent": "mariadb-wikipedia-datasets/1.0 (https://github.com/robertsilen/mariadb-wikipedia-datasets)"}
OLLAMA, MODEL, CONTEXT = "http://localhost:11434", "bge-m3", 8192

parser = argparse.ArgumentParser()
parser.add_argument("--vectors", choices=["download", "embed", "none"], default="download")
parser.add_argument("--refresh-lists", action="store_true")
args = parser.parse_args()
os.makedirs(OUT, exist_ok=True)
os.makedirs(CACHE, exist_ok=True)

# --- 1. The article list -----------------------------------------------------------------------
API = "https://fi.wikipedia.org/w/api.php"
session = requests.Session()
session.headers.update(HEADERS)


def api_all(params, key):
    """Call the Wikipedia API, following 'continue'; politely slow (Wikipedia allows ~200/min)."""
    params = dict(params, format="json", formatversion="2")
    while True:
        time.sleep(0.4)
        r = session.get(API, params=params, timeout=60)
        if r.status_code != 200:
            time.sleep(int(r.headers.get("Retry-After", 10)))
            continue
        data = r.json()
        yield from data["query"][key]
        if "continue" not in data:
            return
        params.update(data["continue"])


if args.refresh_lists or not os.path.exists("lists/s-articles-fi.tsv"):
    name = lambda t: t.removeprefix("Luokka:")
    root, levels = "Luokka:Suomen historia", 3
    categories, articles, links, members, deeper = {root}, {}, set(), set(), {}
    this_level = [root]
    for level in range(levels + 1):
        next_level = []
        for cat in this_level:
            for m in api_all({"action": "query", "list": "categorymembers", "cmtitle": cat,
                              "cmlimit": "500", "cmprop": "ids|title"}, "categorymembers"):
                if m["ns"] == 14 and level < levels:
                    links.add((name(cat), name(m["title"])))
                    if m["title"] not in categories:
                        categories.add(m["title"])
                        next_level.append(m["title"])
                elif m["ns"] == 14:
                    deeper.setdefault(m["title"], cat)
                elif m["ns"] == 0:
                    articles.setdefault(m["pageid"], (m["title"], level))
                    members.add((m["pageid"], name(cat)))
        print(f"level {level}: {len(categories)} categories, {len(articles)} articles", flush=True)
        this_level = next_level
    candidates = [c for c in deeper if c not in categories]       # named categories one level deeper
    for i in range(0, len(candidates), 50):
        for page in api_all({"action": "query", "titles": "|".join(name(c) for c in candidates[i:i + 50])}, "pages"):
            cat = "Luokka:" + page.get("title", "")
            if not page.get("missing") and page["ns"] == 0 and page["pageid"] not in articles and cat in deeper:
                articles[page["pageid"]] = (page["title"], levels + 1)
                links.add((name(deeper[cat]), page["title"]))
                members.add((page["pageid"], page["title"]))
    with open("lists/s-articles-fi.tsv", "w") as f:
        f.write("page_id\ttitle\tlevel\n")
        f.writelines(f"{p}\t{t}\t{l}\n" for p, (t, l) in sorted(articles.items()))
    with open("lists/s-category-links-fi.tsv", "w") as f:
        f.write("parent\tchild\n")
        f.writelines(f"{a}\t{b}\n" for a, b in sorted(links))
    with open("lists/s-article-categories-fi.tsv", "w") as f:
        f.write("page_id\tcategory\n")
        f.writelines(f"{p}\t{c}\n" for p, c in sorted(members))
    shutil.rmtree(CACHE)                                          # a new list needs new text
    os.makedirs(CACHE)
print("1/4 article list:", sum(1 for _ in open("lists/s-articles-fi.tsv")) - 1, "Finnish articles", flush=True)

# --- 2. The text, from FineWiki ----------------------------------------------------------------
db = duckdb.connect()
db.sql("SET enable_progress_bar = false")
COLUMNS = "page_id, version AS rev_id, wikidata_id, title, url, date_modified, text"


def finewiki_files(lang):
    r = requests.get(f"https://huggingface.co/api/datasets/{FINEWIKI}/tree/main/data/{lang}wiki", headers=HEADERS)
    r.raise_for_status()
    return sorted(f["path"] for f in r.json() if f["path"].endswith(".parquet"))


for lang in ("fi", "sv", "en"):
    out = f"{OUT}/articles-{lang}.parquet"
    if os.path.exists(out):
        print(f"2/4 {out}: already done", flush=True)
        continue
    wanted = ("page_id IN (SELECT page_id FROM read_csv('lists/s-articles-fi.tsv', delim='\t', header=true))"
              if lang == "fi" else
              f"wikidata_id IN (SELECT wikidata_id FROM '{OUT}/articles-fi.parquet' WHERE wikidata_id IS NOT NULL)")
    parts = []
    for path in finewiki_files(lang):
        part = f"{CACHE}/{lang}-{os.path.basename(path)}"
        parts.append(part)
        while not os.path.exists(part):
            try:
                db.sql(f"""COPY (SELECT '{lang}' AS lang, {COLUMNS}
                                 FROM read_parquet('https://huggingface.co/datasets/{FINEWIKI}/resolve/main/{path}')
                                 WHERE {wanted}) TO '{part}.tmp' (FORMAT parquet)""")
                os.rename(part + ".tmp", part)
                print(f"    {lang} {path}: done", flush=True)
            except duckdb.HTTPException as e:                     # Hugging Face: "too many requests"
                if "429" not in str(e):
                    raise
                print("    Hugging Face says 'too many requests'. Waiting 5 minutes ...", flush=True)
                time.sleep(300)
    # FineWiki sometimes has several versions of one article: keep the newest.
    db.sql(f"""COPY (SELECT * FROM read_parquet({parts})
                     QUALIFY row_number() OVER (PARTITION BY page_id ORDER BY rev_id DESC) = 1
                     ORDER BY page_id) TO '{out}' (FORMAT parquet, COMPRESSION zstd)""")
    print(f"2/4 {out}: {db.sql(f'SELECT count(*) FROM {chr(39)}{out}{chr(39)}').fetchone()[0]} articles", flush=True)
for name in ("s-category-links-fi.tsv", "s-article-categories-fi.tsv"):
    shutil.copy(f"lists/{name}", f"{OUT}/{name.removeprefix('s-')}")

# --- 3. The vectors ----------------------------------------------------------------------------
if args.vectors == "download":
    for lang in ("fi", "sv", "en"):
        path = f"{OUT}/vectors-{MODEL}-{lang}.parquet"
        if not os.path.exists(path):
            with requests.get(f"{RELEASE}/{os.path.basename(path)}", headers=HEADERS, stream=True, timeout=60) as r:
                r.raise_for_status()
                with open(path + ".part", "wb") as f:
                    for chunk in r.iter_content(1 << 20):
                        f.write(chunk)
            os.rename(path + ".part", path)
        print(f"3/4 {path}: downloaded", flush=True)
elif args.vectors == "embed":
    # One vector per article: title + text, up to 8,192 tokens. Ollama needs num_batch as well as
    # num_ctx, or it refuses (or cuts) anything over 2,048 tokens.
    options = {"num_ctx": CONTEXT, "num_batch": CONTEXT}

    def embed(texts, truncate):
        r = requests.post(f"{OLLAMA}/api/embed", timeout=600, json={
            "model": MODEL, "input": texts, "truncate": truncate, "options": options})
        if r.status_code != 200:
            raise ValueError(r.text)
        return r.json()["embeddings"]

    for lang in ("fi", "sv", "en"):
        path = f"{OUT}/vectors-{MODEL}-{lang}.parquet"
        if os.path.exists(path):
            continue
        articles = pq.read_table(f"{OUT}/articles-{lang}.parquet", columns=["page_id", "title", "text"]).to_pylist()
        done = set()
        for part in glob.glob(f"{CACHE}/vectors-{lang}-*.parquet"):
            done |= set(pq.read_table(part, columns=["page_id"]).column(0).to_pylist())
        todo = [a for a in articles if a["page_id"] not in done]
        print(f"3/4 {lang}: {len(done)} embedded, {len(todo)} to go", flush=True)
        for i in range(0, len(todo), 200):                          # save every 200 articles
            rows = []
            for a in todo[i:i + 200]:
                text = f"{a['title']}\n\n{a['text']}"
                try:
                    vector, cut = embed([text], truncate=False)[0], False
                except ValueError:                                  # longer than the model reads
                    vector, cut = embed([text], truncate=True)[0], True
                rows.append({"lang": lang, "page_id": a["page_id"], "model": MODEL, "truncated": cut,
                             "embedding": vector})
            pq.write_table(pa.Table.from_pylist(rows), f"{CACHE}/vectors-{lang}-{time.time_ns()}.parquet")
            print(f"    {lang}: {len(done) + i + len(rows)} / {len(articles)}", flush=True)
        table = pa.concat_tables(pq.read_table(p) for p in sorted(glob.glob(f"{CACHE}/vectors-{lang}-*.parquet")))
        embedding = pa.FixedSizeListArray.from_arrays(
            pa.array([x for v in table.column("embedding").to_pylist() for x in v], pa.float32()), 1024)
        table = table.set_column(table.schema.get_field_index("embedding"), "embedding", embedding)
        pq.write_table(table.sort_by("page_id"), path, compression="zstd")
        print(f"3/4 {path}: {table.num_rows} vectors", flush=True)

# --- 4. Data card and checksums ----------------------------------------------------------------
counts = {lang: pq.ParquetFile(f"{OUT}/articles-{lang}.parquet").metadata.num_rows for lang in ("fi", "sv", "en")}
with open(f"{OUT}/README.md", "w") as f:
    f.write(f"""# Dataset S — History of Finland, now (fi, sv, en)

{sum(counts.values()):,} Wikipedia articles: {counts['fi']:,} Finnish articles on the history of Finland
(category Luokka:Suomen historia, three levels of subcategories, plus the main article of each named
category one level deeper) and the {counts['sv']:,} Swedish and {counts['en']:,} English articles on the
same topics. Built by `dataset_s.py` in https://github.com/robertsilen/mariadb-wikipedia-datasets.

- articles-<lang>.parquet: lang, page_id, rev_id, wikidata_id (same topic across languages), title,
  url, date_modified, text (clean Markdown, from FineWiki — August 2025 snapshot).
- vectors-bge-m3-<lang>.parquet: lang, page_id, model, truncated, embedding (1024 float32 numbers):
  one vector per article from title + text with BAAI/bge-m3 (Ollama), cut at 8,192 tokens where
  truncated is true. Embed questions with the same model.
- category-links-fi.tsv: parent, child — the category tree. article-categories-fi.tsv: page_id,
  category — the Finnish articles' categories.

Sources: FineWiki (https://huggingface.co/datasets/HuggingFaceFW/finewiki), the Finnish Wikipedia API.
Licence: Wikipedia text is CC BY-SA 4.0 (https://creativecommons.org/licenses/by-sa/4.0/); credit
Wikipedia and link to the article (url column).
""")
with open(f"{OUT}/SHA256SUMS", "w") as f:
    for name in sorted(os.listdir(OUT)):
        if name != "SHA256SUMS" and not name.endswith(".part"):
            h = hashlib.sha256()
            with open(f"{OUT}/{name}", "rb") as data:
                for block in iter(lambda: data.read(1 << 20), b""):
                    h.update(block)
            f.write(f"{h.hexdigest()}  {name}\n")
print(f"4/4 Done: {counts} articles in {OUT}/")
