from __future__ import annotations

from collections import Counter
from pathlib import Path

import numpy as np
import torch

from scorer.confusion_dataset import generate_full_competitor_training_samples
from scorer.confusions import load_confusion_registry
from scorer.data import (
    CoordinateConfusionDataset,
    RenderedConfusionDataset,
    collate_coordinate_confusions,
    collate_rendered_confusions,
)
from scorer.kanjivg import load_char

ROOT = Path(__file__).resolve().parents[1]
REGISTRY_PATH = ROOT / "configs" / "confusions" / "kana_seed_v1.yaml"
KANJI = ROOT / "kanji"


def _samples(split: str = "train"):
    registry = load_confusion_registry(REGISTRY_PATH)
    return generate_full_competitor_training_samples(
        registry,
        lambda char: load_char(KANJI, char),
        split=split,
    )


def _render_stub(strokes, size):
    image = np.full((size, size, 3), len(strokes), dtype=np.uint8)
    grid = np.zeros((len(strokes), 4), dtype=np.float32)
    for index in range(len(strokes)):
        grid[index, index % 4] = 1.0
    return image, grid


def test_full_competitor_training_samples_are_balanced_resolved_pairs():
    samples = _samples()

    assert len(samples) == 64
    assert Counter(sample.kind for sample in samples) == {
        "clean_target": 32,
        "full_competitor": 32,
    }
    for positive, negative in zip(samples[::2], samples[1::2]):
        assert positive.kind == "clean_target"
        assert positive.is_target is True
        assert positive.written_char == positive.target_char
        assert positive.target_match == 1.0

        assert negative.kind == "full_competitor"
        assert negative.is_target is False
        assert negative.written_char == negative.competitor_char
        assert negative.target_match == 0.0

        assert positive.pair_id == negative.pair_id
        assert positive.target_char == negative.target_char
        assert positive.competitor_char == negative.competitor_char
        assert positive.split == negative.split == "train"
        assert positive.quality_for_written_char is not None
        assert negative.quality_for_written_char is not None
        assert negative.quality_for_written_char > 0.9


def test_full_competitor_generation_is_reproducible_and_split_isolated():
    first = _samples("train")
    repeated = _samples("train")
    validation = _samples("validation")
    test = _samples("test")

    assert [sample.to_manifest() for sample in first] == [
        sample.to_manifest() for sample in repeated
    ]
    split_sets = [
        {sample.sample_id for sample in collection}
        for collection in (first, validation, test)
    ]
    seed_sets = [
        {sample.seed for sample in collection}
        for collection in (first, validation, test)
    ]
    assert all(
        left.isdisjoint(right)
        for index, left in enumerate(split_sets)
        for right in split_sets[index + 1 :]
    )
    assert all(
        left.isdisjoint(right)
        for index, left in enumerate(seed_sets)
        for right in seed_sets[index + 1 :]
    )
    assert not np.array_equal(
        first[0].user_strokes[0], validation[0].user_strokes[0]
    )


def test_coordinate_and_rendered_adapters_share_exact_pair_supervision():
    samples = _samples()[:2]
    coordinate_ds = CoordinateConfusionDataset(samples)
    coordinate_batch = collate_coordinate_confusions(
        [coordinate_ds[index] for index in range(len(coordinate_ds))]
    )

    rendered_ds = RenderedConfusionDataset(samples, _render_stub, size=16)
    rendered_batch = collate_rendered_confusions(
        [rendered_ds[index] for index in range(len(rendered_ds))]
    )

    user, template, coordinate_labels, coordinate_metadata = coordinate_batch
    (
        user_images,
        user_grids,
        template_images,
        stroke_mask,
        rendered_labels,
        rendered_metadata,
    ) = rendered_batch

    assert user[0].shape[0] == template[0].shape[0] == 2
    assert len(user_images) == len(template_images) == 2
    assert user_grids.shape[:2] == stroke_mask.shape
    assert coordinate_metadata == rendered_metadata == tuple(
        sample.pair_metadata() for sample in samples
    )
    assert set(coordinate_labels) == set(rendered_labels)
    for key in coordinate_labels:
        assert torch.equal(coordinate_labels[key], rendered_labels[key])
    assert coordinate_labels["is_target"].tolist() == [1.0, 0.0]
    assert coordinate_labels["is_target_mask"].tolist() == [True, True]
    assert coordinate_labels["target_match"].tolist() == [1.0, 0.0]
    assert coordinate_labels["quality_mask"].tolist() == [True, True]
