# MariaDB Wikipedia datasets

Three datasets from Finnish Wikipedia, for building on MariaDB — vector search and RAG, and
system-versioned tables. Each one can be **downloaded ready-made**, or **built from scratch** with
one Python script from Wikipedia's public sources.

Made for hackathons and courses: the same data for everyone, real Wikipedia ids so every result
links back to the live page, and nothing that needs an account or an API key.

> **Status:** all three are built, checked and published as this repo's GitHub releases.

## Which one?

- **Ask questions and get answers with sources (RAG, vector search)** → **S**
- **Show how articles changed over time (system-versioned tables)** → **M**
- **Show that your idea scales to a whole Wikipedia** → **L** (build on S first)

They are not the same data in three sizes — each is made for a different kind of project.

## At a glance

| | **S — History of Finland, now** | **M — History of Finland, every version** | **L — All of Finnish Wikipedia, now** |
|---|---|---|---|
| **Articles** | 19,285 fi · 4,921 sv · 5,254 en | 19,742 fi | 568,416 fi |
| **Versions** | the current one | every saved version, 2003 – 1 Oct 2026: **862,076** (12.2 GB of text) | the current one |
| **Text** | clean Markdown (from Wikipedia's rendered pages) | raw wikitext — the only form old versions exist in | clean Markdown |
| **Snapshot** | August 2025 (FineWiki) | 1 October 2026 (Wikimedia dumps) | August 2025 (FineWiki) |
| **Vectors** | **yes** — bge-m3, 1024 dimensions, one per article, all three languages | no | no — a GPU job |
| **Extras** | categories and the category tree | who saved each version, edit summary, text hash (reverts) | — |
| **Download** | 156 MB | 315 MB | 495 MB |
| **Build from scratch** | ~2 h — mostly finding 5,254 articles in all of English Wikipedia (38 GB); +5 h to compute the vectors yourself | ~25 min + downloading 4.5 GB: every version of every Finnish page | ~4 min — two files, taken whole |
| **Good for** | `VECTOR`, vector index, hybrid search, cross-language search | `WITH SYSTEM VERSIONING`, `FOR SYSTEM_TIME`, partitioning by time | scale: full-text search, vector indexes, partitioning |
| **Example on MariaDB** | [mariadb-vector-wikipedia-rag](https://github.com/MariaDB/mariadb-vector-wikipedia-rag) | [mariadb-system-versioning-wikipedia](https://github.com/MariaDB/mariadb-system-versioning-wikipedia) | — |

**Why the biggest builds fastest.** Build time depends on what has to be *read*, not on the size of
the result. L takes FineWiki's two Finnish files whole. S has to find its Swedish and English articles
— scattered across all of Swedish and English Wikipedia — so it reads nearly all of their text, and
Hugging Face's request limits add waits. M has to unpack the edit history of every Finnish page to
pick out ours.

**Two words that are easy to mix up.** *History of Finland* is the **topic** of the articles.
*Every version* is the **edit history** of an article. S has the topic; M has the topic *and* the
edit history; L has every topic, without history.

**Same columns everywhere.** S and L use the same columns, so code written on S runs on L. Every
row keeps Wikipedia's own `page_id` (and `rev_id`), so every result links back:
`https://fi.wikipedia.org/?curid=<page_id>` or, for one version, `?oldid=<rev_id>`.

---

## S — History of Finland, now

*Every Wikipedia article about the history of Finland, in Finnish, Swedish and English, with a
vector per article — ready for vector search.*

**What is in it**

| File | Rows | Columns |
|---|---|---|
| `articles-fi.parquet`, `-sv`, `-en` | 19,285 · 4,921 · 5,254 | `lang`, `page_id`, `rev_id`, `wikidata_id`, `title`, `url`, `date_modified`, `text` |
| `vectors-bge-m3-fi.parquet`, `-sv`, `-en` | one per article | `lang`, `page_id`, `model`, `truncated`, `embedding` (1024 × float32) |
| `category-links-fi.tsv` | 1,760 | `parent`, `child` — the category tree |
| `article-categories-fi.tsv` | 25,720 | `page_id`, `category` — the Finnish articles' categories |

- **Which articles:** every article in the Finnish category
  [Luokka:Suomen historia](https://fi.wikipedia.org/wiki/Luokka:Suomen_historia) and three levels of
  subcategories (19,296), plus the main article of each *named* category one level deeper (458):
  famous people and things have a category of their own with their article inside
  (*Luokka:Urho Kekkonen* → *Urho Kekkonen*). Without that step the presidents are missing; walking
  the whole fourth level instead would double the set with lists of politicians and councillors.
  Of the 19,754 listed, 19,285 are in FineWiki; the rest are newer than its snapshot.
- **Swedish and English:** the articles on the same topics — same `wikidata_id`. 2,919 topics exist
  in all three languages.
- **Text:** clean Markdown from [FineWiki](https://huggingface.co/datasets/HuggingFaceFW/finewiki),
  which Hugging Face made from Wikimedia's rendered pages: templates filled in, no wiki markup.
- **Vectors:** one per article, from title + text, with [BAAI/bge-m3](https://huggingface.co/BAAI/bge-m3)
  run by [Ollama](https://ollama.com/library/bge-m3) — multilingual, so an English question finds
  Finnish text. bge-m3 reads 8,192 tokens; 290 longer articles (1%) were cut (`truncated`).
  **Embed your questions with the same model.** Why this model and why one vector per article:
  the [RAG demo's README](https://github.com/MariaDB/mariadb-vector-wikipedia-rag#choices-and-why).

**Download ready-made:** the [`s-2026-10` release](https://github.com/MariaDB/mariadb-wikipedia-datasets/releases/tag/s-2026-10)
— 156 MB, with `SHA256SUMS`.

**Build it yourself:**

```bash
python dataset_s.py                   # text from FineWiki, plus the ready-made vectors
python dataset_s.py --vectors embed   # compute the vectors yourself (Ollama with bge-m3)
```

1. **The article list** is in [lists/](lists/) — walking the categories again
   (`--refresh-lists`, ~10 min with the Wikipedia API) gives a slightly different list as Wikipedia
   changes.
2. **The text:** FineWiki stores each language as Parquet files (Finnish 1.65 GB, Swedish 3.1 GB,
   English 37.7 GB). DuckDB reads only the columns needed, straight from Hugging Face — never the
   raw wikitext, about two thirds of each file. Measured: Finnish ~4 min, Swedish ~30 min, English
   ~80 min on a 3 MB/s line. Hugging Face limits anonymous requests; the script waits 5 minutes and
   continues when it says "too many requests".
3. **The vectors:** downloaded (119 MB), or `--vectors embed`: ~100 articles a minute on a MacBook
   Air M4, ~5 hours. Ollama needs both `num_ctx` and `num_batch` set to 8192 — with only the first,
   anything over 2,048 tokens is refused or cut.

**Use it with MariaDB:** the [RAG demo](https://github.com/MariaDB/mariadb-vector-wikipedia-rag)
loads exactly these files (`load.py --vectors`) into a table with a `VECTOR(1024)` column and a
vector index, with search page, evaluation and measured index settings.

---

## M — History of Finland, every version

*Every saved version of every Finnish article in S, with who saved it, when and why — the raw
material for "what did this article say on 3 April 2016?"*

**What is in it**

| File | Rows | Columns |
|---|---|---|
| `versions-01.parquet` … `versions-16.parquet` | 862,076 versions of 19,742 articles | `page_id`, `title`, `rev_id`, `parent_id`, `timestamp` (UTC), `user`, `anonymous`, `comment`, `minor`, `sha1`, `bytes`, `text` |

- **Which articles:** the Finnish articles of S (the same list). 19,742 of the 19,754 listed are in
  the dumps; the other 12 were deleted or renamed after the list was made. S has fewer (19,285)
  because FineWiki's snapshot is older.
- **Which versions:** all of them, from the first in 2002 to 1 October 2026, oldest first per article.
  One output file per dump file.
- **Text:** raw wikitext. Wikipedia keeps old versions only as wikitext — no clean Markdown exists
  for them.
- **`anonymous`:** 19% of the versions. Saved by an IP address — or, since 2025, by a temporary account named like
  `~2026-33532-4`, when Wikipedia stopped publishing anonymous editors' IP addresses.
- **`sha1`:** the same hash means the same text. A version whose text equals an earlier one, but
  not the one just before it, is a **revert**.
- **Not in it:** versions an administrator has hidden; the dumps carry only what anyone may see.

**Download ready-made:** the [`m-2026-10` release](https://github.com/MariaDB/mariadb-wikipedia-datasets/releases/tag/m-2026-10)
— 16 files, 315 MB in all, with `SHA256SUMS`. 12.2 GB of wikitext compress this well because
consecutive versions of an article are nearly identical, and zstd sees them side by side.

**Build it yourself:**

```bash
python dataset_m.py                       # needs 7-Zip's 7zz: brew install 7-zip
python dataset_m.py --dumps ~/wikidumps   # keep the 4.5 GB of dump files elsewhere
```

1. **Download** the 16 `pages-meta-history*.7z` files of the
   [1 October 2026 dump](https://dumps.wikimedia.org/fiwiki/20261001/) — 4.5 GB, every version of
   every page on Finnish Wikipedia. Resumes; checked against Wikimedia's SHA-1 sums. ~35 min at
   1.6 MB/s.
2. **Stream** each file through 7-Zip and keep the listed articles — nothing big is unpacked to
   disk. 1–2.5 minutes per file, ~21 minutes for all 16 on a MacBook Air M4.

**Use it with MariaDB:** the [time machine demo](https://github.com/MariaDB/mariadb-system-versioning-wikipedia)
writes versions like these into a system-versioned table with Wikipedia's real edit times
(`system_versioning_insert_history`), measured and explained — for 50 articles. Its loader reads
the dump files directly; loading these Parquet files instead is the same few lines *(to do there)*.
In a MariaDB table the text takes far more room than in Parquet: the demo's 2 GB of text for 50
articles took 1.2 GB with `COMPRESSED` columns — plan ~5–7 GB for all of M.

---

## L — All of Finnish Wikipedia, now

*Every Finnish Wikipedia article, current text — for showing that an idea scales.*

**What is in it**

| File | Rows | Columns |
|---|---|---|
| `articles-fi.parquet` | 568,416 | the same as S: `lang`, `page_id`, `rev_id`, `wikidata_id`, `title`, `url`, `date_modified`, `text` |

- **Text:** clean Markdown from FineWiki, August 2025 — the same kind of text as S: 1.4 GB.
  All 19,285 Finnish articles of S are in L, in the same version.
- **No vectors.** At ~100 articles a minute on a laptop, embedding all of it would take days —
  a GPU job, or a sample. Ready-made vectors at this scale exist from
  [Cohere](https://huggingface.co/datasets/CohereLabs/wikipedia-2023-11-embed-multilingual-v3)
  (2.4 million paragraphs of Finnish Wikipedia), but from November 2023, and searching them needs a
  Cohere API key.
- **No categories.** FineWiki has none; Wikipedia's category tables are in the
  [SQL dumps](https://dumps.wikimedia.org/fiwiki/20261001/) (`categorylinks`, `linktarget`, `page`)
  if you need them.

**Download ready-made:** the [`l-2026-10` release](https://github.com/MariaDB/mariadb-wikipedia-datasets/releases/tag/l-2026-10)
— one file, 495 MB, with `SHA256SUMS`.

**Build it yourself:**

```bash
python dataset_l.py
```

Reads FineWiki's two Finnish files (1.65 GB) with DuckDB, only the columns needed: ~2 minutes
each on a 3 MB/s line. FineWiki has some articles twice (572,900 rows); the script keeps the newest
version of each.

---

## Before you start

```bash
git clone https://github.com/MariaDB/mariadb-wikipedia-datasets.git
cd mariadb-wikipedia-datasets
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt       # duckdb, pyarrow, requests
```

| | S | M | L |
|---|---|---|---|
| Python 3.11+ | ✓ | ✓ | ✓ |
| Free disk | ~0.5 GB | 0.3 GB + 4.5 GB of dumps | ~1 GB |
| Other | Ollama + bge-m3, only for `--vectors embed` | 7-Zip (`7zz`) | — |
| Accounts / API keys | none | none | none |

Every script is safe to stop and run again: finished downloads and finished steps are kept
(`downloads/`, `cache/`, `output/`). Each output folder gets a `README.md` describing its files
and a `SHA256SUMS`, so the folder can be shared as it is.

## Licence and credit

Wikipedia's text is under [CC BY-SA 4.0](https://creativecommons.org/licenses/by-sa/4.0/): you may
use and share it, including these files, if you credit Wikipedia and its authors and share what you
make from the text under the same licence. Every row has the article's `url` or `page_id` for the
link. Sources: [FineWiki](https://huggingface.co/datasets/HuggingFaceFW/finewiki) (Hugging Face),
[Wikimedia dumps](https://dumps.wikimedia.org/), the Finnish Wikipedia API. The vectors in S are made
from the text, so they carry the same licence.

The scripts and documentation in this repository are under the [MIT licence](LICENSE): use them
freely, in open or closed projects, keeping the licence text.

## Authors

Robert Silén, [MariaDB Foundation](https://mariadb.org/), October 2026 — built with
[Claude Code](https://claude.com/claude-code).
