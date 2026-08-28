"""Model-neutral training contract for character-confusion samples.

The contract keeps three different concepts explicit:

* ``written_char`` is the known identity of the submitted writing.
* ``target_char`` is the character requested by the exercise.
* ``quality_for_written_char`` measures writing quality independently from
  whether that identity matches the requested character.

Coordinate and vision pipelines can consume this object so pair labels cannot
silently drift between model-specific datasets.
"""

from __future__ import annotations

import math
import re
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from typing import Literal

import numpy as np

from .confusions import (
    EVIDENCE_CODES,
    ConfusionDirection,
    ConfusionFixture,
    ConfusionRegistry,
    generate_confusion_fixtures,
    script_for_char,
)
from .synth import compute_labels

CONFUSION_SAMPLE_SCHEMA_VERSION = "confusion_sample.v1"
FULL_COMPETITOR_TRAINING_KINDS = frozenset(
    {"clean_target", "full_competitor"}
)

SampleLabel = Literal["target", "competitor", "ambiguous"]

_ID_PATTERN = re.compile(r"^[a-z][a-z0-9_]{2,255}$")
_KIND_PATTERN = re.compile(r"^[a-z][a-z0-9_]{2,63}$")
_SPLITS = frozenset({"train", "validation", "test"})
_AMBIGUITY_EVIDENCE = "AMBIGUOUS_BETWEEN_CHARACTERS"
_MANIFEST_FIELDS = frozenset(
    {
        "schema_version",
        "sample_id",
        "split",
        "kind",
        "user_strokes",
        "target_template",
        "written_char",
        "target_char",
        "competitor_char",
        "is_target",
        "quality_for_written_char",
        "target_match",
        "pair_id",
        "critical_strokes",
        "evidence_labels",
        "ambiguity",
        "seed",
        "morph_alpha",
    }
)


class ConfusionSampleError(ValueError):
    """Raised when a sample contradicts the common confusion contract."""


def _finite_unit_interval(value: object, name: str) -> float | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float, np.number)):
        raise ConfusionSampleError(f"{name} must be a number in 0..1 or null")
    parsed = float(value)
    if not math.isfinite(parsed) or not 0 <= parsed <= 1:
        raise ConfusionSampleError(f"{name} must be a finite number in 0..1")
    return parsed


def _freeze_strokes(
    value: Sequence[np.ndarray], name: str
) -> tuple[np.ndarray, ...]:
    if isinstance(value, (str, bytes)):
        raise ConfusionSampleError(f"{name} must be a sequence of stroke arrays")
    try:
        strokes = tuple(value)
    except TypeError as exc:
        raise ConfusionSampleError(
            f"{name} must be a sequence of stroke arrays"
        ) from exc
    if not 1 <= len(strokes) <= 64:
        raise ConfusionSampleError(f"{name} must contain 1..64 strokes")

    frozen: list[np.ndarray] = []
    for index, stroke in enumerate(strokes):
        try:
            points = np.asarray(stroke, dtype=np.float64)
        except (TypeError, ValueError) as exc:
            raise ConfusionSampleError(
                f"{name}[{index}] must contain numeric points"
            ) from exc
        if points.ndim != 2 or points.shape[1:] != (2,) or len(points) < 2:
            raise ConfusionSampleError(
                f"{name}[{index}] must have shape (N, 2) with N >= 2"
            )
        if not np.isfinite(points).all():
            raise ConfusionSampleError(f"{name}[{index}] must contain finite points")
        # An immutable bytes owner prevents callers from re-enabling the
        # WRITEABLE flag and mutating a frozen sample behind the dataclass.
        buffer = np.ascontiguousarray(points, dtype=np.float64).tobytes()
        owned = np.frombuffer(buffer, dtype=np.float64).reshape(points.shape)
        frozen.append(owned)
    return tuple(frozen)


def _validate_char(value: object, name: str) -> str:
    if not isinstance(value, str) or script_for_char(value) is None:
        raise ConfusionSampleError(
            f"{name} must be one supported Japanese character"
        )
    return value


@dataclass(frozen=True, slots=True)
class ConfusionSample:
    """One immutable positive, hard-negative, or unresolved boundary sample."""

    sample_id: str
    split: str
    kind: str
    user_strokes: tuple[np.ndarray, ...]
    target_template: tuple[np.ndarray, ...]
    written_char: str | None
    target_char: str
    competitor_char: str
    is_target: bool | None
    quality_for_written_char: float | None
    target_match: float | None
    pair_id: str
    critical_strokes: tuple[int, ...]
    evidence_labels: tuple[str, ...]
    ambiguity: bool
    seed: int | None
    morph_alpha: float | None
    schema_version: str = field(
        default=CONFUSION_SAMPLE_SCHEMA_VERSION, init=False, repr=False
    )

    def __post_init__(self) -> None:
        if not isinstance(self.sample_id, str) or not _ID_PATTERN.fullmatch(
            self.sample_id
        ):
            raise ConfusionSampleError("sample_id is invalid")
        if not isinstance(self.pair_id, str) or not _ID_PATTERN.fullmatch(
            self.pair_id
        ):
            raise ConfusionSampleError("pair_id is invalid")
        if self.split not in _SPLITS:
            raise ConfusionSampleError("split must be train, validation, or test")
        if not isinstance(self.kind, str) or not _KIND_PATTERN.fullmatch(self.kind):
            raise ConfusionSampleError("kind is invalid")

        target_char = _validate_char(self.target_char, "target_char")
        competitor_char = _validate_char(self.competitor_char, "competitor_char")
        if target_char == competitor_char:
            raise ConfusionSampleError("target_char and competitor_char must differ")
        if script_for_char(target_char) != script_for_char(competitor_char):
            raise ConfusionSampleError(
                "target_char and competitor_char must use the same script"
            )
        if self.written_char is not None:
            _validate_char(self.written_char, "written_char")

        if not isinstance(self.ambiguity, bool):
            raise ConfusionSampleError("ambiguity must be a boolean")
        if self.ambiguity:
            if self.written_char is not None or self.is_target is not None:
                raise ConfusionSampleError(
                    "ambiguous sample must leave written_char and is_target null"
                )
        else:
            if self.written_char is None or not isinstance(self.is_target, bool):
                raise ConfusionSampleError(
                    "resolved sample must declare written_char and boolean is_target"
                )
            if self.is_target and self.written_char != target_char:
                raise ConfusionSampleError(
                    "target sample written_char must equal target_char"
                )
            if not self.is_target and self.written_char != competitor_char:
                raise ConfusionSampleError(
                    "competitor sample written_char must equal competitor_char"
                )

        quality = _finite_unit_interval(
            self.quality_for_written_char, "quality_for_written_char"
        )
        target_match = _finite_unit_interval(self.target_match, "target_match")
        if self.written_char is None and quality is not None:
            raise ConfusionSampleError(
                "quality_for_written_char must be null when written_char is null"
            )
        object.__setattr__(self, "quality_for_written_char", quality)
        object.__setattr__(self, "target_match", target_match)

        if self.seed is not None and (
            not isinstance(self.seed, int)
            or isinstance(self.seed, bool)
            or not 0 <= self.seed <= (2**64 - 1)
        ):
            raise ConfusionSampleError("seed must be an unsigned 64-bit integer or null")

        morph_alpha = self.morph_alpha
        if morph_alpha is not None:
            if (
                isinstance(morph_alpha, bool)
                or not isinstance(morph_alpha, (int, float, np.number))
                or not math.isfinite(float(morph_alpha))
                or not 0 < float(morph_alpha) < 1
            ):
                raise ConfusionSampleError("morph_alpha must be inside 0..1 or null")
            if not self.ambiguity:
                raise ConfusionSampleError("morph_alpha requires an ambiguous sample")
            object.__setattr__(self, "morph_alpha", float(morph_alpha))
        if self.kind.startswith("morph_") and morph_alpha is None:
            raise ConfusionSampleError("morph sample must declare morph_alpha")

        user_strokes = _freeze_strokes(self.user_strokes, "user_strokes")
        target_template = _freeze_strokes(self.target_template, "target_template")
        object.__setattr__(self, "user_strokes", user_strokes)
        object.__setattr__(self, "target_template", target_template)

        try:
            critical_strokes = tuple(self.critical_strokes)
        except TypeError as exc:
            raise ConfusionSampleError(
                "critical_strokes must be a sequence of indices"
            ) from exc
        if any(
            not isinstance(index, int) or isinstance(index, bool)
            for index in critical_strokes
        ):
            raise ConfusionSampleError("critical_strokes must contain integer indices")
        if tuple(sorted(set(critical_strokes))) != critical_strokes:
            raise ConfusionSampleError("critical_strokes must be sorted and unique")
        if any(not 0 <= index < len(target_template) for index in critical_strokes):
            raise ConfusionSampleError(
                "critical_strokes contains an index outside target_template"
            )
        object.__setattr__(self, "critical_strokes", critical_strokes)

        try:
            evidence_labels = tuple(self.evidence_labels)
        except TypeError as exc:
            raise ConfusionSampleError(
                "evidence_labels must be a sequence of evidence codes"
            ) from exc
        if len(set(evidence_labels)) != len(evidence_labels):
            raise ConfusionSampleError("evidence_labels must not contain duplicates")
        unknown = set(evidence_labels) - EVIDENCE_CODES
        if unknown:
            raise ConfusionSampleError(f"unknown evidence labels: {sorted(unknown)}")
        if self.ambiguity and _AMBIGUITY_EVIDENCE not in evidence_labels:
            raise ConfusionSampleError(
                "ambiguous sample requires AMBIGUOUS_BETWEEN_CHARACTERS evidence"
            )
        if not self.ambiguity and _AMBIGUITY_EVIDENCE in evidence_labels:
            raise ConfusionSampleError(
                "resolved sample cannot use AMBIGUOUS_BETWEEN_CHARACTERS evidence"
            )
        object.__setattr__(self, "evidence_labels", evidence_labels)

    @property
    def label(self) -> SampleLabel:
        if self.ambiguity:
            return "ambiguous"
        return "target" if self.is_target else "competitor"

    def pair_metadata(self) -> dict[str, object]:
        """Return the identical label payload used by every model adapter."""
        return {
            "schema_version": self.schema_version,
            "sample_id": self.sample_id,
            "split": self.split,
            "kind": self.kind,
            "written_char": self.written_char,
            "target_char": self.target_char,
            "competitor_char": self.competitor_char,
            "is_target": self.is_target,
            "quality_for_written_char": self.quality_for_written_char,
            "target_match": self.target_match,
            "pair_id": self.pair_id,
            "critical_strokes": list(self.critical_strokes),
            "evidence_labels": list(self.evidence_labels),
            "ambiguity": self.ambiguity,
            "seed": self.seed,
            "morph_alpha": self.morph_alpha,
        }

    def to_manifest(self) -> dict[str, object]:
        """Serialize the closed JSON contract, including both stroke streams."""
        return {
            **self.pair_metadata(),
            "user_strokes": [stroke.tolist() for stroke in self.user_strokes],
            "target_template": [stroke.tolist() for stroke in self.target_template],
        }

    @classmethod
    def from_manifest(cls, value: Mapping[str, object]) -> ConfusionSample:
        """Parse one closed manifest and re-apply every semantic invariant."""
        if not isinstance(value, Mapping):
            raise ConfusionSampleError("sample manifest must be an object")
        missing = _MANIFEST_FIELDS - set(value)
        extra = set(value) - _MANIFEST_FIELDS
        if missing:
            raise ConfusionSampleError(
                f"sample manifest is missing fields: {', '.join(sorted(missing))}"
            )
        if extra:
            raise ConfusionSampleError(
                f"sample manifest has unknown fields: {', '.join(sorted(extra))}"
            )
        if value["schema_version"] != CONFUSION_SAMPLE_SCHEMA_VERSION:
            raise ConfusionSampleError("unsupported confusion sample schema version")
        payload = {key: item for key, item in value.items() if key != "schema_version"}
        try:
            return cls(**payload)  # type: ignore[arg-type]
        except TypeError as exc:
            raise ConfusionSampleError(f"invalid sample manifest: {exc}") from exc


def _direction_lookup(
    registry: ConfusionRegistry,
) -> dict[tuple[str, str, str], ConfusionDirection]:
    return {
        (pair.pair_id, direction.target_char, direction.competitor_char): direction
        for pair in registry.pairs
        for direction in pair.directions
    }


def confusion_sample_from_fixture(
    fixture: ConfusionFixture,
    registry: ConfusionRegistry,
    template_loader: Callable[[str], Sequence[np.ndarray]],
    *,
    directions: Mapping[tuple[str, str, str], ConfusionDirection] | None = None,
) -> ConfusionSample:
    """Convert a deterministic baseline fixture without inventing missing labels."""
    direction_map = directions or _direction_lookup(registry)
    direction_key = (
        fixture.pair_id,
        fixture.target_char,
        fixture.competitor_char,
    )
    try:
        direction = direction_map[direction_key]
    except KeyError as exc:
        raise ConfusionSampleError(
            f"fixture {fixture.fixture_id!r} is not declared by its registry"
        ) from exc
    if fixture.critical_stroke != direction.critical_stroke:
        raise ConfusionSampleError(
            f"fixture {fixture.fixture_id!r} has a stale critical stroke"
        )

    if fixture.label == "target":
        is_target: bool | None = True
        target_match: float | None = 1.0
        evidence_labels: tuple[str, ...] = ()
        ambiguity = False
    elif fixture.label == "competitor":
        is_target = False
        target_match = 0.0
        evidence_labels = direction.evidence_codes
        ambiguity = False
    elif fixture.label == "ambiguous":
        is_target = None
        target_match = None
        evidence_labels = (_AMBIGUITY_EVIDENCE,)
        ambiguity = True
    else:
        raise ConfusionSampleError(
            f"fixture {fixture.fixture_id!r} has an unsupported label"
        )

    return ConfusionSample(
        sample_id=fixture.fixture_id,
        split=fixture.split,
        kind=fixture.kind,
        user_strokes=fixture.strokes,
        target_template=tuple(template_loader(fixture.target_char)),
        written_char=fixture.written_char,
        target_char=fixture.target_char,
        competitor_char=fixture.competitor_char,
        is_target=is_target,
        quality_for_written_char=None,
        target_match=target_match,
        pair_id=fixture.pair_id,
        critical_strokes=(direction.critical_stroke,),
        evidence_labels=evidence_labels,
        ambiguity=ambiguity,
        seed=fixture.seed,
        morph_alpha=fixture.morph_alpha,
    )


def confusion_samples_from_fixtures(
    fixtures: Sequence[ConfusionFixture],
    registry: ConfusionRegistry,
    template_loader: Callable[[str], Sequence[np.ndarray]],
) -> tuple[ConfusionSample, ...]:
    """Apply the common label contract to a fixture collection."""
    directions = _direction_lookup(registry)
    samples = tuple(
        confusion_sample_from_fixture(
            fixture,
            registry,
            template_loader,
            directions=directions,
        )
        for fixture in fixtures
    )
    ids = [sample.sample_id for sample in samples]
    if len(ids) != len(set(ids)):
        raise ConfusionSampleError("fixture collection contains duplicate sample IDs")
    return samples


def generate_confusion_samples(
    registry: ConfusionRegistry,
    template_loader: Callable[[str], Sequence[np.ndarray]],
    *,
    split: str | None = None,
) -> tuple[ConfusionSample, ...]:
    """Generate fixtures and immediately normalize them to the common contract."""
    template_cache: dict[str, tuple[np.ndarray, ...]] = {}

    def load(char: str) -> tuple[np.ndarray, ...]:
        if char not in template_cache:
            template_cache[char] = tuple(template_loader(char))
        return template_cache[char]

    fixtures = generate_confusion_fixtures(registry, load, split=split)
    return confusion_samples_from_fixtures(fixtures, registry, load)


def generate_full_competitor_training_samples(
    registry: ConfusionRegistry,
    template_loader: Callable[[str], Sequence[np.ndarray]],
    *,
    split: str = "train",
) -> tuple[ConfusionSample, ...]:
    """Build deterministic positive/full-competitor samples for pair training.

    The baseline fixture adapter intentionally leaves writing quality unknown.
    This training adapter can measure it because each retained sample has a
    resolved ``written_char`` and was generated directly from that character's
    template.  Quality is measured against the written character, while
    ``target_match`` remains the independent requested-character label.

    Samples stay ordered as adjacent ``clean_target``/``full_competitor`` pairs
    for each direction and sample index.  Callers may therefore use them as
    balanced minibatch building blocks without relabeling either model path.
    """
    template_cache: dict[str, tuple[np.ndarray, ...]] = {}

    def load(char: str) -> tuple[np.ndarray, ...]:
        if char not in template_cache:
            template_cache[char] = tuple(template_loader(char))
        return template_cache[char]

    samples = generate_confusion_samples(registry, load, split=split)
    training_samples: list[ConfusionSample] = []
    for sample in samples:
        if sample.kind not in FULL_COMPETITOR_TRAINING_KINDS:
            continue
        if sample.written_char is None:
            raise ConfusionSampleError(
                "full-competitor training samples must have a resolved written_char"
            )
        written_template = load(sample.written_char)
        stroke_count = len(sample.user_strokes)
        if stroke_count != len(written_template):
            raise ConfusionSampleError(
                f"sample {sample.sample_id!r} does not match its written template"
            )
        labels = compute_labels(
            sample.user_strokes,
            written_template,
            np.arange(stroke_count),
            np.zeros(stroke_count, dtype=bool),
        )
        training_samples.append(
            replace(
                sample,
                quality_for_written_char=float(labels["overall"]),
            )
        )
    return tuple(training_samples)
