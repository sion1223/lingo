# Confusion C0 RunPod CUDA baseline — 2026-08-11

## 판정

**C0 seed baseline 완료.** Git의 정확한 소스 SHA와 내용 해시로 고정한 세 checkpoint를 실제
RunPod CUDA 환경에서 같은 192개 fixture에 재생했다. `stroke`, `chandra`, `hybrid`가 모두
`status=ok`로 끝났고 raw JSON과 실행 로그를 로컬로 회수한 뒤 원격·로컬 SHA-256이 일치함을
확인했다.

이 결과는 기존 모델의 품질 합격이 아니다. Chandra의 aggregate competitor false acceptance는
100%, hybrid는 67.1875%였으므로 현재 모델을 문자 정체성 판정기로 운영할 근거가 없다. 이 문서는
C1 hard-negative 학습 전 기준선을 잠그는 보고서다.

검증이 끝난 뒤 사용자의 명시적 지시에 따라 두 Pod를 모두 `Terminate`했다. RunPod API에서 최종
검증 Pod는 404였고, Pod 목록과 별도 network volume 목록은 각각 0개였다.

## 고정된 소스와 입력

| 항목 | 값 |
|---|---|
| branch | `codex/confusion-c0-baseline-20260809` |
| exact source SHA | `9f762ddca86d834d844b6e6a85b68d4847026038` |
| implementation commit | `4a016c32a576ed456310a94898b9d2ba8f959550` |
| remote worktree | clean (`worktree_dirty=false`) |
| registry | `kana_seed_v1`, version 1 |
| registry SHA-256 | `7cab843010b6af19c0589e01a5b8fdbdf01f2f628a3bb745a778c791452b4ae1` |
| fixture generator | `confusion_fixture.v1`, split `test` |
| split seed | `2026080903` |
| fixture seed SHA-256 | `e9d00b4d8f633150b0a17df0784f08d87aaca3359f0ff96a0e2e786da67414e7` |
| fixture content SHA-256 | `1cd347bf454aed52bdb8dc1c1cdb5777ac93a145831209a31ee5ffff5512fc09` |

Private GitHub 인증을 Pod에 복사하지 않았다. 로컬 Git bundle을 만들고 SHA-256을 확인한 뒤 Pod에서
clone했으며, checkout된 `HEAD`와 branch, clean status를 별도로 확인했다.

| 입력 | 크기 | SHA-256 |
|---|---:|---|
| Git bundle | 31,892,508 bytes | `8b0a00fe63a743a49b1ee06553f68214eb15a032f271a1faa47b9b4a5b32f548` |
| `checkpoints/stroke_scorer.pt` | 3,563,151 bytes | `77a0440829459e68d567df30d423723884ec3ce7df82d2473b8297f830e0305b` |
| `checkpoints/chandra_scorer.pt` | 130,904,861 bytes | `af6d1f01497b51220752e414656cf2cd26eaf8234b586d6669dfdb78143be7f3` |
| `checkpoints/hybrid_config.json` | 735 bytes | `2213dd9b0322029295ea312c74f062d5400fef41d4cd7731233cda0b40bd72c1` |

## RunPod 환경

| 항목 | 값 |
|---|---|
| cloud / data center | Secure Cloud / `CA-MTL-1` |
| GPU | NVIDIA RTX A5000, 24,564 MiB |
| GPU driver | `580.159.04` |
| image | `runpod/pytorch:1.0.2-cu1281-torch280-ubuntu2404` |
| image digest | `sha256:0a360022e8de4375af99430f84e8b38951acc397252163a37ceac7204d01be35` |
| Python | `3.12.3` |
| PyTorch / CUDA runtime | `2.8.0+cu128` / `12.8` |
| Transformers / Accelerate | `5.15.0` / `1.14.0` |
| NumPy | `2.1.2` |
| Chandra backbone | `datalab-to/chandra` |

첫 Community RTX 3090 Pod는 SSH daemon을 시작한 뒤에도 해당 호스트의 직접 TCP mapping이 연결을
거부했다. 원격 작업물은 전혀 만들지 않은 상태에서 즉시 Terminate하고, Secure RTX A5000 Pod로
교체했다. 공개키만 Pod에 주입했으며 private SSH key, GitHub 자격 증명, 전체 환경 변수는 저장하거나
보고서에 기록하지 않았다.

## 실행 계약

`い↔り` 양방향, 각 16 seed에서 clean target 32, full competitor 32, critical-stroke transplant 32,
morph 25/50/75% 각 32를 생성해 총 192개를 평가했다. Morph 96개는 ambiguous로 유지했다.

```bash
python -m scorer.evaluate_confusions \
  --backends stroke,chandra,hybrid --strict-backends \
  --output artifacts/confusion_baseline_runpod_20260811.json
```

Acceptance threshold는 사전 고정값 `0.5`이고 sensitivity grid는 `0.5,0.6,0.7`이다. 실행 결과를
보고 threshold를 바꾸지 않았다. 모든 backend는 fixture 하나를 target·competitor template에 각각
채점하며, latency는 이 **두-template decision** 하나의 compute 시간이다.

## Aggregate 결과

| backend | target true acceptance | competitor false acceptance | pairwise candidate accuracy | ROC-AUC | median signed margin |
|---|---:|---:|---:|---:|---:|
| stroke | 100% | **50.0000%** | 83.3333% | 1.00000 | 0.19757 |
| Chandra | 100% | **100.0000%** | **50.0000%** | **0.48535** | **-0.00040** |
| hybrid | 100% | **67.1875%** | 83.3333% | 0.99805 | 0.07829 |

Threshold 기반 FAR와 pairwise margin은 다른 질문이다. 예를 들어 hybrid는 pairwise 후보 정확도가
83.33%여도 기존 quality sigmoid가 높아 negative의 67.19%를 threshold `0.5`에서 받아들인다.
Chandra는 두 후보 사이 aggregate margin이 사실상 0이고 ROC-AUC도 chance 수준이어서, 현 head가
identity보다 필기 품질과 방향 편향을 반영한다는 기존 가설과 일치한다.

### 방향별 결과

| backend / target 방향 | competitor FAR | pairwise candidate accuracy | ROC-AUC | median signed margin |
|---|---:|---:|---:|---:|
| stroke `い→り` | 100% | 66.6667% | 1.00000 | 0.19972 |
| stroke `り→い` | 0% | 100% | 1.00000 | 0.19654 |
| Chandra `い→り` | 100% | 33.3333% | 0.40820 | -0.09151 |
| Chandra `り→い` | 100% | 66.6667% | 0.53320 | 0.09081 |
| hybrid `い→り` | 100% | 66.6667% | 1.00000 | 0.07627 |
| hybrid `り→い` | 34.375% | 100% | 1.00000 | 0.07965 |

### Negative 유형별 FAR

| backend | full competitor | critical-stroke transplant |
|---|---:|---:|
| stroke | 50.0000% | 50.0000% |
| Chandra | 100.0000% | 100.0000% |
| hybrid | 68.7500% | 65.6250% |

Hybrid가 stroke 단독보다 aggregate FAR를 악화시켰다. 따라서 현재 global static blend를 유지한 채
threshold만 조정하는 것은 C1~C4의 대안이 아니다. Full competitor와 critical-stroke hard negative,
명시적 target-vs-competitor head, calibration과 abstention을 독립적으로 평가해야 한다.

## GPU latency와 peak VRAM

| backend | mean | p50 | p95 | max | peak allocated VRAM |
|---|---:|---:|---:|---:|---:|
| stroke | 110.59 ms | 100.19 ms | 195.61 ms | 391.29 ms | 1,173,902,848 bytes |
| Chandra | 439.58 ms | 406.16 ms | 585.64 ms | 1,309.32 ms | 1,229,165,568 bytes |
| hybrid | 650.33 ms | 611.17 ms | 878.19 ms | 1,874.22 ms | 1,229,165,568 bytes |

이는 모델 캐시 다운로드, 8B 전체 weight CPU 로딩, SSH, HTTP, Edge 왕복을 제외한 evaluator 내부
compute latency다. Peak 값은 `torch.cuda.max_memory_allocated()`이며 프로세스·driver 전체 VRAM이
아니다. 최초 백본 다운로드는 약 17GB였고 평가 중 `nvidia-smi` 메모리는 약 1.5GB로 관측됐다.

## 로컬 회수 산출물

Raw prediction과 로그는 `.gitignore`된 로컬 `artifacts/`에 보존했다.

| 로컬 파일 | 크기 | SHA-256 |
|---|---:|---|
| `artifacts/confusion_baseline_runpod_20260811.json` | 376,188 bytes | `1a06060c854359e1a94929535077f977957cb5fe557433e9ba0646b578801457` |
| `artifacts/confusion_baseline_runpod_20260811.log` | 1,759 bytes | `6df73949b6d238fb4bfd843661a3da06555f8bfc0b742359600fe1fc8f6d3a34` |
| `artifacts/runpod_pip_install_20260811.log` | 14,657 bytes | `65db74655f0bc0c5fdde5cfbdb72c7cce441c20f4189f3ec17483831c508b279` |
| `artifacts/runpod_environment_20260811.txt` | 1,178 bytes | `10a463e552ef221bbfe93c007d02cafebdd0ab10b1c73421d16bf688011a39a8` |

JSON은 표준 parser로 다시 읽었고 `allow_nan=False`로 작성됐다. Source SHA, clean worktree, registry와
fixture hash, 세 checkpoint hash, 세 backend `status=ok`, 192개 prediction 수를 로컬에서 재검증했다.

## 로컬 회귀 검증

- `python -m pytest -q`: 156 passed, 기존 Starlette/httpx deprecation warning 1건
- `node --test web/tests/*.test.mjs`: 39 passed
- `python -m compileall -q scorer tests`: pass
- RunPod artifact hash·source·fixture·backend·prediction 수·유한 수치 assert: pass
- `git diff --check`: pass

## Pod 종료와 비용

- 빈 Community RTX 3090 Pod: 약 8분, `$0.22/hour`, 작업 전 Terminate.
- 검증 Secure RTX A5000 Pod: 최종 확인 uptime 1,474초, `$0.27/hour`, 산출물 회수 후 Terminate.
- 시간×단가 단순 추정 합계: 약 `$0.14`. Billing API는 종료 직전 집계 지연으로 아직 0건을 반환해
  확정 청구액으로 간주하지 않는다.
- 최종 삭제 응답: HTTP 204.
- 삭제 후 조회: 검증 Pod 404, 전체 Pod 0개, 별도 network volume 0개.

## 남은 gate와 다음 단일 실험

C0 seed baseline은 닫혔지만 registry가 한 pair뿐이어서 의미 있는 worst-10 비교가 없고, 실제 필기
이미지의 writer/style holdout도 없다. 다음 단일 실험은 이 고정 baseline을 변경하지 않은 채 C1 공통
sample contract와 full-character·critical-stroke hard negative를 만드는 것이다. 그 뒤 stroke v2와
Chandra v2를 각각 독립 ablation하고, 새 checkpoint를 다시 exact-SHA RunPod gate에 통과시킨다.

이번 실행은 C0 model evaluator gate이며 FastAPI service, direct/Edge `/score`, 브라우저 E2E,
iPad/Apple Pencil 왕복 latency는 측정하지 않았다. 해당 항목은 Phase 2 서비스 검증 보고서와 분리한다.
