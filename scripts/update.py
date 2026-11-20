"""Daily job: mirror the ministry's math textbook list and keep curriculum.json in step with it.

1. Reads the ministry's books.json for primary, preparatory and secondary, keeps the math
   books, and writes books.json here (raw.githubusercontent.com lets the app read it).
2. For every Arabic math book not seen before, sends its first pages (where the table of
   contents is) to Claude, stores the extracted units/lessons in toc-cache.json, and rebuilds
   that grade/term in curriculum.json. Skipped when ANTHROPIC_API_KEY is not set.
"""
import base64
import io
import json
import os
import re
import sys
from datetime import date
from pathlib import Path

import requests
from pypdf import PdfReader, PdfWriter

ROOT = Path(__file__).resolve().parent.parent
SITE = "https://studentbooks.moe.gov.eg/Books"
STAGES = ["Books-Primary", "Books-Preparatory", "Books-sec"]
UA = {"User-Agent": "teacher-curriculum-sync/1.0"}
TOC_PAGES = 16  # the contents page is always inside the first pages of the book

ORD = {"الأول": 1, "الثاني": 2, "الثالث": 3, "الرابع": 4, "الخامس": 5, "السادس": 6}
STAGE_PREFIX = {"الابتدائي": "p", "الإعدادي": "m", "الثانوي": "s"}
FULL_YEAR = {"s3"}
# order of books inside one grade (secondary has several math books)
BOOK_ORDER = ["الرياضيات", "الرياضيات البحتة", "الرياضيات العامة", "تطبيقات الرياضيات", "الرياضيات التطبيقية"]


def grade_id(name):
    m = re.search(r"الصف (\S+) (\S+)", name)
    if not m or m.group(1) not in ORD:
        return None
    for word, prefix in STAGE_PREFIX.items():
        if m.group(2).startswith(word):
            return f"{prefix}{ORD[m.group(1)]}"
    return None


def term_id(name):
    return "2" if "الثاني" in name else "1"


def lang_of(subject):
    if "الإنجليزية" in subject:
        return "en"
    if "الفرنسية" in subject:
        return "fr"
    return "ar"


def fetch_books():
    books = []
    for stage in STAGES:
        r = requests.get(f"{SITE}/{stage}/books.json", headers=UA, timeout=60)
        r.raise_for_status()
        for b in r.json():
            if "رياض" not in b.get("subject", ""):
                continue
            g = grade_id(b.get("grade", ""))
            if not g:
                continue
            books.append({
                "grade": g,
                "term": term_id(b.get("term", "")),
                "lang": lang_of(b["subject"]),
                "title": re.sub(r"\s*باللغة \S+$", "", b["subject"]).strip(),
                "type": b.get("type", ""),
                "link": b["link"],
            })
    order = {t: i for i, t in enumerate(BOOK_ORDER)}
    books.sort(key=lambda b: (b["grade"][0] != "p", b["grade"][0] == "s", b["grade"], b["term"], b["lang"] != "ar", order.get(b["title"], 99)))
    return books


def write_json(path, data):
    text = json.dumps(data, ensure_ascii=False, indent=2) + "\n"
    if path.exists() and path.read_text(encoding="utf-8") == text:
        return False
    path.write_text(text, encoding="utf-8")
    return True


def first_pages_pdf(link):
    r = requests.get(link, headers=UA, timeout=600)
    r.raise_for_status()
    reader = PdfReader(io.BytesIO(r.content))
    writer = PdfWriter()
    for page in reader.pages[:TOC_PAGES]:
        writer.add_page(page)
    out = io.BytesIO()
    writer.write(out)
    return out.getvalue()


PROMPT = """These are the first pages of an official Egyptian Ministry of Education math textbook ({title}).
Find the table of contents (الفهرس / المحتويات) and list every unit and its lessons in book order.
Skip front matter and review/exercise pages that are not lessons (for example "تمارين عامة", "اختبار الوحدة", "مراجعة").
Write titles in Arabic exactly as printed, without page numbers and without the word "الدرس" numbering prefix.
Reply with only one JSON object and no other text:
{{"units": [{{"title": "الوحدة الأولى: ...", "lessons": ["...", "..."]}}]}}"""


def extract_toc(client, pdf_bytes, title):
    msg = client.beta.messages.create(
        model="claude-opus-5-5",
        max_tokens=16000,
        betas=["server-side-fallback-2026-07-01"],
        fallbacks="default",
        messages=[{
            "role": "user",
            "content": [
                {"type": "document", "source": {"type": "base64", "media_type": "application/pdf", "data": base64.standard_b64encode(pdf_bytes).decode()}},
                {"type": "text", "text": PROMPT.format(title=title)},
            ],
        }],
    )
    if msg.stop_reason == "refusal":
        raise RuntimeError("request was declined")
    text = "".join(b.text for b in msg.content if b.type == "text")
    m = re.search(r"```(?:json)?\s*(.*?)```", text, re.S)
    data = json.loads(m.group(1) if m else text[text.index("{"): text.rindex("}") + 1])
    units = data["units"]
    if not units or not all(isinstance(u.get("title"), str) and isinstance(u.get("lessons"), list) for u in units):
        raise RuntimeError("no units found")
    return units


def rebuild_curriculum(books, cache):
    path = ROOT / "curriculum.json"
    cur = json.loads(path.read_text(encoding="utf-8"))
    changed = False
    groups = {}
    for b in books:
        if b["lang"] == "ar":
            groups.setdefault((b["grade"], b["term"]), []).append(b)
    for (g, t), group in groups.items():
        if not all(b["link"] in cache for b in group):
            continue
        units = []
        for b in group:
            for u in cache[b["link"]]["units"]:
                title = u["title"] if len(group) == 1 else f'{b["title"]} - {u["title"]}'
                units.append({"title": title, "lessons": u["lessons"]})
        grade = cur["grades"].setdefault(g, {"terms": {}})
        if grade["terms"].get(t) != units:
            grade["terms"][t] = units
            grade["source"] = "book"
            grade["updated"] = date.today().isoformat()
            changed = True
        if g in FULL_YEAR and not grade.get("fullYear"):
            grade["fullYear"] = True
            changed = True
    if changed:
        cur["updated"] = date.today().isoformat()
        cur["version"] = f"{books[0]['link'].split('/')[4].replace('_', '-')} من كتب الوزارة {cur['updated']}"
        write_json(path, cur)
    return changed


def main():
    books = fetch_books()
    if not books:
        sys.exit("no math books found on the ministry site")
    payload = {"updated": date.today().isoformat(), "source": "https://studentbooks.moe.gov.eg/Books/", "books": books}
    old = ROOT / "books.json"
    old_books = json.loads(old.read_text(encoding="utf-8"))["books"] if old.exists() else None
    if old_books != books:
        write_json(old, payload)
        print(f"books.json: {len(books)} math books")

    cache_path = ROOT / "toc-cache.json"
    cache = json.loads(cache_path.read_text(encoding="utf-8")) if cache_path.exists() else {}
    todo = [b for b in books if b["lang"] == "ar" and b["link"] not in cache]
    if todo and os.environ.get("ANTHROPIC_API_KEY"):
        import anthropic
        client = anthropic.Anthropic()
        for b in todo:
            try:
                units = extract_toc(client, first_pages_pdf(b["link"]), f'{b["title"]} - {b["grade"]} - term {b["term"]}')
                cache[b["link"]] = {"grade": b["grade"], "term": b["term"], "title": b["title"], "units": units}
                print(f"toc: {b['grade']} t{b['term']} {b['title']}: {len(units)} units")
            except Exception as e:  # keep going; the book is retried on the next run
                print(f"toc failed for {b['link']}: {e}")
        write_json(cache_path, cache)
    elif todo:
        print(f"{len(todo)} new books waiting for a table of contents (ANTHROPIC_API_KEY not set)")

    if rebuild_curriculum(books, cache):
        print("curriculum.json updated")


if __name__ == "__main__":
    main()
