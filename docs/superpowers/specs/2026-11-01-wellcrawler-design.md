# wellcrawler — design

Date: 2026-11-01
Status: approved for implementation (autonomous run; user's direct request was "create a system based on this idea and commit to GitHub")

## The idea (source)

From a public LinkedIn post by NthDS (https://lnkd.in/p/ez_4ftYN):

> "There's a generic folder called temp(14) in your shared drive. It contains checkshots, core, well logs for a basin you are exploring...but you have no idea that data is in there because it's 'temp(14)'. That can be costly!
> Drop it into Crawler and the files come back sorted, the records standardized by well. Pick wells in NDepth, or ask your AI assistant in plain English, and the cross-section draws itself: reported shows, formation tops and LAS curves at depth, side by side.
> Every top is one click from its source page, so you can check the depth yourself."

This repo is an independent, open-source implementation of that *concept* (crawl messy archives → standardize by well → auto cross-section → provenance everywhere → plain-English queries). It is not affiliated with or endorsed by NthDS; their product (Crawler/NDepth) remains their own.

## Goals

1. **Discover**: crawl any messy directory tree and classify subsurface files by *content*, not extension or folder name.
2. **Standardize**: parse each recognized file type into depth-tied records, organized by well (merging aliases).
3. **Correlate**: render a cross-section (formation tops, LAS curve tracks, reported shows, core intervals) as a standalone SVG, wells side by side.
4. **Provenance**: every extracted data point carries its source file (+ page/line where applicable) and is one click away in the rendered output.
5. **Ask**: answer plain-English questions about the archive from the CLI, deterministically, with no external API.

## Non-goals (v1)

- OCR of scanned pages / handwritten notes (the post's product does this; we define a clean seam — text page extraction — where OCR can plug in later).
- A web UI/server, multi-user access, auth.
- Geographically positioned cross-sections (equal spacing, clearly labelled as such).
- LAS 3.0 / WRAP=YES / DLIS / SEG-Y support.

## Approaches considered

**A. Web platform (FastAPI + React + Postgres + LLM assistant).** Closest to the demoed product; but heavy to host, dependency-laden, and the core value (classification, standardization, provenance) gets buried under plumbing.

**B. Python package, stdlib-only, SQLite catalog, CLI, hand-rolled SVG cross-sections. (CHOSEN)** Runs anywhere Python ≥3.10 runs; zero install friction; SQLite keeps the catalog queryable by anything; SVG renders in any browser *and* supports native hyperlinks — so "every top is one click from its source page" is literally implemented as a clickable link per marker. Deterministic → easy tests + CI.

**C. Notebook/Streamlit app.** Fast visuals, but couples the engine to a framework and still needs everything in B underneath.

## Architecture

```
messy directory ──▶ crawl ──▶ classify (content sniffing)
                              │
                              ├─ las.py         → curves, well header, well id
                              ├─ checkshot.py   → depth/time pairs
                              ├─ core_desc.py   → depth-tied core intervals
                              ├─ tops_csv.py    → formation tops
                              ├─ report.py      → paginated text: tops, shows,
                              │                   mud-weight/other observations
                              └─ unknown        → recorded, skipped, reported
                              ▼
                        standardize (well identity: normalize names, merge
                        aliases, prefer LAS WELL/UWI header)
                              ▼
                     catalog.db (SQLite)  ── every row keeps provenance:
                                           source path + page + line + excerpt
                              ▼
              consume:  wells / tops / shows / well detail / sources
                        ask    (plain-English, pattern-based)
                        cross-section (SVG, clickable provenance links)
```

## Modules (each with one job)

| Module | Does | Depends on |
|---|---|---|
| `classify.py` | sniff a file → `FileKind` (las, checkshot, core_desc, tops_csv, report, unknown) + confidence | stdlib only |
| `las.py` | minimal LAS 1.2/2.0 parser: well header, curve definitions, ~A samples (NULL→None), WRAP detection, duplicate-mnemonic dedupe | stdlib |
| `tables.py` | tolerant CSV parsers (fuzzy headers): checkshot points, core intervals, tops rows | stdlib (`csv`) |
| `report.py` | split text on form-feed pages; regex-extract tops, shows, mud-weight observations; page/line provenance per record | stdlib |
| `registry.py` | well identity: normalize (`[A-Z0-9]` fold), UWI detection, alias merging, display-name choice | stdlib |
| `catalog.py` | SQLite schema + insert/query helpers; idempotent re-crawl (sha1 skip); failed-file ledger | stdlib (`sqlite3`) |
| `crawl.py` | orchestrates classify → extract → standardize → catalog; prints a summary report | the above |
| `query.py` | plain-English intents over the catalog (list wells, wells with shows, tops for well X, where is formation F, data availability, build cross-section) | `catalog.py` |
| `xsec.py` | SVG cross-section renderer: shared depth axis, per-well strip panel (curve track, tops, show bands, core column), legend, source footer; every top/show is an `<a href="file://…">` | `catalog.py`, `las.py` |
| `__main__.py` | argparse CLI wiring all verbs | all |

## Data model (SQLite)

- `sources(id, root_relative_path, kind, size, mtime, sha1, status, error)` — every file seen, including failures/unknowns.
- `wells(well_key, display_name, uwi)` + `well_aliases(well_key, alias)`.
- `tops(id, well_key, formation, depth_md, unit, source_id, page, line_no, excerpt)`.
- `shows(id, well_key, top_md, base_md, fluid, description, source_id, page, line_no)`.
- `observations(id, well_key, kind, depth_md, text, source_id, page, line_no)` — mud weights and misc depth-tied notes.
- `core_intervals(id, well_key, top_md, base_md, lithology, porosity, permeability, description, source_id, line_no)`.
- `curves(id, well_key, mnemonic, units, n_samples, min_value, max_value, source_id)` — summary only; renderer re-reads samples from the source LAS.
- `checkshots(id, well_key, n_points, md_min, md_max, twt_min, twt_max, source_id)`.

## Key decisions

- **Content sniffing over extensions**: files in `temp(14)` are misnamed by definition. First bytes / header rows decide the kind; extension is only a weak hint.
- **Well identity by normalized key**: `norm(name) = uppercase, non-alphanumerics stripped`. LAS `WELL`/`UWI` headers are authoritative when present; filename tokens (noise words like `final`, `export`, `v2`, `copy` removed) are the fallback. Same normalized key ⇒ same well; all variants kept as aliases; most human-readable variant wins display name.
- **Provenance is a first-class column set** (`source_id, page, line_no, excerpt`), not an afterthought — surfaced in every CLI listing and as clickable `file://` links in the SVG.
- **Idempotent crawling**: re-crawling a tree skips files whose (sha1, size) is unchanged; deletions are detected by path scan.
- **Fail loudly but continue**: a corrupt file is recorded with `status=failed` + error; the crawl never aborts on one bad file.
- **Deterministic plain-English `ask`**: regex intent matchers with an honest fallback message. No LLM, no network, no keys.

## Error handling

- Unknown/corrupt files never stop a crawl; they land in `sources` with `status` and `error`, and the summary reports them.
- Parsers return `(records, warnings)`; warnings ride along into the source row.
- CLI verbs exit non-zero on hard errors (bad path, unknown well), zero otherwise.

## Testing

- Unit tests per module (classifier sniffing, LAS parse incl. NULL/WRAP, report page/line provenance, alias merging, query intents, SVG structure).
- End-to-end test: fixture `temp(14)`-style tree (messy names, junk files, one corrupt LAS) → crawl → query → render; assert catalog contents, provenance links, and that junk was recorded as unknown.
- CI: GitHub Actions, Python 3.10–3.14 matrix, `python -m pytest`.

## Repository layout

```
wellcrawler/
├── README.md  LICENSE (MIT)  pyproject.toml  .gitignore
├── .github/workflows/ci.yml
├── docs/superpowers/specs/2026-11-01-wellcrawler-design.md
├── src/wellcrawler/… (modules above)
├── examples/temp(14)/…   ← committed messy demo tree (3 wells)
├── examples/xsec-demo.svg ← generated example output, committed for the README
└── tests/…
```
