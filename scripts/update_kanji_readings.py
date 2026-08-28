"""Build the browser's kanji-reading search index from KANJIDIC2.

KANJIDIC2 is copyright EDRDG and distributed under CC BY-SA 4.0. See
web/data/KANJIDIC2-NOTICE.md before redistributing the generated index.
"""

from __future__ import annotations

import argparse
import gzip
import io
import json
import re
import urllib.request
import xml.etree.ElementTree as ET
from pathlib import Path

DEFAULT_SOURCE = "https://www.edrdg.org/kanjidic/kanjidic2.xml.gz"
SVG_NAME = re.compile(r"^([0-9a-f]{5})\.svg$")
READING_SEPARATORS = str.maketrans("", "", ".-‐‑‒–—・･ ")


def available_characters(kanji_dir: Path) -> set[str]:
    result = set()
    for path in kanji_dir.iterdir():
        match = SVG_NAME.fullmatch(path.name)
        if match:
            result.add(chr(int(match.group(1), 16)))
    return result


def read_source(source: str) -> bytes:
    path = Path(source)
    if path.exists():
        payload = path.read_bytes()
    else:
        request = urllib.request.Request(
            source,
            headers={"User-Agent": "lingo-reading-index/1"},
        )
        with urllib.request.urlopen(request, timeout=60) as response:
            payload = response.read()
    return gzip.GzipFile(fileobj=io.BytesIO(payload)).read() if payload[:2] == b"\x1f\x8b" else payload


def clean_reading(value: str | None) -> str:
    return (value or "").translate(READING_SEPARATORS).strip()


def build_index(xml_bytes: bytes, allowed: set[str]) -> dict:
    root = ET.fromstring(xml_bytes)
    created = root.findtext("./header/date_of_creation") or "unknown"
    readings: dict[str, list[str]] = {}
    for entry in root.findall("character"):
        literal = entry.findtext("literal")
        if not literal or literal not in allowed:
            continue
        values = []
        for node in entry.findall("./reading_meaning/rmgroup/reading"):
            if node.attrib.get("r_type") not in {"ja_on", "ja_kun"}:
                continue
            reading = clean_reading(node.text)
            if reading and reading not in values:
                values.append(reading)
        for node in entry.findall("./reading_meaning/nanori"):
            reading = clean_reading(node.text)
            if reading and reading not in values:
                values.append(reading)
        if values:
            readings[literal] = values
    return {
        "schema_version": 1,
        "database_date": created,
        "source": DEFAULT_SOURCE,
        "license": "CC BY-SA 4.0",
        "readings": dict(sorted(readings.items(), key=lambda item: ord(item[0]))),
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", default=DEFAULT_SOURCE)
    parser.add_argument("--kanji-dir", type=Path, default=Path("kanji"))
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("web/data/kanji-readings.json"),
    )
    args = parser.parse_args()
    index = build_index(read_source(args.source), available_characters(args.kanji_dir))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(index, ensure_ascii=False, separators=(",", ":")),
        encoding="utf-8",
    )
    print(
        f"wrote {len(index['readings'])} kanji readings "
        f"({index['database_date']}) to {args.output}"
    )


if __name__ == "__main__":
    main()
