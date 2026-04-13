"""
신경망 기반 획 임베딩 모델 (선택적 사용)

DTW만으로도 충분하지만, CNN Siamese 네트워크를 사용하면
더 의미론적인 유사도 비교가 가능하다.

구조:
  StrokeEncoder: 1D CNN → 고정 크기 임베딩
  SiameseNet:    두 획의 임베딩 거리로 유사도 학습
  DTW 손실:      DTW 거리를 지도 신호로 사용
"""
import numpy as np

try:
    import torch
    import torch.nn as nn
    import torch.nn.functional as F
    TORCH_AVAILABLE = True
except ImportError:
    TORCH_AVAILABLE = False


if TORCH_AVAILABLE:

    class StrokeEncoder(nn.Module):
        """
        단일 획(시계열)을 고정 크기 임베딩으로 변환하는 1D CNN.

        Input:  (batch, channels, time_steps)  — channels = 2(x,y) 또는 5(피처)
        Output: (batch, embed_dim)
        """

        def __init__(self, in_channels: int = 2, embed_dim: int = 64):
            super().__init__()
            self.conv = nn.Sequential(
                # 로컬 패턴 학습
                nn.Conv1d(in_channels, 32, kernel_size=5, padding=2),
                nn.BatchNorm1d(32),
                nn.ReLU(),
                nn.Conv1d(32, 64, kernel_size=5, padding=2),
                nn.BatchNorm1d(64),
                nn.ReLU(),
                nn.Conv1d(64, 128, kernel_size=3, padding=1),
                nn.BatchNorm1d(128),
                nn.ReLU(),
            )
            # 전역 컨텍스트
            self.pool = nn.AdaptiveAvgPool1d(1)
            self.fc = nn.Sequential(
                nn.Linear(128, embed_dim),
                nn.LayerNorm(embed_dim),
            )

        def forward(self, x: "torch.Tensor") -> "torch.Tensor":
            # x: (B, T, C) → (B, C, T)
            x = x.permute(0, 2, 1)
            x = self.conv(x)
            x = self.pool(x).squeeze(-1)  # (B, 128)
            x = self.fc(x)               # (B, embed_dim)
            return F.normalize(x, p=2, dim=1)  # L2 정규화


    class SiameseStrokeNet(nn.Module):
        """
        두 획의 유사도를 학습하는 Siamese 네트워크.
        DTW 거리를 ground truth로 사용하여 임베딩 거리를 정렬.
        """

        def __init__(self, in_channels: int = 2, embed_dim: int = 64):
            super().__init__()
            self.encoder = StrokeEncoder(in_channels, embed_dim)

        def forward(
            self,
            stroke1: "torch.Tensor",
            stroke2: "torch.Tensor"
        ) -> "torch.Tensor":
            """
            Returns cosine similarity (−1 ~ 1).
            1 = 완전히 같음, −1 = 완전히 다름
            """
            e1 = self.encoder(stroke1)
            e2 = self.encoder(stroke2)
            return F.cosine_similarity(e1, e2)

        def embed(self, stroke: "torch.Tensor") -> "torch.Tensor":
            return self.encoder(stroke)


    class DTWRegressionLoss(nn.Module):
        """
        임베딩 거리와 DTW 거리의 차이를 최소화하는 회귀 손실.
        MSE(euclidean_distance(e1, e2), dtw_distance)
        """

        def forward(
            self,
            e1: "torch.Tensor",
            e2: "torch.Tensor",
            dtw_targets: "torch.Tensor"
        ) -> "torch.Tensor":
            embed_dist = torch.norm(e1 - e2, p=2, dim=1)
            return F.mse_loss(embed_dist, dtw_targets)


    class ContrastiveLoss(nn.Module):
        """
        Contrastive Loss: 유사 쌍은 가깝게, 비유사 쌍은 멀게.
        label=1 → 같은 문자, label=0 → 다른 문자
        """

        def __init__(self, margin: float = 1.0):
            super().__init__()
            self.margin = margin

        def forward(
            self,
            e1: "torch.Tensor",
            e2: "torch.Tensor",
            labels: "torch.Tensor"
        ) -> "torch.Tensor":
            dist = torch.norm(e1 - e2, p=2, dim=1)
            loss_sim   = labels       * dist.pow(2)
            loss_dissim = (1 - labels) * F.relu(self.margin - dist).pow(2)
            return (loss_sim + loss_dissim).mean()


class StrokeDataset:
    """
    신경망 학습용 데이터셋 (PyTorch 없이도 사용 가능).

    kanji_dataset.json에서 획 쌍(pair)을 생성한다.
    - positive pair: 같은 문자의 다른 변형 획
    - negative pair: 다른 문자의 동일 위치 획
    """

    def __init__(
        self,
        records: list,
        resample_n: int = 50,
        use_features: bool = False
    ):
        from .features import preprocess_stroke
        self.samples = []  # (seq, char, stroke_idx)

        for rec in records:
            char = rec.get("character", "")
            for s in rec.get("strokes", []):
                pts = s.get("points", [])
                if len(pts) < 2:
                    continue
                processed = preprocess_stroke(pts, resample_n, use_features)
                self.samples.append({
                    "seq":        processed,
                    "character":  char,
                    "stroke_idx": s.get("order", 0),
                })

    def get_pairs(self, n_pairs: int = 10000) -> list:
        """학습용 (seq1, seq2, is_same_char) 쌍 목록을 생성한다."""
        import random
        rng = random.Random(42)
        chars = list({s["character"] for s in self.samples})
        char_to_samples = {c: [] for c in chars}
        for s in self.samples:
            char_to_samples[s["character"]].append(s)

        pairs = []
        for _ in range(n_pairs):
            if rng.random() < 0.5:
                # positive pair
                char = rng.choice(chars)
                pool = char_to_samples[char]
                if len(pool) < 2:
                    continue
                a, b = rng.sample(pool, 2)
                pairs.append((a["seq"], b["seq"], 1))
            else:
                # negative pair
                c1, c2 = rng.sample(chars, 2)
                a = rng.choice(char_to_samples[c1])
                b = rng.choice(char_to_samples[c2])
                pairs.append((a["seq"], b["seq"], 0))

        return pairs

    def __len__(self):
        return len(self.samples)


def build_torch_dataset(pairs: list):
    """쌍 목록을 PyTorch DataLoader에 맞는 형태로 변환한다."""
    if not TORCH_AVAILABLE:
        raise RuntimeError("PyTorch가 설치되지 않았습니다: pip install torch")

    import torch
    from torch.utils.data import TensorDataset, DataLoader

    seq1 = torch.tensor(np.array([p[0] for p in pairs]), dtype=torch.float32)
    seq2 = torch.tensor(np.array([p[1] for p in pairs]), dtype=torch.float32)
    labels = torch.tensor([p[2] for p in pairs], dtype=torch.float32)

    dataset = TensorDataset(seq1, seq2, labels)
    return DataLoader(dataset, batch_size=128, shuffle=True)
