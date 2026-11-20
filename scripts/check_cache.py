"""Checks toc-cache.json for mistakes a contents page can carry over.

    python scripts/check_cache.py        # prints one line per problem, exits 1 if there are any
"""
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
FIELDS = ("subject", "grade", "term", "title", "units")
PAGE_NUMBER = re.compile(r"(?:[.…_·]{2,}|\t|\s{2,})\s*[\d٠-٩]+$")
BROKEN_ARABIC = re.compile(r"اإل|األ|اآل|اال")  # lam-alef split by a bad PDF text layer


def check_entry(entry):
    problems = [f"missing {f}" for f in FIELDS if f not in entry]
    units = entry.get("units") or []
    if not units:
        problems.append("no units")
    for u in units:
        title, lessons = u.get("title", ""), u.get("lessons") or []
        if not lessons:
            problems.append(f"no lessons in «{title}»")
        for name in [title, *lessons]:
            if not isinstance(name, str) or not name.strip():
                problems.append("empty name")
            elif re.fullmatch(r"[\d٠-٩\s]+", name):
                problems.append(f"only digits: «{name}»")
            elif PAGE_NUMBER.search(name):
                problems.append(f"page number: «{name}»")
            elif BROKEN_ARABIC.search(name):
                problems.append(f"broken letters: «{name}»")
    return problems


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    cache = json.loads((ROOT / "toc-cache.json").read_text(encoding="utf-8"))
    bad = 0
    for link, entry in cache.items():
        for p in check_entry(entry):
            bad += 1
            print(f"{entry.get('subject')} {entry.get('grade')} t{entry.get('term')}: {p}  ({link.rsplit('/', 1)[-1]})")
    sys.exit(1 if bad else 0)


if __name__ == "__main__":
    main()
