import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
from check_cache import check_entry  # noqa: E402

GOOD = {"subject": "التاريخ", "grade": "s1", "term": "1", "title": "التاريخ",
        "units": [{"title": "الوحدة الأولى: مدخل لدراسة حضارة مصر", "lessons": ["الحضارة والتاريخ", "مصادر دراسة الحضارات"]}]}


def with_units(units):
    return {**GOOD, "units": units}


def test_good_entry_passes():
    assert check_entry(GOOD) == []


def test_no_units():
    assert check_entry(with_units([]))


def test_empty_lessons():
    assert check_entry(with_units([{"title": "الوحدة", "lessons": []}]))


def test_digits_only_title():
    assert check_entry(with_units([{"title": "12", "lessons": ["أ"]}]))


def test_lesson_ending_in_page_number():
    assert check_entry(with_units([{"title": "الوحدة", "lessons": ["الحضارة والتاريخ ...... 4"]}]))
    assert check_entry(with_units([{"title": "الوحدة", "lessons": ["الحضارة والتاريخ\t12"]}]))


def test_number_that_is_part_of_the_name_passes():
    assert check_entry(with_units([{"title": "الفصل 1", "lessons": ["الأعداد حتى 10"]}])) == []


def test_broken_arabic():
    assert check_entry(with_units([{"title": "الوحدة األولى", "lessons": ["أ"]}]))
    assert check_entry(with_units([{"title": "الوحدة", "lessons": ["اإلعالم"]}]))


def test_real_words_ending_in_amal_pass():
    assert check_entry(with_units([{"title": "الوحدة السادسة: العوامل والمضاعفات", "lessons": ["التكامل المحدد"]}])) == []


def test_missing_fields():
    assert check_entry({"units": GOOD["units"]})
