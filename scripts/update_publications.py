#!/usr/bin/env python3

import json
import re
from pathlib import Path
from urllib.parse import urlencode
from urllib.request import Request, urlopen

AUTHOR_RECID = 1040066
MAX_AUTHORS = 20

API = "https://inspirehep.net/api"
USER_AGENT = "asengupta74-publications/2.0"

COLLAB_FILE = Path("historical_collaboration_papers.json")
EXTRA_FILE = Path("extra_research_papers.json")
POPULAR_FILE = Path("popular_articles.json")
PROCEEDINGS_FILE = Path("conference_proceedings.json")
REPORTS_FILE = Path("technical_reports.json")

OUTPUT_FILE = Path("publications_body.qmd")


def load_json(path):
    return json.loads(path.read_text())


def get_json(url):
    req = Request(
        url,
        headers={
            "User-Agent": USER_AGENT,
            "Accept": "application/json",
        },
    )
    with urlopen(req, timeout=60) as response:
        return json.load(response)


def search_literature(query, size=250):
    out = []
    page = 1

    while True:
        params = urlencode({
            "q": query,
            "sort": "mostrecent",
            "size": size,
            "page": page,
        })

        data = get_json(f"{API}/literature?{params}")
        hits = data.get("hits", {}).get("hits", [])

        out.extend(hits)

        total = data.get("hits", {}).get("total", 0)
        if isinstance(total, dict):
            total = total.get("value", 0)

        if not hits or len(out) >= total:
            break

        page += 1

    return out



def search_once(query, size=25):
    """Return only the first page of INSPIRE results.

    This is deliberately non-paginating and is used for record lookup,
    where downloading every match to a broad text query would be wasteful.
    """
    params = urlencode({
        "q": query,
        "sort": "mostrecent",
        "size": size,
        "page": 1,
    })

    data = get_json(f"{API}/literature?{params}")

    return data.get("hits", {}).get("hits", [])


def fetch_by_arxiv(arxiv):
    hits = search_once(
        f'arxiv_eprints.value:"{arxiv}"',
        size=10,
    )

    for hit in hits:
        metadata = hit.get("metadata", {})
        values = [
            x.get("value")
            for x in metadata.get("arxiv_eprints", [])
        ]

        if arxiv in values:
            return hit

    return hits[0] if hits else None


def get_author_bai():
    data = get_json(f"{API}/authors/{AUTHOR_RECID}")
    metadata = data.get("metadata", {})

    for ident in metadata.get("ids", []):
        schema = str(ident.get("schema", "")).lower()
        if "bai" in schema:
            return ident.get("value")

    return None


def get_small_author_papers():
    bai = get_author_bai()

    if bai:
        hits = search_literature(
            f"a {bai} and ac 1->{MAX_AUTHORS}"
        )
        if hits:
            print(f"Using INSPIRE BAI: {bai}")
            return hits

    hits = search_literature(
        f"authors.record.$ref:{AUTHOR_RECID} and ac 1->{MAX_AUTHORS}"
    )

    if not hits:
        raise RuntimeError(
            "INSPIRE returned no publications for Anand Sengupta."
        )

    return hits


def normalize_title(title):
    return re.sub(
        r"[^a-z0-9]+",
        " ",
        title.lower()
    ).strip()


def title_of(metadata):
    titles = metadata.get("titles", [])
    if not titles:
        return "Untitled"

    return titles[0].get("title", "Untitled").strip()


def fetch_by_title(title):
    """Bounded fallback lookup for older records without arXiv IDs."""
    target = normalize_title(title)

    # Exact INSPIRE title query first.
    hits = search_once(
        f'title:"{title}"',
        size=25,
    )

    for hit in hits:
        candidate = title_of(hit.get("metadata", {}))
        if normalize_title(candidate) == target:
            return hit

    # One bounded free-text page as a fallback.
    hits = search_once(title, size=25)

    if not hits:
        return None

    # Prefer the closest normalized title.
    from difflib import SequenceMatcher

    scored = []

    for hit in hits:
        candidate = title_of(hit.get("metadata", {}))
        score = SequenceMatcher(
            None,
            target,
            normalize_title(candidate)
        ).ratio()
        scored.append((score, hit))

    scored.sort(
        key=lambda x: x[0],
        reverse=True
    )

    # Avoid silently attaching a completely different paper.
    if scored and scored[0][0] >= 0.72:
        return scored[0][1]

    return None



def author_name(full_name):
    if not full_name:
        return ""

    if "," in full_name:
        family, given = full_name.split(",", 1)
        return f"{given.strip()} {family.strip()}"

    return full_name.strip()


def authors_of(metadata):
    authors = metadata.get("authors", [])
    count = metadata.get("author_count", len(authors))

    if count > MAX_AUTHORS:
        collaborations = [
            c.get("value")
            for c in metadata.get("collaborations", [])
            if c.get("value")
        ]

        if collaborations:
            return ", ".join(collaborations)

        if authors:
            return f"{author_name(authors[0].get('full_name', ''))} et al."

        return "Collaboration paper"

    names = []

    for author in authors:
        name = author_name(author.get("full_name", ""))

        low = name.lower()
        if "sengupta" in low and "anand" in low:
            name = f"**{name}**"

        if name:
            names.append(name)

    return ", ".join(names)


def year_of(metadata):
    for pub in metadata.get("publication_info", []):
        year = pub.get("year")
        if year:
            return int(year)

    for key in ("preprint_date", "earliest_date"):
        value = metadata.get(key)
        if value:
            m = re.match(r"(\d{4})", str(value))
            if m:
                return int(m.group(1))

    return 0


def date_key(metadata):
    for key in ("earliest_date", "preprint_date"):
        value = metadata.get(key)
        if value:
            return str(value)

    return f"{year_of(metadata):04d}-00-00"


def journal_of(metadata):
    pubs = metadata.get("publication_info", [])
    if not pubs:
        return ""

    pub = pubs[0]

    journal = pub.get("journal_title", "")
    volume = pub.get("journal_volume", "")
    article = pub.get("artid") or pub.get("page_start") or ""
    year = pub.get("year", "")

    pieces = []

    if journal:
        pieces.append(journal)

    if volume:
        pieces.append(str(volume))

    result = " ".join(pieces)

    if article:
        result += f", {article}"

    if year:
        result += f" ({year})"

    return result.strip()


def arxiv_of(metadata):
    entries = metadata.get("arxiv_eprints", [])
    if entries:
        return entries[0].get("value")
    return None


def doi_of(metadata):
    entries = metadata.get("dois", [])
    if entries:
        return entries[0].get("value")
    return None


def render_inspire(record, note=None):
    metadata = record.get("metadata", {})
    recid = record.get("id")

    title = title_of(metadata)
    authors = authors_of(metadata)
    journal = journal_of(metadata)
    arxiv = arxiv_of(metadata)
    doi = doi_of(metadata)

    url = f"https://inspirehep.net/literature/{recid}"

    lines = [
        f"1. **[{title}]({url})**  ",
        f"   {authors}  ",
    ]

    refs = []

    if journal:
        refs.append(journal)

    if arxiv:
        refs.append(
            f"[arXiv:{arxiv}](https://arxiv.org/abs/{arxiv})"
        )

    if doi:
        refs.append(
            f"[DOI](https://doi.org/{doi})"
        )

    if refs:
        lines.append("   " + " · ".join(refs) + "  ")

    if note:
        lines.append(f"   *Note:* {note}  ")

    lines.append("")

    return "\n".join(lines)


def render_static(item):
    title = item["title"]
    url = item.get("url")

    if url:
        title_line = f"**[{title}]({url})**"
    else:
        title_line = f"**{title}**"

    lines = [f"- {title_line}  "]

    authors = item.get("authors")
    if authors:
        lines.append(f"  {authors}  ")

    info = []

    publication = item.get("publication") or item.get("journal")
    if publication:
        info.append(publication)

    date = item.get("date")
    if date:
        info.append(date)

    year = item.get("year")
    if year and not date:
        info.append(str(year))

    document = item.get("document")
    if document:
        info.append(document)

    doi = item.get("doi")
    if doi:
        info.append(f"[DOI](https://doi.org/{doi})")

    arxiv = item.get("arxiv")
    if arxiv:
        info.append(
            f"[arXiv:{arxiv}](https://arxiv.org/abs/{arxiv})"
        )

    if info:
        lines.append("  " + " · ".join(info) + "  ")

    lines.append("")
    return "\n".join(lines)


def main():
    selected = {}

    # All INSPIRE papers with <=20 authors.
    for record in get_small_author_papers():
        key = normalize_title(title_of(record.get("metadata", {})))
        selected[key] = {
            "record": record,
            "note": None,
        }

    # Historical large-collaboration papers from Anand's curated list.
    for item in load_json(COLLAB_FILE):
        if item.get("arxiv"):
            record = fetch_by_arxiv(item["arxiv"])
        else:
            record = fetch_by_title(item["title"])

        if record is None:
            print(
                "WARNING: INSPIRE match not found for:",
                item["title"]
            )
            continue

        key = normalize_title(title_of(record.get("metadata", {})))

        selected[key] = {
            "record": record,
            "note": item.get("note"),
        }

    # Static research papers that may not reliably appear on INSPIRE.
    extra = load_json(EXTRA_FILE)

    # Remove static extras if INSPIRE already supplied the same title.
    extra = [
        item for item in extra
        if normalize_title(item["title"]) not in selected
    ]

    papers = list(selected.values())

    # Build one chronological stream containing both INSPIRE records and
    # static small-author papers.  The primary sort key is the publication
    # year, so year headings cannot repeat or appear out of order.
    entries = []

    for item in papers:
        metadata = item["record"].get("metadata", {})
        year = year_of(metadata)

        entries.append({
            "kind": "inspire",
            "year": year,
            "sort_date": date_key(metadata),
            "payload": item,
        })

    for item in extra:
        year = int(item.get("year", 0))

        entries.append({
            "kind": "static",
            "year": year,
            "sort_date": f"{year:04d}-00-00",
            "payload": item,
        })

    entries.sort(
        key=lambda x: (
            x["year"],
            x["sort_date"],
        ),
        reverse=True,
    )

    out = [
        "## Research papers",
        "",
    ]

    current_year = None

    for entry in entries:
        year = entry["year"]

        if year != current_year:
            current_year = year
            out.extend([
                f"### {year if year else 'Undated'}",
                "",
            ])

        if entry["kind"] == "inspire":
            item = entry["payload"]

            out.append(
                render_inspire(
                    item["record"],
                    item["note"],
                )
            )

        else:
            out.append(
                render_static(entry["payload"])
            )

    out.extend([
        "## Popular articles",
        "",
    ])

    for item in load_json(POPULAR_FILE):
        out.append(render_static(item))

    out.extend([
        "## Conference proceedings",
        "",
    ])

    for item in sorted(
        load_json(PROCEEDINGS_FILE),
        key=lambda x: x.get("year", 0),
        reverse=True,
    ):
        out.append(render_static(item))

    out.extend([
        "## Technical reports",
        "",
    ])

    for item in sorted(
        load_json(REPORTS_FILE),
        key=lambda x: x.get("year", 0),
        reverse=True,
    ):
        out.append(render_static(item))

    content = "\n".join(out).rstrip() + "\n"

    old = (
        OUTPUT_FILE.read_text()
        if OUTPUT_FILE.exists()
        else None
    )

    if old == content:
        print("publications.qmd is already up to date")
        return

    OUTPUT_FILE.write_text(content)

    print(
        f"Wrote {OUTPUT_FILE}: "
        f"{len(papers)} INSPIRE research papers, "
        f"{len(extra)} static research papers"
    )


if __name__ == "__main__":
    main()
