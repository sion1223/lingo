"""Build the server's common-word index from JMdict.

The learning menu asks an AI model for JLPT words; the server keeps a word only
when JMdict lists it as a common word (any ke_pri tag) with the given reading.

JMdict is copyright EDRDG and distributed under CC BY-SA 4.0. See
scorer/data/JMDICT-NOTICE.md before redistributing the generated index.
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

DEFAULT_SOURCE = "https://www.edrdg.org/pub/Nihongo/JMdict_e.gz"
MAX_WORD_CHARS = 8
CREATED = re.compile(rb"JMdict created: (\d{4}-\d{2}-\d{2})")


def read_source(source: str) -> bytes:
    path = Path(source)
    if path.exists():
        payload = path.read_bytes()
    else:
        request = urllib.request.Request(source, headers={"User-Agent": "lingo-word-index/1"})
        with urllib.request.urlopen(request, timeout=120) as response:
            payload = response.read()
    return gzip.decompress(payload) if payload[:2] == b"\x1f\x8b" else payload


def to_hiragana(text: str) -> str:
    return "".join(chr(ord(char) - 0x60) if "ァ" <= char <= "ヶ" else char for char in text)


def has_kanji(text: str) -> bool:
    return any("㐀" <= char <= "鿿" or char == "々" for char in text)


def build_index(xml_bytes: bytes) -> dict:
    created = CREATED.search(xml_bytes[:300_000])
    words: dict[str, list[str]] = {}
    for _, entry in ET.iterparse(io.BytesIO(xml_bytes), events=("end",)):
        if entry.tag != "entry":
            continue
        common = [
            keb
            for k_ele in entry.findall("k_ele")
            if (keb := k_ele.findtext("keb"))
            and k_ele.find("ke_pri") is not None
            and has_kanji(keb)
            and len(keb) <= MAX_WORD_CHARS
        ]
        for r_ele in entry.findall("r_ele") if common else ():
            reb = r_ele.findtext("reb")
            if not reb or r_ele.find("re_nokanji") is not None:
                continue
            restricted = {node.text for node in r_ele.findall("re_restr")}
            for keb in common:
                if restricted and keb not in restricted:
                    continue
                readings = words.setdefault(keb, [])
                reading = to_hiragana(reb)
                if reading not in readings:
                    readings.append(reading)
        entry.clear()
    return {
        "schema_version": 1,
        "database_date": created.group(1).decode() if created else "unknown",
        "source": DEFAULT_SOURCE,
        "license": "CC BY-SA 4.0",
        "words": dict(sorted(words.items())),
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", default=DEFAULT_SOURCE)
    parser.add_argument("--output", type=Path, default=Path("scorer/data/jmdict-common-words.json.gz"))
    args = parser.parse_args()
    index = build_index(read_source(args.source))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(index, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    args.output.write_bytes(gzip.compress(payload, mtime=0))
    print(f"wrote {len(index['words'])} common words ({index['database_date']}) to {args.output}")


if __name__ == "__main__":
    main()
