"""Dataset M — History of Finland, every version.

Every saved version of the ~19,750 Finnish Wikipedia articles on the history of Finland (the
Finnish articles of dataset S), from 2002 to 1 October 2026, with the full wikitext of each.

    python dataset_m.py                      # download what is missing, then build output/m/
    python dataset_m.py --dumps ~/wikidumps  # keep the 4.5 GB of dump files somewhere else

Needs 7-Zip's command-line program 7zz (Mac: brew install 7-zip; Linux: the 7zip package).
Safe to stop and run again: finished downloads and finished output files are kept.

Steps:
  1. Download the 16 pages-meta-history .7z files of the 1 October 2026 dump (4.5 GB) from
     dumps.wikimedia.org, resuming where a download stopped, checked against Wikimedia's SHA-1 sums.
  2. Stream each file through 7zz (nothing big is unpacked to disk), keep the listed articles,
     and write their versions to output/m/versions-<n>.parquet — one output file per dump file.
  3. Write output/m/README.md (what is in the files) and output/m/SHA256SUMS.
"""
import argparse, csv, hashlib, os, re, subprocess, sys, time
from datetime import datetime, timezone
import xml.etree.ElementTree as ET
import pyarrow as pa, pyarrow.parquet as pq
import requests

DUMP = "https://dumps.wikimedia.org/fiwiki/20261001/"
HEADERS = {"User-Agent": "mariadb-wikipedia-datasets/1.0 (https://github.com/robertsilen/mariadb-wikipedia-datasets)"}
OUT = "output/m"

parser = argparse.ArgumentParser()
parser.add_argument("--list", default="lists/s-articles-fi.tsv", help="the articles to keep")
parser.add_argument("--dumps", default="downloads/fiwiki-20261001", help="where the dump files go")
args = parser.parse_args()
os.makedirs(args.dumps, exist_ok=True)
os.makedirs(OUT, exist_ok=True)

# --- 1. Download -------------------------------------------------------------------------------
sums = {}
for line in requests.get(DUMP + "fiwiki-20261001-sha1sums.txt", headers=HEADERS, timeout=60).text.splitlines():
    checksum, name = line.split()
    if "pages-meta-history" in name and name.endswith(".7z"):
        sums[name] = checksum


def sha1(path):
    h = hashlib.sha1()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


for name in sorted(sums):
    path = os.path.join(args.dumps, name)
    done = os.path.getsize(path) if os.path.exists(path) else 0
    r = requests.get(DUMP + name, headers={**HEADERS, "Range": f"bytes={done}-"}, stream=True, timeout=60)
    if r.status_code != 416:                       # 416: already complete
        r.raise_for_status()
        with open(path, "ab" if r.status_code == 206 else "wb") as f:
            for chunk in r.iter_content(1 << 20):
                f.write(chunk)
        print(f"downloaded {name}", flush=True)
    r.close()
    if sha1(path) != sums[name]:
        sys.exit(f"Checksum does not match for {path}. Delete it and run again.")
print(f"{len(sums)} dump files present and checked", flush=True)

# --- 2. Extract the versions of our articles ---------------------------------------------------
wanted = {int(r["page_id"]) for r in csv.DictReader(open(args.list), delimiter="\t")}
ANONYMOUS = re.compile(r"^~\d{4}-")      # since 2025 anonymous editors get temporary accounts like ~2026-33532-4
SCHEMA = pa.schema([
    ("page_id", pa.uint32()), ("title", pa.string()), ("rev_id", pa.uint32()), ("parent_id", pa.uint32()),
    ("timestamp", pa.timestamp("s", tz="UTC")), ("user", pa.string()), ("anonymous", pa.bool_()),
    ("comment", pa.string()), ("minor", pa.bool_()), ("sha1", pa.string()), ("bytes", pa.uint32()),
    ("text", pa.string()),
])


def tag(element):
    return element.tag.rsplit("}", 1)[-1]


def extract(dump_path, out_path):
    """Stream one dump file and write the versions of the wanted articles, one article at a time."""
    tmp = out_path + ".part"
    writer = pq.ParquetWriter(tmp, SCHEMA, compression="zstd", compression_level=9)
    stream = subprocess.Popen(["7zz", "x", "-so", dump_path], stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    page_id, title, keep, rows = None, None, False, []
    articles = versions = 0
    for _, el in ET.iterparse(stream.stdout, events=("end",)):
        name = tag(el)
        if name == "title" and page_id is None:
            title = el.text
        elif name == "id" and page_id is None:
            page_id = int(el.text)                # the page's own id comes before its revisions
            keep = page_id in wanted
        elif name == "revision":
            if keep:
                f = {tag(c): c for c in el}
                who = {tag(c): c.text for c in f["contributor"]} if "contributor" in f else {}
                user = who.get("username") or who.get("ip")
                rows.append({
                    "page_id": page_id, "title": title, "rev_id": int(f["id"].text),
                    "parent_id": int(f["parentid"].text) if "parentid" in f else None,
                    "timestamp": datetime.strptime(f["timestamp"].text, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc),
                    "user": user, "anonymous": "ip" in who or bool(user and ANONYMOUS.match(user)),
                    "comment": f["comment"].text if "comment" in f else None, "minor": "minor" in f,
                    "sha1": f["sha1"].text if "sha1" in f else None,
                    "bytes": int(f["text"].get("bytes", 0)) if "text" in f else 0,
                    "text": (f["text"].text or "") if "text" in f else ""})
            el.clear()
        elif name == "page":
            if rows:
                rows.sort(key=lambda r: (r["timestamp"], r["rev_id"]))
                writer.write_table(pa.Table.from_pylist(rows, SCHEMA), row_group_size=2000)
                articles += 1
                versions += len(rows)
            page_id, title, keep, rows = None, None, False, []
            el.clear()
    stream.wait()
    writer.close()
    os.rename(tmp, out_path)
    return articles, versions


for n, name in enumerate(sorted(sums, key=lambda s: int(re.search(r"-p(\d+)p", s).group(1))), 1):
    out_path = os.path.join(OUT, f"versions-{n:02d}.parquet")
    if os.path.exists(out_path):
        print(f"{out_path}: already done", flush=True)
        continue
    start = time.time()
    a, v = extract(os.path.join(args.dumps, name), out_path)
    print(f"{out_path}: {a} articles, {v:,} versions, {os.path.getsize(out_path) / 1e6:,.0f} MB, "
          f"{time.time() - start:.0f} s  (from {name})", flush=True)

# --- 3. Data card and checksums ----------------------------------------------------------------
files = sorted(f for f in os.listdir(OUT) if f.endswith(".parquet"))
meta = [pq.ParquetFile(os.path.join(OUT, f)).metadata for f in files]
total_versions = sum(m.num_rows for m in meta)
total_mb = sum(os.path.getsize(os.path.join(OUT, f)) for f in files) / 1e6
with open(os.path.join(OUT, "README.md"), "w") as f:
    f.write(f"""# Dataset M — History of Finland, every version

Every saved version of the Finnish Wikipedia articles on the history of Finland, from the
`pages-meta-history` dump of 1 October 2026: {total_versions:,} versions in {len(files)} Parquet files,
{total_mb:,.0f} MB (zstd). Built by `dataset_m.py` in https://github.com/robertsilen/mariadb-wikipedia-datasets.

One row per version, oldest first within each article. Columns: page_id, title (as it was then),
rev_id, parent_id, timestamp (UTC, when the version was saved), user (name, IP address or
temporary account), anonymous (IP address or temporary account), comment (edit summary), minor,
sha1 (same hash = same text), bytes, text (wikitext).

Source: Wikimedia dumps, https://dumps.wikimedia.org/fiwiki/20261001/
Licence: Wikipedia text is CC BY-SA 4.0 (https://creativecommons.org/licenses/by-sa/4.0/).
Credit Wikipedia and its authors; each version's authors are listed on
https://fi.wikipedia.org/w/index.php?curid=<page_id>&action=history
""")
with open(os.path.join(OUT, "SHA256SUMS"), "w") as f:
    for name in files + ["README.md"]:
        h = hashlib.sha256()
        with open(os.path.join(OUT, name), "rb") as data:
            for block in iter(lambda: data.read(1 << 20), b""):
                h.update(block)
        f.write(f"{h.hexdigest()}  {name}\n")
print(f"Done: {total_versions:,} versions, {total_mb:,.0f} MB in {OUT}/")
