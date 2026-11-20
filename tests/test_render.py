import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
from render_toc import plan_jobs, resolve_pages  # noqa: E402


def book(subject, grade, link):
    return {"subject": subject, "grade": grade, "term": "1", "lang": "ar",
            "title": subject, "type": "كتاب الطالب", "link": link}


BOOKS = [book("العلوم", "p4", "https://x/cached.pdf"),
         book("العلوم", "p5", "https://x/known.pdf"),
         book("العلوم", "p6", "https://x/unknown.pdf"),
         book("التاريخ", "s1", "https://x/history.pdf")]
CACHE = {"https://x/cached.pdf": {"units": []}}
PAGES = {"https://x/known.pdf": [5, 6]}


def test_plan_jobs_modes():
    jobs = plan_jobs(BOOKS, CACHE, PAGES, "العلوم")
    assert [j["link"] for j in jobs] == ["https://x/known.pdf", "https://x/unknown.pdf"]
    assert jobs[0]["mode"] == "pages" and jobs[0]["pages"] == [5, 6]
    assert jobs[1]["mode"] == "sheet" and jobs[1]["pages"] == list(range(1, 13))
    assert jobs[0]["file"] == "001_p5_t1.jpg" and jobs[1]["file"] == "002_p6_t1.jpg"


def test_plan_jobs_end_sheet():
    jobs = plan_jobs(BOOKS, CACHE, {**PAGES, "https://x/unknown.pdf": "end"}, "العلوم")
    assert jobs[1]["mode"] == "sheet" and jobs[1]["pages"] == list(range(-12, 0))


def test_resolve_pages_counts_from_the_end():
    assert resolve_pages([-2, -1, 3, 0, 99], 50) == [49, 50, 3]


def test_plan_jobs_subject_filter():
    assert [j["subject"] for j in plan_jobs(BOOKS, CACHE, PAGES, "all")] == ["العلوم", "العلوم", "التاريخ"]
    assert [j["link"] for j in plan_jobs(BOOKS, CACHE, PAGES, "التاريخ")] == ["https://x/history.pdf"]
