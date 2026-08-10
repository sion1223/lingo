# Confusion C0 local baseline — 2026-08-09

> 2026-08-11 후속 RunPod CUDA gate는
> [`CONFUSION_BASELINE_RUNPOD_20260811.md`](CONFUSION_BASELINE_RUNPOD_20260811.md)에서 완료했다.
> 아래 내용은 2026-08-09 로컬 측정 당시의 기록으로 보존한다.

## 판정

**2026-08-09 당시 부분 완료 / RunPod gate 미완료.** `い↔り` registry, deterministic fixture, 가나
template-neighbor mining, metric harness와 현재 stroke checkpoint의 로컬 기준선은 고정했다.
이 PC는 CUDA가 없어 Chandra와 hybrid는 실행하지 않았다. 새 유료 RunPod를 만들거나 외부 배포를
변경하지 않았으며, 따라서 이 문서는 C0 전체 완료나 모델 개선의 합격 근거가 아니다.

## 고정된 소스와 입력

| 항목 | 값 |
|---|---|
| branch | `codex/confusion-c0-baseline-20260809` |
| evaluator source SHA | `4a016c32a576ed456310a94898b9d2ba8f959550` |
| implementation base SHA | `dcec73f113d17c8e0a9a0203a5c3401ca968ab4d` |
| `main` merge-base | `649813b5df6e34043aa0aeaca23d37677146dbc4` |
| worktree at measurement | clean (`worktree_dirty=false`) |
| registry | `kana_seed_v1`, version 1 |
| registry SHA-256 | `7cab843010b6af19c0589e01a5b8fdbdf01f2f628a3bb745a778c791452b4ae1` |
| fixture generator | `confusion_fixture.v1`, split `test` |
| split seed | `2026080903` |
| fixture seed SHA-256 | `e9d00b4d8f633150b0a17df0784f08d87aaca3359f0ff96a0e2e786da67414e7` |
| fixture content SHA-256 | `1cd347bf454aed52bdb8dc1c1cdb5777ac93a145831209a31ee5ffff5512fc09` |

Checkpoint는 내용 해시로 식별했다. Chandra 파일은 Git에서 무시되는 로컬 head checkpoint이며,
실행 시 `datalab-to/chandra` 비전 백본을 다시 로드해야 한다.

| 입력 | 크기 | SHA-256 |
|---|---:|---|
| `checkpoints/stroke_scorer.pt` | 3,563,151 bytes | `77a0440829459e68d567df30d423723884ec3ce7df82d2473b8297f830e0305b` |
| `checkpoints/chandra_scorer.pt` | 130,904,861 bytes | `af6d1f01497b51220752e414656cf2cd26eaf8234b586d6669dfdb78143be7f3` |
| `checkpoints/hybrid_config.json` | 735 bytes | `2213dd9b0322029295ea312c74f062d5400fef41d4cd7731233cda0b40bd72c1` |

## Fixture와 측정 규칙

`kanji/03044.svg`(`い`)와 `kanji/0308a.svg`(`り`)를 획당 12점으로 파싱했다. 양방향,
각 16 seed에 대해 다음 192개를 만들었다.

| 유형 | 개수 | 강라벨 |
|---|---:|---|
| clean target | 32 | target |
| full competitor substitution | 32 | competitor |
| critical-stroke transplant | 32 | competitor |
| morph 25% / 50% / 75% | 각 32, 총 96 | 전부 ambiguous |

사람 검토가 없는 interpolation을 target/competitor로 강제하지 않았다. 가벼운 style variation은
획 순서와 방향을 보존하며 train/validation/test seed family는 서로 다르다.

현재 모델의 `overall`은 문자 정체성 확률이 아니라 동일 문자 기준의 필기 품질 sigmoid다.
그러므로 다음을 함께 기록했다.

- 고정 acceptance threshold `0.5`와 사전 정의 sensitivity `0.6`, `0.7`
- `target_score - competitor_score` pairwise margin
- pairwise candidate accuracy, ROC-AUC, average precision
- 기존 quality score를 identity probability처럼 해석했을 때의 Brier/ECE **proxy**

`target→competitor`는 “과제 target에 competitor를 썼을 때” 방향이다. Morph는 threshold와
calibration 집계에서 제외하고 margin 궤적만 남겼다.

## Template-neighbor mining

가나 184자를 대상으로 같은 script·같은 획수 후보의 정렬된 획 geometry distance를 계산했다.
parse failure는 0건이었다.

| 조회 문자 | 상대 문자 rank (top-10) |
|---|---:|
| `い` | `り` 3위 |
| `り` | `い` 2위 |

Graph SHA-256은 `bc92d78bd5ec5f2a2aa1212fd7c9f76f97ddd49d382af2ec35b0911b9108b6d2`다.
이 O(N²) 구현은 작은 가나 seed용 prototype이며 전체 CJK graph의 확장성 근거는 아니다.

## Stroke checkpoint 결과

주요 결과는 threshold `0.5` 기준이다.

| 방향 | target true acceptance | competitor false acceptance | pairwise candidate accuracy | ROC-AUC | median signed margin |
|---|---:|---:|---:|---:|---:|
| `い→り` | 100% | **100%** | **66.67%** | 1.0000 | 0.19972 |
| `り→い` | 100% | 0% | 100% | 1.0000 | 0.19654 |
| aggregate | 100% | **50%** | **83.33%** | 1.0000 | 0.19757 |

Aggregate average precision은 1.0000이다. 기존 target quality score를 확률로 간주한 proxy는
Brier `0.18743`, ECE `0.42050`으로 calibration이 좋다고 볼 수 없다.

Negative 유형별로 보면 오류 위치가 더 명확하다.

| target 방향 | full competitor FAR / median margin | critical transplant FAR / median margin |
|---|---:|---:|
| `い←り` | **100%** / -0.19972 | **100%** / **+0.03520** |
| `り←い` | 0% / -0.28458 | 0% / -0.05018 |

현재 `/score`처럼 target score만 threshold하면 `い` 과제에 쓴 `り`를 모두 잘못 받아들인다.
두 템플릿을 함께 비교하면 full substitution은 음수 margin으로 잡히지만, 두 번째 획만 이식한 critical
fixture는 양수 margin이라 16건 모두 여전히 `い` 쪽으로 분류된다. 즉 단순 competitor 조회만으로
완료되는 문제가 아니며 critical-stroke hard negative가 필요하다는 baseline이다.

Threshold sensitivity는 다음과 같다. 이는 test fixture를 본 뒤 `0.6`으로 바꾸라는 제안이 아니다.
한 pair의 합성 test에서 고른 임계값을 운영에 적용하면 leakage가 된다.

| threshold | target true acceptance | competitor false acceptance |
|---:|---:|---:|
| 0.5 | 100% | 50% |
| 0.6 | 100% | 0% |
| 0.7 | 90.625% | 0% |

## Geometry reference

학습 모델이 아닌 `exp(-4 × aligned template distance)` 기준선은 target acceptance 100%,
target-score 기반 competitor FAR 50%, pairwise candidate accuracy 100%, ROC-AUC 1.0000이었다.
두 방향 모두 critical transplant의 target score는 threshold를 넘었지만 competitor와 직접 비교한
margin은 음수였다. 이 결과 역시 단일 품질 threshold와 pairwise identity 판단을 분리해야 함을 보인다.

## 로컬 latency와 실행 환경

환경은 Windows 11, Python 3.13.14, PyTorch 2.11.0 CPU, NumPy 2.4.3이며 CUDA는 없었다.
수치는 fixture 하나를 target과 competitor 템플릿에 각각 한 번 채점한 **두-template decision** 시간이다.

| backend | mean | p50 | p95 | max |
|---|---:|---:|---:|---:|
| template geometry | 1.37 ms | 1.30 ms | 2.53 ms | 3.22 ms |
| stroke scorer CPU | 62.72 ms | 47.63 ms | 100.42 ms | 1,235.84 ms |

첫 실행 초기화 outlier를 포함한다. 이 수치는 RunPod GPU 또는 브라우저 왕복 latency가 아니다.

## 2026-08-09에 실행하지 않은 gate

| Gate | 상태 | 이유 |
|---|---|---|
| Chandra pairwise baseline | NOT RUN | CUDA 없음; head와 HF 비전 백본의 실제 forward 필요 |
| hybrid pairwise baseline | NOT RUN | Chandra forward가 선행 조건 |
| actual RunPod exact-SHA replay | NOT RUN | 기존 Pod가 내려갔고 새 유료 Pod 생성을 지시받지 않음 |
| GPU/VRAM 및 direct API latency | NOT RUN | 실제 Pod 없음 |
| Pod Stop 증거 | N/A | 이번 작업에서 Pod를 생성·시작하지 않음 |
| macro/worst-10 registry | NOT RUN | seed v1은 mandatory pair 한 개뿐임 |
| writer/style holdout | NOT RUN | 실제 동의 기반 필기 데이터가 없음 |

따라서 심층 `/score`를 다시 운영하거나 C0를 완료하려면 새 RunPod가 필요하다. 반대로 Luna 교사
renderer와 로컬 geometry/stroke 코치는 이 원격 GPU가 없어도 별도 경로로 동작한다.

## 재현 명령

```bash
git checkout 4a016c32a576ed456310a94898b9d2ba8f959550
python -m pip install -r requirements-runpod.txt
python -m scorer.build_confusion_graph \
  --scope kana --top-k 10 \
  --output artifacts/confusion_graph_kana_seed_v1_4a016c3.json
python -m scorer.evaluate_confusions \
  --output artifacts/confusion_baseline_4a016c3_20260809.json
```

실제 RunPod에서는 정확한 SHA와 checkpoint hash를 먼저 확인한 뒤 실패를 허용하지 않게 실행한다.

```bash
python -m scorer.evaluate_confusions \
  --backends stroke,chandra,hybrid --strict-backends \
  --output artifacts/confusion_baseline_runpod_20260809.json
```

Raw prediction JSON과 graph JSON은 Git에서 제외된 `artifacts/`에만 두었다.

## 검증

- `python -m pytest -q`: 156 passed, 기존 Starlette/httpx deprecation warning 1건
- `node --test web/tests/*.test.mjs`: 39 passed
- 새 Python 파일 Ruff: pass
- `python -m compileall -q scorer tests`: pass
- `git diff --check`: pass

## 당시 다음 단일 실험

이 실험은 2026-08-11 후속 보고서에서 완료했다. 원래 계획은 evaluator source SHA를 push하고,
위 strict 명령으로
Chandra·hybrid의 동일 192 fixture 결과와 GPU p50/p95/peak VRAM을 먼저 채운다. 그 결과를 고정하기
전에는 threshold를 바꾸거나 C1 hard-negative 학습을 시작하지 않는다. 측정 후 Pod는 Stop하고 상태를
기록하며, 사용자 지시 없이 Terminate하지 않는다.
