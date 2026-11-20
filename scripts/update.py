"""Manual job: mirror the ministry's textbook list and build the curriculum of one subject (or all).

Run from GitHub Actions ("Update curriculum" -> Run workflow) with the subject name, or locally:
    python scripts/update.py "العلوم"        # one subject
    python scripts/update.py all             # every subject
    python scripts/update.py                 # only refresh books.json and subjects/index.json

1. Reads the ministry's books.json for primary, preparatory and secondary and writes books.json
   here with every subject (the app reads the subject list from the ministry the same way).
2. For every book of the chosen subject not seen before, sends its first pages (where the table of
   contents is) to Claude, stores the extracted units/lessons in toc-cache.json, and rebuilds that
   subject's curriculum file. Skipped when ANTHROPIC_API_KEY is not set.
3. Writes subjects/index.json: which subjects have a curriculum file, where it is and its version.

Curriculum files: math stays at curriculum.json (older app versions read it there);
every other subject is subjects/<subject name>.json.
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
BROWSER_UA = {"User-Agent": "Mozilla/5.0"}
TOC_PAGES = 16  # the contents page is always inside the first pages of the book

ORD = {"الأول": 1, "الثاني": 2, "الثالث": 3, "الرابع": 4, "الخامس": 5, "السادس": 6}
STAGE_PREFIX = {"الابتدائي": "p", "الإعدادي": "m", "الثانوي": "s"}
FULL_YEAR = {"s3"}
MATH = "الرياضيات"
# order of math books inside one grade (secondary has several math books)
BOOK_ORDER = ["الرياضيات", "الرياضيات البحتة", "الرياضيات العامة", "تطبيقات الرياضيات", "الرياضيات التطبيقية"]
# extra material that is not part of the lesson plan
NOT_LESSONS = re.compile(r"كراسة|فيديو|صوتيات|بطاقة")


# Must match subjectOf() in the app (app.src.html), so both name the subjects the same way.
def subject_of(raw):
    s = re.sub(r"\s+", " ", raw or "").strip()
    if "رياض" in s:
        return MATH
    s = re.sub(r"\s*باللغة \S+$", "", s)
    s = re.split(r"\s*-\s*", s)[0].strip()
    s = re.sub(r"ى(?=\s|$)", "ي", s)
    if "تكنولوجيا المعلومات" in s:
        return "تكنولوجيا المعلومات والاتصالات"
    if s.startswith("العلوم"):
        return "العلوم"
    if "جغرافيا" in s:
        return "الجغرافيا"
    return s


def book_title(raw):
    """The book's own name inside its subject, e.g. "اللغة العربية - القصة"."""
    s = re.sub(r"\s+", " ", raw or "").strip()
    s = re.sub(r"\s*باللغة \S+$", "", s)
    return re.sub(r"\s*-\s*", " - ", s).strip()


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
    if "باللغة الإنجليزية" in subject or "باللغة الانجليزية" in subject:
        return "en"
    if "باللغة الفرنسية" in subject:
        return "fr"
    return "ar"


def grade_key(g):
    return ("pms".index(g[0]), int(g[1:]))


def stage_list(stage):
    """The ministry's list for one stage. The site refuses GitHub's servers (403), so when the
    request fails, use the copy saved in ministry/<stage>.json (refreshed from a PC in Egypt)."""
    try:
        r = requests.get(f"{SITE}/{stage}/books.json", headers=BROWSER_UA, timeout=60)
        r.raise_for_status()
        return r.json()
    except Exception as e:
        saved = ROOT / "ministry" / f"{stage}.json"
        print(f"{stage}: ministry site unavailable ({e}); using {saved.relative_to(ROOT).as_posix()}")
        return json.loads(saved.read_text(encoding="utf-8-sig"))


def fetch_books():
    books = []
    for stage in STAGES:
        for b in stage_list(stage):
            g = grade_id(b.get("grade", ""))
            if not g or not b.get("link"):
                continue
            raw = b.get("subject", "")
            books.append({
                "subject": subject_of(raw),
                "grade": g,
                "term": term_id(b.get("term", "")),
                "lang": lang_of(raw),
                "title": book_title(raw),
                "type": b.get("type", ""),
                "link": b["link"],
            })
    books.sort(key=lambda b: (b["subject"], grade_key(b["grade"]), b["term"], b["lang"] != "ar", b["title"]))
    return books


def write_json(path, data):
    text = json.dumps(data, ensure_ascii=False, indent=2) + "\n"
    if path.exists() and path.read_text(encoding="utf-8") == text:
        return False
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return True


def cur_path(subject):
    return ROOT / ("curriculum.json" if subject == MATH else f"subjects/{subject}.json")


def lesson_books(books, subject):
    """The books whose contents make up the lesson plan: the Arabic-medium PDFs of the subject."""
    return [b for b in books if b["subject"] == subject and b["lang"] == "ar"
            and b["link"].lower().endswith(".pdf") and not NOT_LESSONS.search(b["title"])]


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


PROMPT = """These are the first pages of an official Egyptian Ministry of Education textbook: "{title}" ({subject}).
Find the table of contents (الفهرس / المحتويات / Contents) and list every unit and its lessons in book order.
Skip front matter and review/exercise pages that are not lessons (for example "تمارين عامة", "اختبار الوحدة", "مراجعة", "Revision").
Write titles exactly as printed in the book, in the book's own language, without page numbers and without a "الدرس"/"Lesson" numbering prefix.
Reply with only one JSON object and no other text:
{{"units": [{{"title": "...", "lessons": ["...", "..."]}}]}}"""


def extract_toc(client, pdf_bytes, title, subject):
    msg = client.beta.messages.create(
        model="claude-opus-5-5",
        max_tokens=16000,
        betas=["server-side-fallback-2026-07-01"],
        fallbacks="default",
        messages=[{
            "role": "user",
            "content": [
                {"type": "document", "source": {"type": "base64", "media_type": "application/pdf", "data": base64.standard_b64encode(pdf_bytes).decode()}},
                {"type": "text", "text": PROMPT.format(title=title, subject=subject)},
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


def book_rank(subject, title):
    if subject == MATH:
        return BOOK_ORDER.index(title) if title in BOOK_ORDER else 99
    return 0 if title == subject else 1  # the main book first, then the story/grammar books


def rebuild_curriculum(subject, books, cache):
    """Rebuilds the grades/terms of one subject whose books all have a table of contents."""
    path = cur_path(subject)
    if path.exists():
        cur = json.loads(path.read_text(encoding="utf-8"))
    else:
        cur = {"subject": subject, "version": "", "updated": "", "grades": {},
               "note": "من فهرس كتب الوزارة الرسمية. الترم الثاني بيتضاف لما الوزارة تنزّل كتبه."}
    cur["subject"] = subject
    changed = False
    groups = {}
    for b in lesson_books(books, subject):
        groups.setdefault((b["grade"], b["term"]), []).append(b)
    for (g, t), group in groups.items():
        if not all(b["link"] in cache for b in group):
            continue
        group.sort(key=lambda b: (book_rank(subject, b["title"]), b["title"]))
        units = []
        for b in group:
            entry = cache[b["link"]]
            # several books for one grade: name the book in front of each unit, unless the
            # cache entry says the unit titles already tell the books apart (prefix: "")
            prefix = entry.get("prefix", b["title"] if len(group) > 1 else "")
            for u in entry["units"]:
                title = f'{prefix} - {u["title"]}' if prefix else u["title"]
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
        cur["grades"] = dict(sorted(cur["grades"].items(), key=lambda kv: grade_key(kv[0])))
        cur["updated"] = date.today().isoformat()
        year = groups and next(iter(groups.values()))[0]["link"].split("/")[4].replace("_", "-")
        cur["version"] = f"{year} من كتب الوزارة {cur['updated']}"
        write_json(path, cur)
    return changed


def write_index(books):
    """subjects/index.json: every subject on the ministry site, and its curriculum file if there is one."""
    subjects = {}
    for b in books:
        s = subjects.setdefault(b["subject"], {"grades": set()})
        s["grades"].add(b["grade"])
    out = {}
    for name in sorted(subjects, key=lambda n: (n != MATH, n)):
        entry = {"grades": sorted(subjects[name]["grades"], key=grade_key)}
        path = cur_path(name)
        if path.exists():
            cur = json.loads(path.read_text(encoding="utf-8"))
            entry.update({"file": path.relative_to(ROOT).as_posix(), "version": cur.get("version", ""), "updated": cur.get("updated", "")})
        out[name] = entry
    return write_json(ROOT / "subjects" / "index.json", {"updated": date.today().isoformat(), "subjects": out})


def main():
    wanted = (sys.argv[1] if len(sys.argv) > 1 else os.environ.get("SUBJECT", "")).strip()
    books = fetch_books()
    if not books:
        sys.exit("no books found on the ministry site")
    all_subjects = sorted({b["subject"] for b in books})
    if wanted and wanted != "all" and wanted not in all_subjects:
        sys.exit(f"unknown subject {wanted!r}. Subjects on the ministry site: {', '.join(all_subjects)}")

    payload = {"updated": date.today().isoformat(), "source": "https://studentbooks.moe.gov.eg/Books/", "books": books}
    old = ROOT / "books.json"
    old_books = json.loads(old.read_text(encoding="utf-8"))["books"] if old.exists() else None
    if old_books != books:
        write_json(old, payload)
        print(f"books.json: {len(books)} books, {len(all_subjects)} subjects")

    targets = all_subjects if wanted == "all" else [wanted] if wanted else []
    cache_path = ROOT / "toc-cache.json"
    cache = json.loads(cache_path.read_text(encoding="utf-8")) if cache_path.exists() else {}
    todo = [b for s in targets for b in lesson_books(books, s) if b["link"] not in cache]
    if todo and os.environ.get("ANTHROPIC_API_KEY"):
        import anthropic
        client = anthropic.Anthropic()
        for b in todo:
            try:
                units = extract_toc(client, first_pages_pdf(b["link"]), f'{b["title"]} - {b["grade"]} - term {b["term"]}', b["subject"])
                cache[b["link"]] = {"subject": b["subject"], "grade": b["grade"], "term": b["term"], "title": b["title"], "units": units}
                print(f"toc: {b['subject']} {b['grade']} t{b['term']} {b['title']}: {len(units)} units")
                write_json(cache_path, cache)  # keep what is done if a later book fails the run
            except Exception as e:  # keep going; the book is retried on the next run
                print(f"toc failed for {b['link']}: {e}")
    elif todo:
        print(f"{len(todo)} books waiting for a table of contents (ANTHROPIC_API_KEY not set)")

    for s in targets:
        if rebuild_curriculum(s, books, cache):
            print(f"{cur_path(s).relative_to(ROOT).as_posix()} updated")
    if write_index(books):
        print("subjects/index.json updated")


if __name__ == "__main__":
    main()
