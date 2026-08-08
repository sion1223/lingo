"""Versioned confusion registry, deterministic fixtures, and geometry mining.

The current scorers predict writing quality against one supplied template.  This
module deliberately keeps that legacy score separate from character identity:
fixtures always carry both a task target and a competing character, and callers
must evaluate both templates to obtain a pairwise margin.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

import numpy as np
import yaml

from .kanjivg import resample_stroke

REGISTRY_SCHEMA_VERSION = "confusion_registry.v1"
FIXTURE_GENERATOR_VERSION = "confusion_fixture.v1"
DEFAULT_REGISTRY_PATH = Path("configs/confusions/kana_seed_v1.yaml")

EVIDENCE_CODES = frozenset(
    {
        "START_TOO_HIGH",
        "START_TOO_LOW",
        "START_TOO_LEFT",
        "START_TOO_RIGHT",
        "END_TOO_HIGH",
        "END_TOO_LOW",
        "STROKE_TOO_LONG",
        "STROKE_TOO_SHORT",
        "STROKE_TOO_VERTICAL",
        "STROKE_TOO_HORIZONTAL",
        "STROKE_ANGLE_MISMATCH",
        "CURVE_TOO_EARLY",
        "CURVE_TOO_LATE",
        "TERMINAL_HOOK_WRONG_DIRECTION",
        "INTER_STROKE_GAP_TOO_SMALL",
        "INTER_STROKE_GAP_TOO_LARGE",
        "CHARACTER_RESEMBLES_COMPETITOR",
        "AMBIGUOUS_BETWEEN_CHARACTERS",
    }
)

_ID_PATTERN = re.compile(r"^[a-z][a-z0-9_]{2,95}$")
_PROFILE_KEY_PATTERN = re.compile(r"^[a-z][a-z0-9_]*$")

FixtureLabel = Literal["target", "competitor", "ambiguous"]


class ConfusionRegistryError(ValueError):
    """Raised when the versioned registry violates its closed contract."""


@dataclass(frozen=True)
class FixturePolicy:
    generator_version: str
    samples_per_direction: int
    style_severity: float
    morph_alphas: tuple[float, ...]
    baseline_split: str
    split_seeds: Mapping[str, int]


@dataclass(frozen=True)
class ConfusionDirection:
    target_char: str
    competitor_char: str
    critical_stroke: int
    critical_region: str
    target_profile: Mapping[str, str]
    competitor_profile: Mapping[str, str]
    evidence_codes: tuple[str, ...]

    @property
    def direction_id(self) -> str:
        return f"u{ord(self.target_char):05x}_u{ord(self.competitor_char):05x}"


@dataclass(frozen=True)
class ConfusionPair:
    pair_id: str
    script: str
    characters: tuple[str, str]
    source: str
    directions: tuple[ConfusionDirection, ConfusionDirection]


@dataclass(frozen=True)
class ConfusionRegistry:
    schema_version: str
    registry_id: str
    version: int
    fixture_policy: FixturePolicy
    pairs: tuple[ConfusionPair, ...]
    source_path: Path
    sha256: str


@dataclass(frozen=True)
class ConfusionFixture:
    fixture_id: str
    pair_id: str
    split: str
    target_char: str
    competitor_char: str
    written_char: str | None
    kind: str
    label: FixtureLabel
    critical_stroke: int
    seed: int
    morph_alpha: float | None
    strokes: tuple[np.ndarray, ...]


@dataclass(frozen=True)
class TemplateNeighbor:
    char: str
    distance: float
    stroke_count: int


def _mapping(value, location: str) -> dict:
    if not isinstance(value, dict):
        raise ConfusionRegistryError(f"{location} must be an object")
    return value


def _closed_keys(
    value: Mapping,
    *,
    required: set[str],
    optional: set[str] = frozenset(),
    location: str,
) -> None:
    missing = required - set(value)
    extra = set(value) - required - optional
    if missing:
        raise ConfusionRegistryError(
            f"{location} is missing fields: {', '.join(sorted(missing))}"
        )
    if extra:
        raise ConfusionRegistryError(
            f"{location} has unknown fields: {', '.join(sorted(extra))}"
        )


def script_for_char(char: str) -> str | None:
    """Return the supported script family for exactly one Unicode code point."""
    if not isinstance(char, str) or len(char) != 1:
        return None
    cp = ord(char)
    if 0x3040 <= cp <= 0x309F:
        return "hiragana"
    if 0x30A0 <= cp <= 0x30FF or 0x31F0 <= cp <= 0x31FF:
        return "katakana"
    if (
        0x3400 <= cp <= 0x4DBF
        or 0x4E00 <= cp <= 0x9FFF
        or 0x20000 <= cp <= 0x2FA1F
        or 0x30000 <= cp <= 0x323AF
    ):
        return "cjk"
    return None


def canonical_sha256(value) -> str:
    payload = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _validate_profile(value, location: str) -> dict[str, str]:
    profile = _mapping(value, location)
    if not profile:
        raise ConfusionRegistryError(f"{location} must not be empty")
    result: dict[str, str] = {}
    for key, item in profile.items():
        if not isinstance(key, str) or not _PROFILE_KEY_PATTERN.fullmatch(key):
            raise ConfusionRegistryError(f"{location} has invalid key {key!r}")
        if not isinstance(item, str) or not 1 <= len(item) <= 64:
            raise ConfusionRegistryError(f"{location}.{key} must be a short string")
        result[key] = item
    return result


def _parse_direction(value, location: str) -> ConfusionDirection:
    item = _mapping(value, location)
    required = {
        "target_char",
        "competitor_char",
        "critical_stroke",
        "critical_region",
        "target_profile",
        "competitor_profile",
        "evidence_codes",
    }
    _closed_keys(item, required=required, location=location)
    target = item["target_char"]
    competitor = item["competitor_char"]
    if script_for_char(target) is None or script_for_char(competitor) is None:
        raise ConfusionRegistryError(
            f"{location} target and competitor must be supported single characters"
        )
    if target == competitor:
        raise ConfusionRegistryError(f"{location} target and competitor must differ")
    critical_stroke = item["critical_stroke"]
    if (
        not isinstance(critical_stroke, int)
        or isinstance(critical_stroke, bool)
        or not 0 <= critical_stroke <= 63
    ):
        raise ConfusionRegistryError(f"{location}.critical_stroke is invalid")
    critical_region = item["critical_region"]
    if not isinstance(critical_region, str) or not _ID_PATTERN.fullmatch(
        critical_region
    ):
        raise ConfusionRegistryError(f"{location}.critical_region is invalid")
    codes = item["evidence_codes"]
    if not isinstance(codes, list) or not codes:
        raise ConfusionRegistryError(f"{location}.evidence_codes must not be empty")
    if len(codes) != len(set(codes)):
        raise ConfusionRegistryError(f"{location}.evidence_codes contains duplicates")
    unknown_codes = set(codes) - EVIDENCE_CODES
    if unknown_codes:
        raise ConfusionRegistryError(
            f"{location} has unknown evidence codes: {sorted(unknown_codes)}"
        )
    return ConfusionDirection(
        target_char=target,
        competitor_char=competitor,
        critical_stroke=critical_stroke,
        critical_region=critical_region,
        target_profile=_validate_profile(
            item["target_profile"], f"{location}.target_profile"
        ),
        competitor_profile=_validate_profile(
            item["competitor_profile"], f"{location}.competitor_profile"
        ),
        evidence_codes=tuple(codes),
    )


def _parse_fixture_policy(value) -> FixturePolicy:
    item = _mapping(value, "fixture_policy")
    required = {
        "generator_version",
        "samples_per_direction",
        "style_severity",
        "morph_alphas",
        "baseline_split",
        "split_seeds",
    }
    _closed_keys(item, required=required, location="fixture_policy")
    if item["generator_version"] != FIXTURE_GENERATOR_VERSION:
        raise ConfusionRegistryError("unsupported fixture generator version")
    count = item["samples_per_direction"]
    if not isinstance(count, int) or isinstance(count, bool) or not 1 <= count <= 1000:
        raise ConfusionRegistryError("samples_per_direction must be in 1..1000")
    severity = item["style_severity"]
    if (
        not isinstance(severity, (int, float))
        or isinstance(severity, bool)
        or not math.isfinite(float(severity))
        or not 0 <= float(severity) <= 1
    ):
        raise ConfusionRegistryError("style_severity must be in 0..1")
    alphas = item["morph_alphas"]
    if not isinstance(alphas, list) or not alphas:
        raise ConfusionRegistryError("morph_alphas must not be empty")
    parsed_alphas = tuple(float(alpha) for alpha in alphas)
    if any(not math.isfinite(alpha) or not 0 < alpha < 1 for alpha in parsed_alphas):
        raise ConfusionRegistryError("morph_alphas must be finite and inside 0..1")
    if tuple(sorted(set(parsed_alphas))) != parsed_alphas:
        raise ConfusionRegistryError("morph_alphas must be sorted and unique")
    split_seeds = _mapping(item["split_seeds"], "fixture_policy.split_seeds")
    _closed_keys(
        split_seeds,
        required={"train", "validation", "test"},
        location="fixture_policy.split_seeds",
    )
    for name, seed in split_seeds.items():
        if not isinstance(seed, int) or isinstance(seed, bool) or seed < 0:
            raise ConfusionRegistryError(f"split seed {name!r} must be non-negative")
    if len(set(split_seeds.values())) != 3:
        raise ConfusionRegistryError("train/validation/test seed families must differ")
    baseline_split = item["baseline_split"]
    if baseline_split not in split_seeds:
        raise ConfusionRegistryError("baseline_split must name a declared split")
    return FixturePolicy(
        generator_version=item["generator_version"],
        samples_per_direction=count,
        style_severity=float(severity),
        morph_alphas=parsed_alphas,
        baseline_split=baseline_split,
        split_seeds=dict(split_seeds),
    )


def load_confusion_registry(path: str | Path = DEFAULT_REGISTRY_PATH) -> ConfusionRegistry:
    """Load and strictly validate a YAML registry without accepting extensions."""
    source = Path(path)
    try:
        raw = yaml.safe_load(source.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        raise ConfusionRegistryError(f"cannot read registry {source}: {exc}") from exc
    root = _mapping(raw, "registry")
    _closed_keys(
        root,
        required={
            "schema_version",
            "registry_id",
            "version",
            "fixture_policy",
            "pairs",
        },
        location="registry",
    )
    if root["schema_version"] != REGISTRY_SCHEMA_VERSION:
        raise ConfusionRegistryError("unsupported confusion registry schema version")
    registry_id = root["registry_id"]
    if not isinstance(registry_id, str) or not _ID_PATTERN.fullmatch(registry_id):
        raise ConfusionRegistryError("registry_id is invalid")
    version = root["version"]
    if not isinstance(version, int) or isinstance(version, bool) or version < 1:
        raise ConfusionRegistryError("registry version must be a positive integer")
    raw_pairs = root["pairs"]
    if not isinstance(raw_pairs, list) or not raw_pairs:
        raise ConfusionRegistryError("registry pairs must not be empty")
    pairs: list[ConfusionPair] = []
    pair_ids: set[str] = set()
    pair_char_sets: set[frozenset[str]] = set()
    for pair_index, raw_pair in enumerate(raw_pairs):
        location = f"pairs[{pair_index}]"
        item = _mapping(raw_pair, location)
        _closed_keys(
            item,
            required={"pair_id", "script", "characters", "source", "directions"},
            location=location,
        )
        pair_id = item["pair_id"]
        if not isinstance(pair_id, str) or not _ID_PATTERN.fullmatch(pair_id):
            raise ConfusionRegistryError(f"{location}.pair_id is invalid")
        if pair_id in pair_ids:
            raise ConfusionRegistryError(f"duplicate pair_id {pair_id!r}")
        pair_ids.add(pair_id)
        script = item["script"]
        if script not in {"hiragana", "katakana", "cjk"}:
            raise ConfusionRegistryError(f"{location}.script is invalid")
        chars = item["characters"]
        if (
            not isinstance(chars, list)
            or len(chars) != 2
            or len(set(chars)) != 2
            or any(script_for_char(char) != script for char in chars)
        ):
            raise ConfusionRegistryError(
                f"{location}.characters must be two unique {script} characters"
            )
        frozen_chars = frozenset(chars)
        if frozen_chars in pair_char_sets:
            raise ConfusionRegistryError(f"duplicate character pair at {location}")
        pair_char_sets.add(frozen_chars)
        source_name = item["source"]
        if source_name not in {"manual_seed", "template_mined", "model_mined"}:
            raise ConfusionRegistryError(f"{location}.source is invalid")
        raw_directions = item["directions"]
        if not isinstance(raw_directions, list) or len(raw_directions) != 2:
            raise ConfusionRegistryError(f"{location}.directions must contain two items")
        directions = tuple(
            _parse_direction(value, f"{location}.directions[{index}]")
            for index, value in enumerate(raw_directions)
        )
        expected = {(chars[0], chars[1]), (chars[1], chars[0])}
        actual = {(d.target_char, d.competitor_char) for d in directions}
        if actual != expected:
            raise ConfusionRegistryError(
                f"{location}.directions must cover both pair directions exactly"
            )
        pairs.append(
            ConfusionPair(
                pair_id=pair_id,
                script=script,
                characters=(chars[0], chars[1]),
                source=source_name,
                directions=directions,
            )
        )
    return ConfusionRegistry(
        schema_version=root["schema_version"],
        registry_id=registry_id,
        version=version,
        fixture_policy=_parse_fixture_policy(root["fixture_policy"]),
        pairs=tuple(pairs),
        source_path=source.resolve(),
        sha256=canonical_sha256(root),
    )


def _arc_length(stroke: np.ndarray) -> float:
    if len(stroke) < 2:
        return 0.0
    return float(np.linalg.norm(np.diff(stroke, axis=0), axis=1).sum())


def template_distance(
    first: Sequence[np.ndarray],
    second: Sequence[np.ndarray],
    *,
    points_per_stroke: int = 24,
) -> float:
    """Deterministic aligned-stroke distance used only for candidate mining.

    This is a transparent geometry baseline, not a learned identity score.  A
    stroke-count mismatch receives an explicit penalty instead of a fabricated
    correspondence.
    """
    if not first or not second:
        raise ValueError("templates must contain at least one stroke")
    if len(first) != len(second):
        return 1.0 + 0.25 * abs(len(first) - len(second))
    distances = []
    for stroke_a, stroke_b in zip(first, second):
        a = resample_stroke(np.asarray(stroke_a, dtype=np.float64), points_per_stroke)
        b = resample_stroke(np.asarray(stroke_b, dtype=np.float64), points_per_stroke)
        path = float(np.linalg.norm(a - b, axis=1).mean())
        endpoints = float(
            (np.linalg.norm(a[0] - b[0]) + np.linalg.norm(a[-1] - b[-1])) / 2
        )
        centroid = float(np.linalg.norm(a.mean(axis=0) - b.mean(axis=0)))
        length_delta = abs(math.log((_arc_length(a) + 1e-8) / (_arc_length(b) + 1e-8)))
        distances.append(
            0.65 * path + 0.15 * endpoints + 0.10 * centroid + 0.10 * length_delta
        )
    return float(np.mean(distances))


def mine_template_neighbors(
    templates: Mapping[str, Sequence[np.ndarray]],
    *,
    top_k: int = 10,
    same_script: bool = True,
    same_stroke_count: bool = True,
) -> dict[str, tuple[TemplateNeighbor, ...]]:
    """Return nearest template candidates under the declared geometry metric."""
    if top_k < 1:
        raise ValueError("top_k must be positive")
    result: dict[str, tuple[TemplateNeighbor, ...]] = {}
    for char, template in sorted(templates.items(), key=lambda item: ord(item[0])):
        candidates: list[TemplateNeighbor] = []
        for other, other_template in templates.items():
            if other == char:
                continue
            if same_script and script_for_char(other) != script_for_char(char):
                continue
            if same_stroke_count and len(other_template) != len(template):
                continue
            candidates.append(
                TemplateNeighbor(
                    char=other,
                    distance=template_distance(template, other_template),
                    stroke_count=len(other_template),
                )
            )
        candidates.sort(key=lambda item: (item.distance, ord(item.char)))
        result[char] = tuple(candidates[:top_k])
    return result


def _stable_seed(split_seed: int, *parts: object) -> int:
    value = "|".join([str(split_seed), *(str(part) for part in parts)])
    digest = hashlib.sha256(value.encode("utf-8")).digest()
    return int.from_bytes(digest[:8], "big", signed=False)


def _smooth_noise(count: int, rng: np.random.Generator, sigma: float) -> np.ndarray:
    if sigma <= 0:
        return np.zeros((count, 2), dtype=np.float64)
    control = rng.normal(0.0, sigma, (4, 2))
    positions = np.linspace(0.0, 3.0, count)
    return np.column_stack(
        [np.interp(positions, np.arange(4), control[:, axis]) for axis in range(2)]
    )


def style_variant(
    template: Sequence[np.ndarray],
    rng: np.random.Generator,
    severity: float,
) -> tuple[np.ndarray, ...]:
    """Apply mild style variation while preserving stroke order and direction."""
    if not 0 <= severity <= 1:
        raise ValueError("severity must be in 0..1")
    if severity == 0:
        return tuple(
            np.asarray(stroke, dtype=np.float64).copy() for stroke in template
        )
    theta = float(rng.normal(0.0, 0.08 * severity))
    scale = float(1.0 + rng.normal(0.0, 0.06 * severity))
    shift = rng.normal(0.0, 0.025 * severity, 2)
    cosine, sine = math.cos(theta), math.sin(theta)
    rotation = np.asarray([[cosine, -sine], [sine, cosine]])
    result = []
    for stroke in template:
        points = np.asarray(stroke, dtype=np.float64).copy()
        local_shift = rng.normal(0.0, 0.012 * severity, 2)
        noise = _smooth_noise(len(points), rng, 0.012 * severity)
        points = (points - 0.5) @ rotation.T * scale + 0.5
        points = points + shift + local_shift + noise
        result.append(np.clip(points, 0.0, 1.0))
    return tuple(result)


def interpolate_templates(
    target: Sequence[np.ndarray],
    competitor: Sequence[np.ndarray],
    alpha: float,
) -> tuple[np.ndarray, ...]:
    if not 0 <= alpha <= 1:
        raise ValueError("alpha must be in 0..1")
    if len(target) != len(competitor):
        raise ValueError("morph fixtures require equal stroke counts")
    result = []
    for target_stroke, competitor_stroke in zip(target, competitor):
        count = max(len(target_stroke), len(competitor_stroke))
        first = resample_stroke(target_stroke, count)
        second = resample_stroke(competitor_stroke, count)
        result.append(first * (1.0 - alpha) + second * alpha)
    return tuple(result)


def transplant_critical_stroke(
    target: Sequence[np.ndarray],
    competitor: Sequence[np.ndarray],
    critical_stroke: int,
) -> tuple[np.ndarray, ...]:
    if not 0 <= critical_stroke < len(target):
        raise ValueError("critical stroke is outside the target template")
    if critical_stroke >= len(competitor):
        raise ValueError("critical stroke is outside the competitor template")
    result = [np.asarray(stroke, dtype=np.float64).copy() for stroke in target]
    result[critical_stroke] = resample_stroke(
        np.asarray(competitor[critical_stroke], dtype=np.float64),
        len(result[critical_stroke]),
    )
    return tuple(result)


def _fixture(
    *,
    pair: ConfusionPair,
    direction: ConfusionDirection,
    split: str,
    split_seed: int,
    sample_index: int,
    kind: str,
    label: FixtureLabel,
    written_char: str | None,
    morph_alpha: float | None,
    source: Sequence[np.ndarray],
    severity: float,
) -> ConfusionFixture:
    seed = _stable_seed(
        split_seed,
        pair.pair_id,
        direction.direction_id,
        sample_index,
        kind,
    )
    rng = np.random.default_rng(seed)
    suffix = f"__{split}__{kind}__{sample_index:03d}"
    return ConfusionFixture(
        fixture_id=f"{pair.pair_id}__{direction.direction_id}{suffix}",
        pair_id=pair.pair_id,
        split=split,
        target_char=direction.target_char,
        competitor_char=direction.competitor_char,
        written_char=written_char,
        kind=kind,
        label=label,
        critical_stroke=direction.critical_stroke,
        seed=seed,
        morph_alpha=morph_alpha,
        strokes=style_variant(source, rng, severity),
    )


def generate_confusion_fixtures(
    registry: ConfusionRegistry,
    template_loader: Callable[[str], Sequence[np.ndarray]],
    *,
    split: str | None = None,
) -> tuple[ConfusionFixture, ...]:
    """Generate clean, substitution, critical, and boundary fixtures."""
    policy = registry.fixture_policy
    selected_split = split or policy.baseline_split
    if selected_split not in policy.split_seeds:
        raise ValueError(f"unknown fixture split {selected_split!r}")
    split_seed = policy.split_seeds[selected_split]
    template_cache: dict[str, tuple[np.ndarray, ...]] = {}

    def load(char: str) -> tuple[np.ndarray, ...]:
        if char not in template_cache:
            template_cache[char] = tuple(
                np.asarray(stroke, dtype=np.float64) for stroke in template_loader(char)
            )
        return template_cache[char]

    fixtures: list[ConfusionFixture] = []
    for pair in registry.pairs:
        for direction in pair.directions:
            target = load(direction.target_char)
            competitor = load(direction.competitor_char)
            if direction.critical_stroke >= min(len(target), len(competitor)):
                raise ConfusionRegistryError(
                    f"{pair.pair_id} critical stroke is outside its templates"
                )
            critical = transplant_critical_stroke(
                target, competitor, direction.critical_stroke
            )
            morphs = {
                alpha: interpolate_templates(target, competitor, alpha)
                for alpha in policy.morph_alphas
            }
            for sample_index in range(policy.samples_per_direction):
                base = {
                    "pair": pair,
                    "direction": direction,
                    "split": selected_split,
                    "split_seed": split_seed,
                    "sample_index": sample_index,
                    "severity": policy.style_severity,
                }
                fixtures.append(
                    _fixture(
                        **base,
                        kind="clean_target",
                        label="target",
                        written_char=direction.target_char,
                        morph_alpha=None,
                        source=target,
                    )
                )
                fixtures.append(
                    _fixture(
                        **base,
                        kind="full_competitor",
                        label="competitor",
                        written_char=direction.competitor_char,
                        morph_alpha=None,
                        source=competitor,
                    )
                )
                fixtures.append(
                    _fixture(
                        **base,
                        kind="critical_transplant",
                        label="competitor",
                        written_char=direction.competitor_char,
                        morph_alpha=None,
                        source=critical,
                    )
                )
                for alpha, morph in morphs.items():
                    # The boundary ladder has no human-reviewed decision rule.
                    # Keep every interpolation weakly labeled as ambiguous.
                    fixtures.append(
                        _fixture(
                            **base,
                            kind=f"morph_{round(alpha * 100):03d}",
                            label="ambiguous",
                            written_char=None,
                            morph_alpha=alpha,
                            source=morph,
                        )
                    )
    ids = [fixture.fixture_id for fixture in fixtures]
    if len(ids) != len(set(ids)):
        raise RuntimeError("fixture generator produced duplicate IDs")
    return tuple(fixtures)


def fixture_seed_sha256(fixtures: Sequence[ConfusionFixture]) -> str:
    manifest = [
        {
            "fixture_id": fixture.fixture_id,
            "split": fixture.split,
            "seed": fixture.seed,
            "kind": fixture.kind,
            "label": fixture.label,
            "morph_alpha": fixture.morph_alpha,
        }
        for fixture in fixtures
    ]
    return canonical_sha256(manifest)


def fixture_content_sha256(fixtures: Sequence[ConfusionFixture]) -> str:
    digest = hashlib.sha256()
    for fixture in fixtures:
        digest.update(fixture.fixture_id.encode("utf-8"))
        digest.update(fixture.label.encode("ascii"))
        for stroke in fixture.strokes:
            array = np.ascontiguousarray(stroke, dtype="<f8")
            digest.update(np.asarray(array.shape, dtype="<i8").tobytes())
            digest.update(array.tobytes())
    return digest.hexdigest()
