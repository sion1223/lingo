"""Build the browser's school-grade-first jōyō kanji catalog.

KANJIDIC2 grade values 1-6 identify the 1,026 kyōiku kanji. Grade 8 identifies
the remaining 1,110 jōyō kanji. Primary-school characters are ordered by grade,
stroke count, and newspaper frequency; remaining jōyō characters are ordered
by frequency and then stroke count.
"""

from __future__ import annotations

import argparse
import gzip
import io
import json
import urllib.request
import xml.etree.ElementTree as ET
from pathlib import Path


DEFAULT_SOURCE = "https://www.edrdg.org/kanjidic/kanjidic2.xml.gz"
EDUCATION_GRADES = (1, 2, 3, 4, 5, 6)
JOYO_GRADES = frozenset((*EDUCATION_GRADES, 8))
EXPECTED_GRADE_COUNTS = {
    "1": 80,
    "2": 160,
    "3": 200,
    "4": 202,
    "5": 193,
    "6": 191,
    "other_joyo": 1110,
}


def read_source(source: str) -> bytes:
    path = Path(source)
    if path.exists():
        payload = path.read_bytes()
    else:
        request = urllib.request.Request(
            source,
            headers={"User-Agent": "lingo-kanji-catalog/1"},
        )
        with urllib.request.urlopen(request, timeout=60) as response:
            payload = response.read()
    return (
        gzip.GzipFile(fileobj=io.BytesIO(payload)).read()
        if payload[:2] == b"\x1f\x8b"
        else payload
    )


def _integer(node: ET.Element, path: str) -> int | None:
    value = node.findtext(path)
    try:
        return int(value) if value else None
    except ValueError:
        return None


def build_catalog(xml_bytes: bytes) -> dict:
    root = ET.fromstring(xml_bytes)
    database_date = root.findtext("./header/date_of_creation") or "unknown"
    entries: list[dict] = []
    for node in root.findall("character"):
        character = node.findtext("literal")
        grade = _integer(node, "./misc/grade")
        if not character or len(character) != 1 or grade not in JOYO_GRADES:
            continue
        entries.append(
            {
                "character": character,
                "grade": grade,
                "strokes": _integer(node, "./misc/stroke_count") or 0,
                "frequency": _integer(node, "./misc/freq"),
            }
        )

    def rank(entry: dict) -> tuple:
        frequency = entry["frequency"]
        frequency_rank = frequency if frequency is not None else 1_000_000
        if entry["grade"] in EDUCATION_GRADES:
            return (
                0,
                entry["grade"],
                entry["strokes"],
                frequency_rank,
                ord(entry["character"]),
            )
        return (
            1,
            frequency_rank,
            entry["strokes"],
            ord(entry["character"]),
        )

    entries.sort(key=rank)
    counts = {
        str(grade): sum(entry["grade"] == grade for entry in entries)
        for grade in EDUCATION_GRADES
    }
    counts["other_joyo"] = sum(entry["grade"] == 8 for entry in entries)
    if counts != EXPECTED_GRADE_COUNTS:
        raise ValueError(
            f"jōyō grade counts changed: expected {EXPECTED_GRADE_COUNTS}, got {counts}"
        )
    if len(entries) != 2_136 or len({entry["character"] for entry in entries}) != 2_136:
        raise ValueError("expected 2,136 unique jōyō kanji")

    return {
        "schema_version": 1,
        "database_date": database_date,
        "source": DEFAULT_SOURCE,
        "license": "CC BY-SA 4.0",
        "sort_policy": (
            "kyouiku: grade, stroke count, frequency; "
            "remaining joyo: frequency, stroke count"
        ),
        "counts": counts,
        "entries": entries,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", default=DEFAULT_SOURCE)
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("web/data/kanji-catalog.json"),
    )
    args = parser.parse_args()
    catalog = build_catalog(read_source(args.source))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(catalog, ensure_ascii=False, separators=(",", ":")),
        encoding="utf-8",
    )
    print(
        f"wrote {len(catalog['entries'])} joyo kanji "
        f"({catalog['database_date']}) to {args.output}"
    )


if __name__ == "__main__":
    main()
