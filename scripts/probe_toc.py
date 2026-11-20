"""Test only: can each book's table of contents be read without AI?

For every lesson book (Arabic-medium PDF) on the ministry site, reports:
- the PDF's own bookmarks (outline): how many entries, how deep, and the first titles;
- the text of the contents page (found in the first pages), read two ways (PyMuPDF and pypdf),
  with simple checks on whether the Arabic comes out readable.
Writes probe/report.json and probe/report.md. Changes nothing else in the repository.
"""
import io
import json
import re
import sys
import unicodedata
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import fitz  # PyMuPDF
import requests
from pypdf import PdfReader

sys.path.insert(0, str(Path(__file__).resolve().parent))
from update import UA, fetch_books, lesson_books  # noqa: E402

OUT = Path(__file__).resolve().parent.parent / "probe"
SCAN_PAGES = 20
TOC_WORDS = ["المحتويات", "الفهرس", "فهرس", "المحتوى", "Contents", "CONTENTS"]
TOC_WORDS_REV = [w[::-1] for w in TOC_WORDS if re.search(r"[؀-ۿ]", w)]
UNIT_WORDS = ["الوحدة", "الدرس", "الفصل", "المحور", "Unit", "Lesson"]


def text_quality(t):
    letters = [c for c in t if c.isalpha()]
    if not letters:
        return {"chars": 0}
    arabic = sum(1 for c in letters if "؀" <= c <= "ۿ")
    shaped = sum(1 for c in letters if "ﭐ" <= c <= "﻿")
    nfkc = unicodedata.normalize("NFKC", t)
    return {
        "chars": len(t),
        "arabic_ratio": round(arabic / len(letters), 2),
        "presentation_forms_ratio": round(shaped / len(letters), 2),
        "unit_words": sum(nfkc.count(w) for w in UNIT_WORDS),
        "unit_words_reversed": sum(nfkc.count(w[::-1]) for w in UNIT_WORDS if re.search(r"[؀-ۿ]", w)),
    }


def outline_entries(reader):
    out = []

    def walk(items, depth):
        for it in items:
            if isinstance(it, list):
                walk(it, depth + 1)
                continue
            try:
                page = reader.get_destination_page_number(it) + 1
            except Exception:
                page = None
            out.append({"depth": depth, "title": str(it.title).strip(), "page": page})

    try:
        walk(reader.outline, 0)
    except Exception as e:
        return [], f"outline error: {e}"
    return out, None


def find_toc_page(texts):
    for i, t in enumerate(texts):
        n = unicodedata.normalize("NFKC", t)
        if any(w in n for w in TOC_WORDS) or any(w in n for w in TOC_WORDS_REV):
            return i
    return None


def probe(b):
    r = {k: b[k] for k in ("subject", "grade", "term", "title", "link")}
    try:
        resp = requests.get(b["link"], headers=UA, timeout=600)
        resp.raise_for_status()
        data = resp.content
        r["mb"] = round(len(data) / 1048576, 1)
        reader = PdfReader(io.BytesIO(data))
        r["pages"] = len(reader.pages)
        entries, err = outline_entries(reader)
        r["outline_count"] = len(entries)
        r["outline_max_depth"] = max((e["depth"] for e in entries), default=None)
        r["outline_sample"] = entries[:25]
        if err:
            r["outline_error"] = err
        doc = fitz.open(stream=data, filetype="pdf")
        n = min(SCAN_PAGES, doc.page_count)
        mu = [doc[i].get_text() for i in range(n)]
        pp = []
        for i in range(n):
            try:
                pp.append(reader.pages[i].extract_text() or "")
            except Exception:
                pp.append("")
        r["image_only_pages"] = sum(1 for t in mu if len(t.strip()) < 20)
        idx = find_toc_page(mu)
        if idx is None:
            idx = find_toc_page(pp)
        r["toc_page"] = None if idx is None else idx + 1
        if idx is not None:
            # the contents can run over two pages
            mt = mu[idx] + ("\n" + mu[idx + 1] if idx + 1 < n else "")
            pt = pp[idx] + ("\n" + pp[idx + 1] if idx + 1 < n else "")
            r["pymupdf"] = text_quality(mt)
            r["pypdf"] = text_quality(pt)
            r["pymupdf_text"] = mt[:1500]
            r["pypdf_text"] = pt[:800]
    except Exception as e:
        r["error"] = f"{type(e).__name__}: {e}"
    print(f"{r['subject']} {r['grade']} t{r['term']} {r['title']}: outline={r.get('outline_count')} toc_page={r.get('toc_page')} {r.get('error', '')}", flush=True)
    return r


def verdict(r):
    if r.get("error"):
        return "error"
    if r.get("outline_count", 0) >= 5:
        return "bookmarks"
    q = r.get("pymupdf") or {}
    if q.get("chars", 0) > 100 and q.get("arabic_ratio", 0) > 0.5 and q.get("unit_words", 0) >= 2:
        return "text"
    if q.get("unit_words_reversed", 0) >= 2:
        return "text-reversed"
    if r.get("toc_page") is None and r.get("image_only_pages", 0) >= SCAN_PAGES // 2:
        return "images"
    return "unclear"


def main():
    books = []
    all_books = fetch_books()
    for s in sorted({b["subject"] for b in all_books}):
        books += lesson_books(all_books, s)
    print(f"{len(books)} lesson books", flush=True)
    with ThreadPoolExecutor(max_workers=6) as ex:
        results = list(ex.map(probe, books))
    for r in results:
        r["verdict"] = verdict(r)
    OUT.mkdir(exist_ok=True)
    (OUT / "report.json").write_text(json.dumps(results, ensure_ascii=False, indent=1), encoding="utf-8")

    counts = {}
    for r in results:
        counts[r["verdict"]] = counts.get(r["verdict"], 0) + 1
    lines = ["# Contents test", "", f"{len(results)} books", ""]
    lines += [f"- {k}: {v}" for k, v in sorted(counts.items(), key=lambda kv: -kv[1])]
    lines += ["", "| subject | grade | term | book | verdict | outline | toc page | pages | MB |", "|---|---|---|---|---|---|---|---|---|"]
    for r in sorted(results, key=lambda r: (r["subject"], r["grade"], r["term"])):
        lines.append(f"| {r['subject']} | {r['grade']} | {r['term']} | {r['title']} | {r['verdict']} | {r.get('outline_count', '')} | {r.get('toc_page', '')} | {r.get('pages', '')} | {r.get('mb', '')} |")
    (OUT / "report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n".join(lines[:12]))


if __name__ == "__main__":
    main()
