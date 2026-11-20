"""Renders the contents pages of books still missing from toc-cache.json, for reading by eye.

    python scripts/render_toc.py [subject|all]

Two kinds of image per book, chosen by toc-pages.json ({link: [page numbers]}):
- "pages": the listed contents pages at a readable size, stacked;
- "sheet": the first 12 pages as small numbered thumbnails, to find where the contents are
  (the found pages then go into toc-pages.json and the book is rendered again as "pages").
Writes toc-images/*.jpg and toc-images/manifest.json. Nothing is committed: the workflow uploads
the folder as an artifact, because the repository is public and holds no book images.
"""
import io
import json
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from update import ROOT, UA, fetch_books, lesson_books  # noqa: E402

OUT = ROOT / "toc-images"
SHEET_PAGES = list(range(1, 13))


def plan_jobs(books, cache, toc_pages, subject):
    """Books of the subject (or "all") that are not in the cache yet, with what to render."""
    jobs = []
    for b in books:
        if subject != "all" and b["subject"] != subject:
            continue
        if b["link"] in cache:
            continue
        pages = toc_pages.get(b["link"])
        n = len(jobs) + 1
        jobs.append({**{k: b[k] for k in ("link", "subject", "grade", "term", "title")},
                     "mode": "pages" if pages else "sheet",
                     "pages": pages or SHEET_PAGES,
                     "file": f"{n:03d}_{b['grade']}_t{b['term']}.jpg"})
    return jobs


def download(job):
    import requests

    r = requests.get(job["link"], headers=UA, timeout=600)
    r.raise_for_status()
    return r.content


def render(job, data):
    """Called on one thread only: PyMuPDF crashes when used from several threads at once."""
    import pymupdf as fitz
    from PIL import Image, ImageDraw

    doc = fitz.open(stream=data, filetype="pdf")
    pages = [p for p in job["pages"] if 1 <= p <= doc.page_count]
    sheet = job["mode"] == "sheet"
    images = []
    for p in pages:
        pix = doc[p - 1].get_pixmap(dpi=45 if sheet else 110, colorspace=fitz.csGRAY)
        img = Image.open(io.BytesIO(pix.tobytes("png"))).convert("L")
        if sheet:
            ImageDraw.Draw(img).rectangle([0, 0, 44, 26], fill=255)
            ImageDraw.Draw(img).text((6, 4), str(p), fill=0, font_size=20)
        images.append(img)
    if sheet:  # 4 columns x 3 rows
        w, h = max(i.width for i in images), max(i.height for i in images)
        out = Image.new("L", (w * 4, h * ((len(images) + 3) // 4)), 255)
        for i, img in enumerate(images):
            out.paste(img, ((3 - i % 4) * w, (i // 4) * h))  # right to left, like the book
    else:
        out = Image.new("L", (max(i.width for i in images), sum(i.height for i in images)), 255)
        y = 0
        for img in images:
            out.paste(img, (0, y))
            y += img.height
    out.save(OUT / job["file"], "JPEG", quality=75)
    job["pages"] = pages
    print(f"{job['file']} {job['subject']} {job['grade']} t{job['term']} {job['mode']} {pages}", flush=True)
    return job


def main():
    subject = (sys.argv[1] if len(sys.argv) > 1 else "all").strip() or "all"
    all_books = fetch_books()
    books = [b for s in sorted({b["subject"] for b in all_books}) for b in lesson_books(all_books, s)]
    cache = json.loads((ROOT / "toc-cache.json").read_text(encoding="utf-8"))
    pages_path = ROOT / "toc-pages.json"
    toc_pages = json.loads(pages_path.read_text(encoding="utf-8")) if pages_path.exists() else {}
    jobs = plan_jobs(books, cache, toc_pages, subject)
    OUT.mkdir(exist_ok=True)
    done, failed = [], []
    with ThreadPoolExecutor(max_workers=6) as ex:
        for i in range(0, len(jobs), 6):  # six downloads at a time, rendered one after another
            chunk = jobs[i:i + 6]
            for job, data in zip(chunk, ex.map(lambda j: _safe(download, j), chunk)):
                result = _safe(render, job, data) if isinstance(data, bytes) else data
                (done if isinstance(result, dict) else failed).append(result if isinstance(result, dict) else {**job, "error": result})
    (OUT / "manifest.json").write_text(json.dumps({"done": done, "failed": failed}, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"{len(done)} rendered, {len(failed)} failed")


def _safe(fn, *args):
    try:
        return fn(*args)
    except Exception as e:  # one bad book must not stop the others
        return f"{type(e).__name__}: {e}"


if __name__ == "__main__":
    main()
