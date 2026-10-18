"""Build a small offline practice pack from existing licensed KanjiVG paths.

Run from the repository root: python scripts/build_learning_assets.py
Requires the existing NumPy parser and Node (for the menu's source of truth).
No network, font outlines, model labels, or checkpoint changes are involved.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import re
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scorer.kanjivg import load_display_paths, normalize_strokes, parse_svg  # noqa: E402


def build_pack():
    result = subprocess.run([
        "node", "--input-type=module", "-e",
        'import { LEARNING_ENTRIES, practiceCharacters } from "./web/learning.js"; '
        'console.log(JSON.stringify([...new Set(LEARNING_ENTRIES.flatMap(practiceCharacters))]));',
    ], cwd=ROOT, check=True, capture_output=True, encoding="utf-8")
    chars = set(json.loads(result.stdout)) | set("永水木日語令")
    registry = json.loads((ROOT / "configs/template-variants.json").read_text(encoding="utf-8"))
    pack = {
        "schema_version": 1,
        "license": "CC-BY-SA-3.0",
        "attribution": "KanjiVG — Copyright (C) 2009/2010/2011 Ulrich Apel and contributors",
        "source_url": "https://kanjivg.tagaini.net/",
        "characters": {},
    }
    for char in sorted(chars):
        base = {"id": "default", "file": f"{ord(char):05x}.svg", "label": "기본 획순", "usage": "practice", "remote_scoring": True}
        reviewed = registry["characters"].get(char, {})
        forms = []
        for definition in [base, *reviewed.get("forms", [])]:
            path = ROOT / "kanji" / definition["file"]
            digest = hashlib.sha256(path.read_bytes()).hexdigest()
            expected = reviewed.get("base_sha256") if definition["id"] == "default" else definition["source_sha256"]
            if expected and expected != digest:
                raise ValueError(f"Review required: source changed: {path.name}")
            raw = parse_svg(path)
            display = load_display_paths(path)
            if not raw or len(raw) != len(display["paths"]):
                raise ValueError(f"Incomplete paths: {path.name}")
            types = re.findall(r'<path[^>]*kvg:type="([^"]+)"', path.read_text(encoding="utf-8"))
            if definition.get("stroke_count", len(raw)) != len(raw) or definition.get("stroke_types", types) != types:
                raise ValueError(f"Review required: stroke order/types changed: {path.name}")
            forms.append({
                **definition, "source_sha256": digest, "stroke_count": len(raw),
                "stroke_types": types, "display": display,
                "strokes": [stroke.round(6).tolist() for stroke in normalize_strokes(raw)],
            })
        pack["characters"][char] = {"forms": forms}
    return pack


if __name__ == "__main__":
    output = ROOT / "web/data/learning-practice.json"
    pack = build_pack()
    output.write_text(json.dumps(pack, ensure_ascii=False, separators=(",", ":")) + "\n", encoding="utf-8")
    print(f"{len(pack['characters'])} characters, {output.stat().st_size} bytes")
