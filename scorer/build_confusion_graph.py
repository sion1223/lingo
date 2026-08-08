"""Mine transparent template-neighbor candidates for the confusion registry."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from .confusions import (
    canonical_sha256,
    load_confusion_registry,
    mine_template_neighbors,
    script_for_char,
)
from .kanjivg import load_char


def _characters_for_scope(kanji_dir: Path, scope: str, registry) -> list[str]:
    if scope == "registry":
        return sorted(
            {char for pair in registry.pairs for char in pair.characters},
            key=ord,
        )
    characters = []
    for path in sorted(kanji_dir.glob("*.svg")):
        try:
            codepoint = int(path.stem, 16)
            char = chr(codepoint)
        except (ValueError, OverflowError):
            continue
        script = script_for_char(char)
        if script is None:
            continue
        if scope == "kana" and script not in {"hiragana", "katakana"}:
            continue
        characters.append(char)
    return characters


def build_graph(
    *,
    kanji_dir: str | Path,
    registry_path: str | Path,
    scope: str = "kana",
    top_k: int = 10,
    same_script: bool = True,
    same_stroke_count: bool = True,
) -> dict:
    directory = Path(kanji_dir)
    registry = load_confusion_registry(registry_path)
    templates = {}
    parse_errors = []
    for char in _characters_for_scope(directory, scope, registry):
        try:
            templates[char] = load_char(directory, char)
        except (OSError, ValueError) as exc:
            parse_errors.append(
                {"char": char, "codepoint": f"U+{ord(char):04X}", "error": type(exc).__name__}
            )
    required_chars = {char for pair in registry.pairs for char in pair.characters}
    missing = sorted(required_chars - set(templates), key=ord)
    if missing:
        rendered = ", ".join(f"U+{ord(char):04X}" for char in missing)
        raise ValueError(f"registry templates are missing from graph scope: {rendered}")
    neighbors = mine_template_neighbors(
        templates,
        top_k=top_k,
        same_script=same_script,
        same_stroke_count=same_stroke_count,
    )
    graph = {
        char: [
            {
                "char": neighbor.char,
                "codepoint": f"U+{ord(neighbor.char):04X}",
                "distance": round(neighbor.distance, 10),
                "stroke_count": neighbor.stroke_count,
            }
            for neighbor in values
        ]
        for char, values in neighbors.items()
    }
    seeded_ranks = []
    for pair in registry.pairs:
        for direction in pair.directions:
            candidates = [item.char for item in neighbors[direction.target_char]]
            rank = (
                candidates.index(direction.competitor_char) + 1
                if direction.competitor_char in candidates
                else None
            )
            seeded_ranks.append(
                {
                    "pair_id": pair.pair_id,
                    "target_char": direction.target_char,
                    "competitor_char": direction.competitor_char,
                    "rank_within_top_k": rank,
                }
            )
    payload = {
        "schema_version": "confusion_graph.v1",
        "registry_id": registry.registry_id,
        "registry_sha256": registry.sha256,
        "scope": scope,
        "method": {
            "name": "aligned_template_geometry.v1",
            "top_k": top_k,
            "same_script": same_script,
            "same_stroke_count": same_stroke_count,
        },
        "character_count": len(templates),
        "parse_errors": parse_errors,
        "seeded_pair_ranks": seeded_ranks,
        "neighbors": graph,
    }
    payload["graph_sha256"] = canonical_sha256(payload)
    return payload


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--kanji-dir", default="kanji")
    parser.add_argument(
        "--registry", default="configs/confusions/kana_seed_v1.yaml"
    )
    parser.add_argument("--scope", choices=("registry", "kana", "all"), default="kana")
    parser.add_argument("--top-k", type=int, default=10)
    parser.add_argument("--cross-script", action="store_true")
    parser.add_argument("--allow-stroke-count-mismatch", action="store_true")
    parser.add_argument("--output")
    args = parser.parse_args()
    payload = build_graph(
        kanji_dir=args.kanji_dir,
        registry_path=args.registry,
        scope=args.scope,
        top_k=args.top_k,
        same_script=not args.cross_script,
        same_stroke_count=not args.allow_stroke_count_mismatch,
    )
    rendered = json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
    if args.output:
        path = Path(args.output)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(rendered, encoding="utf-8")
    else:
        print(rendered, end="")


if __name__ == "__main__":
    main()
