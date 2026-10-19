"""Build the browser's per-kanji Korean meaning (훈음) and Japanese reading index.

Sources (see web/data/KANJI-MEANINGS-NOTICE.md before redistributing):
- Korean 훈음: libhangul data/hanja/hanja.txt, (c) Choe Hwanjin, BSD-3-Clause.
- Japanese 음독/훈독 and old-form variants: KANJIDIC2, (c) EDRDG, CC BY-SA 4.0.
- Shinjitai -> traditional fallbacks: Unicode Unihan database, Unicode License v3.

Japanese shinjitai such as 国 or 学 have no 훈음 line in hanja.txt, so the
script looks the meaning up through the traditional form (國, 學). A small,
hand-written override table covers Japanese-only characters (国字) and cases
where the automatic variant lookup picks an unrelated sense.
"""

from __future__ import annotations

import argparse
import gzip
import io
import json
import re
import urllib.request
import xml.etree.ElementTree as ET
import zipfile
from collections import defaultdict
from pathlib import Path

KANJIDIC_SOURCE = "https://www.edrdg.org/kanjidic/kanjidic2.xml.gz"
HANJA_SOURCE = "https://raw.githubusercontent.com/libhangul/libhangul/main/data/hanja/hanja.txt"
UNIHAN_SOURCE = "https://www.unicode.org/Public/UCD/latest/ucd/Unihan.zip"
SVG_NAME = re.compile(r"^([0-9a-f]{5})\.svg$")
CJK = re.compile(r"[㐀-鿿豈-﫿\U00020000-\U0003ffff]")
UNIHAN_FIELDS = ("kTraditionalVariant", "kZVariant", "kCompatibilityVariant")
MAX_SENSES = 2
MAX_ON = 2
MAX_KUN = 3
SEPARATOR = "・"

# Hand-written 훈음 for characters the dictionaries cannot resolve correctly.
OVERRIDES = {
    "青": "푸를 청",
    "毎": "매양 매",
    "歩": "걸음 보",
    "清": "맑을 청",
    "働": "일할 동",
    "録": "기록할 록",
    "増": "더할 증",
    "込": "담을 입",
    "徴": "부를 징",
    "戻": "어그러질 려, 되돌아올 려",
    "掲": "높이 들 게, 걸 게",
    "枠": "테두리 (일본 한자)",
    "既": "이미 기",
    "瀬": "여울 뢰",
    "寛": "너그러울 관",
    "涙": "눈물 루",
    "尚": "오히려 상, 숭상할 상",
    "姫": "아가씨 희",
    "閲": "볼 열",
    "捗": "칠 척, 진척될 척",
    "毀": "헐 훼",
    "咲": "꽃 필 소",
    "丼": "덮밥 (일본 한자), 우물 정",
    "概": "대개 개",
    "歴": "지낼 력",
    "駅": "역 역, 역말 역",
    "瓶": "병 병",
    "畑": "화전 전, 밭 (일본 한자)",
    "峠": "고개 상 (일본 한자)",
    "匂": "향내 내 (일본 한자)",
    "教": "가르칠 교",
    "歳": "해 세",
    "頼": "의뢰할 뢰, 힘입을 뢰",
    "縁": "인연 연",
    "踊": "뛸 용, 춤출 용",
    "舗": "펼 포, 가게 포",
    "駄": "실을 태, 짐 태",
    "串": "꿸 관, 꼬챙이 천",
    "楽": "즐길 락, 노래 악",
    "行": "다닐 행, 갈 행",
}


def fetch(source: str) -> bytes:
    path = Path(source)
    if path.exists():
        return path.read_bytes()
    request = urllib.request.Request(source, headers={"User-Agent": "lingo-meaning-index/1"})
    with urllib.request.urlopen(request, timeout=120) as response:
        return response.read()


def available_characters(kanji_dir: Path) -> set[str]:
    result = set()
    for path in kanji_dir.iterdir():
        match = SVG_NAME.fullmatch(path.name)
        if match:
            result.add(chr(int(match.group(1), 16)))
    return result


def parse_hanja(text: str) -> dict[str, list[tuple[str, str]]]:
    """Map a single hanja to its (음, 훈음 string) lines in file order."""
    lines: dict[str, list[tuple[str, str]]] = defaultdict(list)
    for line in text.splitlines():
        if line.startswith("#"):
            continue
        parts = line.split(":")
        if len(parts) >= 3 and len(parts[1]) == 1 and parts[2].strip():
            entry = (parts[0], parts[2].strip())
            if entry not in lines[parts[1]]:
                lines[parts[1]].append(entry)
    return lines


def parse_unihan(payload: bytes) -> dict[str, list[str]]:
    with zipfile.ZipFile(io.BytesIO(payload)) as archive:
        text = archive.read("Unihan_Variants.txt").decode("utf-8")
    variants: dict[str, list[str]] = defaultdict(list)
    for line in text.splitlines():
        if line.startswith("#") or not line.strip():
            continue
        codepoint, field, value = line.split("\t")
        if field not in UNIHAN_FIELDS:
            continue
        character = chr(int(codepoint[2:], 16))
        for token in value.split():
            variant = chr(int(token.split("<")[0][2:], 16))
            if variant != character and variant not in variants[character]:
                variants[character].append(variant)
    return variants


def clean_senses(value: str) -> list[str]:
    senses = []
    for sense in value.split(","):
        sense = sense.strip()
        if sense and not CJK.search(sense) and sense not in senses:
            senses.append(sense)
    return senses


def pick_hun(lines: list[tuple[str, str]], korean: list[str]) -> str:
    """Prefer the line whose 음 matches KANJIDIC2's first Korean reading."""
    ordered = sorted(lines, key=lambda line: 0 if korean and line[0] == korean[0] else 1)
    for _, value in ordered:
        senses = clean_senses(value)
        if senses:
            return ", ".join(senses[:MAX_SENSES])
    return ""


def kun_display(value: str) -> str:
    value = value.strip("-")
    stem, dot, okurigana = value.partition(".")
    return f"{stem}({okurigana})" if dot else stem


def build_index(kanjidic: bytes, hanja: str, unihan: dict[str, list[str]], allowed: set[str]) -> dict:
    root = ET.fromstring(kanjidic)
    created = root.findtext("./header/date_of_creation") or "unknown"
    hun_lines = parse_hanja(hanja)

    by_code: dict[tuple[str, str], str] = {}
    entries: dict[str, ET.Element] = {}
    for node in root.findall("character"):
        literal = node.findtext("literal")
        if not literal:
            continue
        entries[literal] = node
        for cp in node.findall("./codepoint/cp_value"):
            by_code[(cp.attrib["cp_type"], cp.text or "")] = literal

    def candidates(character: str, node: ET.Element) -> list[str]:
        result = [character]
        for variant in node.findall("./misc/variant"):
            var_type = variant.attrib.get("var_type", "")
            if var_type == "ucs":
                result.append(chr(int(variant.text or "0", 16)))
            elif (var_type, variant.text or "") in by_code:
                result.append(by_code[(var_type, variant.text or "")])
        result.extend(unihan.get(character, []))
        return list(dict.fromkeys(result))

    meanings = {}
    unresolved = []
    for character in sorted(allowed & entries.keys(), key=ord):
        node = entries[character]
        readings = node.findall("./reading_meaning/rmgroup/reading")
        korean = [r.text or "" for r in readings if r.attrib.get("r_type") == "korean_h"]
        on = [r.text or "" for r in readings if r.attrib.get("r_type") == "ja_on"]
        kun = [kun_display(r.text or "") for r in readings if r.attrib.get("r_type") == "ja_kun"]

        hun = OVERRIDES.get(character, "")
        if not hun:
            for candidate in candidates(character, node):
                hun = pick_hun(hun_lines.get(candidate, []), korean)
                if hun:
                    break
        if not hun and korean:
            hun = korean[0]
        if not hun:
            unresolved.append(character)

        on_text = SEPARATOR.join(list(dict.fromkeys(on))[:MAX_ON])
        kun_text = SEPARATOR.join(list(dict.fromkeys(kun))[:MAX_KUN])
        if hun or on_text or kun_text:
            meanings[character] = [hun, on_text, kun_text]

    return {
        "schema_version": 1,
        "database_date": created,
        "sources": {
            "hun": HANJA_SOURCE,
            "readings": KANJIDIC_SOURCE,
            "variants": UNIHAN_SOURCE,
        },
        "fields": ["hun", "on", "kun"],
        "license": "Korean 훈음 BSD-3-Clause (libhangul); readings CC BY-SA 4.0 (KANJIDIC2)",
        "meanings": meanings,
    }, unresolved


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--kanjidic", default=KANJIDIC_SOURCE)
    parser.add_argument("--hanja", default=HANJA_SOURCE)
    parser.add_argument("--unihan", default=UNIHAN_SOURCE)
    parser.add_argument("--kanji-dir", type=Path, default=Path("kanji"))
    parser.add_argument("--output", type=Path, default=Path("web/data/kanji-meanings.json"))
    args = parser.parse_args()

    kanjidic = fetch(args.kanjidic)
    if kanjidic[:2] == b"\x1f\x8b":
        kanjidic = gzip.decompress(kanjidic)
    index, unresolved = build_index(
        kanjidic,
        fetch(args.hanja).decode("utf-8"),
        parse_unihan(fetch(args.unihan)),
        available_characters(args.kanji_dir),
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(index, ensure_ascii=False, separators=(",", ":")),
        encoding="utf-8",
    )
    with_hun = sum(1 for record in index["meanings"].values() if record[0])
    print(
        f"wrote {len(index['meanings'])} kanji ({with_hun} with 훈음, "
        f"{len(unresolved)} without) to {args.output}"
    )


if __name__ == "__main__":
    main()
