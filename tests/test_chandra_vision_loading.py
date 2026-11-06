import json

import huggingface_hub
import torch
from safetensors.torch import save_file

from scorer.chandra_scorer import _vision_state_dict


def _fake_hub(tmp_path, monkeypatch, weight_map, shards):
    (tmp_path / 'model.safetensors.index.json').write_text(
        json.dumps({'weight_map': weight_map}), encoding='utf-8')
    for name, tensors in shards.items():
        save_file(tensors, str(tmp_path / name))
    requested = []

    def fake_download(model_id, filename):
        requested.append(filename)
        return str(tmp_path / filename)

    monkeypatch.setattr(huggingface_hub, 'hf_hub_download', fake_download)
    return requested


def test_reads_only_vision_tensors_from_vision_shards(tmp_path, monkeypatch):
    requested = _fake_hub(tmp_path, monkeypatch, {
        'model.visual.blocks.0.attn.qkv.weight': 'a.safetensors',
        'model.visual.merger.norm.weight': 'a.safetensors',
        'model.language_model.embed_tokens.weight': 'a.safetensors',
        'model.language_model.layers.0.mlp.up_proj.weight': 'b.safetensors',
    }, {
        'a.safetensors': {
            'model.visual.blocks.0.attn.qkv.weight': torch.ones(2, 2),
            'model.visual.merger.norm.weight': torch.zeros(2),
            'model.language_model.embed_tokens.weight': torch.ones(3),
        },
        'b.safetensors': {
            'model.language_model.layers.0.mlp.up_proj.weight': torch.ones(1),
        },
    })

    state = _vision_state_dict('fake/chandra')

    assert set(state) == {'blocks.0.attn.qkv.weight', 'merger.norm.weight'}
    assert torch.equal(state['blocks.0.attn.qkv.weight'], torch.ones(2, 2))
    # The language-model-only shard (most of the 8B weights) is never downloaded.
    assert 'b.safetensors' not in requested


def test_accepts_legacy_visual_prefix(tmp_path, monkeypatch):
    _fake_hub(tmp_path, monkeypatch, {
        'visual.patch_embed.proj.weight': 'only.safetensors',
    }, {
        'only.safetensors': {'visual.patch_embed.proj.weight': torch.ones(1)},
    })

    assert set(_vision_state_dict('fake/old')) == {'patch_embed.proj.weight'}
