import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import update  # noqa: E402
from update import rebuild_curriculum, subject_of, write_index  # noqa: E402

MATH = "الرياضيات"


def test_subject_of_languages():
    assert subject_of("اللغة الإنجليزية-لغة أجنبية أولى") == "اللغة الإنجليزية - لغة أولى"
    assert subject_of("اللغة الإنجليزية-لغة أجنبية ثانية") == "اللغة الإنجليزية - لغة ثانية"
    assert subject_of("اللغة الفرنسية-لغة أجنبية أولى-كراسة التدريبات") == "اللغة الفرنسية - لغة أولى"
    assert subject_of("اللغة الألمانية-لغة أجنبية ثانية") == "اللغة الألمانية - لغة ثانية"
    assert subject_of("الرياضيات البحتة باللغة العربية") == MATH
    assert subject_of("العلوم المتكاملة باللغة العربية") == "العلوم"


def book(subject, grade, term, link, title=None):
    return {"subject": subject, "grade": grade, "term": term, "lang": "ar",
            "title": title or subject, "type": "كتاب الطالب", "link": link}


T1 = "https://x/p1_t1.pdf"
T2 = "https://x/p1_t2.pdf"
DRAFT2 = [{"title": "مسودة", "lessons": ["درس مسودة"]}]
BOOK1 = [{"title": "الوحدة الأولى", "lessons": ["أ", "ب"]}]
BOOK2 = [{"title": "الوحدة الخامسة", "lessons": ["ج"]}]


def math_root(tmp_path):
    cur = {"version": "old", "updated": "2026-01-01", "grades": {
        "p1": {"terms": {"1": BOOK1, "2": DRAFT2}, "source": "book"}}}
    (tmp_path / "curriculum.json").write_text(json.dumps(cur, ensure_ascii=False), encoding="utf-8")
    return tmp_path


def read_math(root):
    return json.loads((root / "curriculum.json").read_text(encoding="utf-8"))


def test_rebuild_keeps_and_marks_draft(tmp_path):
    root = math_root(tmp_path)
    books = [book(MATH, "p1", "1", T1)]
    cache = {T1: {"units": BOOK1}}
    rebuild_curriculum(MATH, books, cache, root=root)
    p1 = read_math(root)["grades"]["p1"]
    assert p1["terms"]["2"] == DRAFT2
    assert p1["draft"] == {"2": True}


def test_rebuild_replaces_draft_when_book_arrives(tmp_path):
    root = math_root(tmp_path)
    books = [book(MATH, "p1", "1", T1), book(MATH, "p1", "2", T2)]
    cache = {T1: {"units": BOOK1}, T2: {"units": BOOK2}}
    rebuild_curriculum(MATH, books, cache, root=root)
    p1 = read_math(root)["grades"]["p1"]
    assert p1["terms"]["2"] == BOOK2
    assert "2" not in p1.get("draft", {})


def test_index_version_changes_only_for_changed_subject(tmp_path):
    root = math_root(tmp_path)
    sci = "العلوم"
    s1, s2 = "https://x/sci_t1.pdf", "https://x/sci2_t1.pdf"
    books = [book(MATH, "p1", "1", T1), book(sci, "p4", "1", s1), book(sci, "p5", "1", s2)]
    cache = {T1: {"units": BOOK1}, s1: {"units": BOOK1}}
    rebuild_curriculum(MATH, books, cache, root=root)
    rebuild_curriculum(sci, books, cache, root=root)
    write_index(books, root=root)
    before = json.loads((root / "subjects" / "index.json").read_text(encoding="utf-8"))["subjects"]
    cache[s2] = {"units": BOOK2}
    update.date = FakeDate  # the second build happens on a later day
    try:
        rebuild_curriculum(sci, books, cache, root=root)
        write_index(books, root=root)
    finally:
        update.date = RealDate
    after = json.loads((root / "subjects" / "index.json").read_text(encoding="utf-8"))["subjects"]
    assert after[sci]["version"] != before[sci]["version"]
    assert after[MATH]["version"] == before[MATH]["version"]
    assert after[sci]["file"] == "subjects/العلوم.json"


def test_lesson_books_leaves_out_skipped_books(tmp_path):
    books = [book("العلوم", "p4", "1", "https://x/a.pdf"), book("العلوم", "p4", "1", "https://x/reader.pdf")]
    (tmp_path / "toc-skip.json").write_text(json.dumps({"https://x/reader.pdf": "story reader, no contents"}), encoding="utf-8")
    assert [b["link"] for b in update.lesson_books(books, "العلوم", root=tmp_path)] == ["https://x/a.pdf"]


RealDate = update.date


class FakeDate(RealDate):
    @classmethod
    def today(cls):
        return RealDate(2030, 1, 2)
