# 개선 계획

## P0 — 실시간 선생님형 필기 피드백

현재의 `글자 완성 → 채점하기 → 사후 총평` 흐름을 `필기 중 관찰 → 획 종료 즉시 교정 → 다음 획 안내 → 비동기 최종채점` 흐름으로 개선한다.

- 전체 구현 계약: [`REALTIME_TUTOR_IMPLEMENTATION.md`](REALTIME_TUTOR_IMPLEMENTATION.md)
- 다음 실행·RunPod 검증 계약: [`NEXT_PHASE_RUNPOD_VALIDATION.md`](NEXT_PHASE_RUNPOD_VALIDATION.md)
- 유사 문자·두 모델·LLM 품질 계약: [`CONFUSABLE_CHARACTER_MODEL_LLM_PLAN.md`](CONFUSABLE_CHARACTER_MODEL_LLM_PLAN.md)
- Codex 저장소 지시: [`../AGENTS.md`](../AGENTS.md)
- 구현 순서: Phase 0 기준선/테스트 → Phase 1 브라우저 로컬 코치 → Phase 2 경량 서버 코치 → RunPod 실기 gate → confusion C0 baseline → 두 모델 개선 → 구조화 LLM 교사 계층
- 기존 `/score`, `[x, y]` 입력, 따라쓰기/암기쓰기 호환성을 유지한다.
- 서버가 꺼져 있어도 로컬 geometry-only 피드백이 동작해야 한다.

### 현재 다음 작업

- [ ] 현재 구현의 Phase 0/1 완료 조건을 테스트 근거와 함께 감사
- [ ] 누락된 Phase 0/1 항목만 보완
- [ ] `POST /coach/stroke` 경량 서버 코치 구현
- [ ] geometry-only 및 geometry+stroke-model 모드 구현
- [ ] `/health`에 coach/deep 상태와 정확한 `build_sha` 추가
- [ ] Supabase Edge Function coach 라우팅과 2.5초 timeout 구현
- [ ] 실제 RunPod GPU Pod에 push된 정확한 SHA 배포
- [ ] direct Pod API와 실제 Edge Function E2E 실행
- [ ] direct/Edge coach 및 `/score` p50/p95 실측
- [ ] Pod Stop 상태에서 새로 앱을 열어 핵심 문자 local-only 연습 검증
- [ ] 검증 성공·실패와 관계없이 실제 Pod Stop 확인
- [ ] `docs/validation/RUNPOD_PHASE2_<YYYYMMDD>.md` 작성

RunPod 접근 권한이 없거나 실제 Pod를 시작하지 못한 경우 Phase 2는 완료로 표시하지 않는다. localhost, mock, CI 결과는 RunPod 실기 검증을 대체하지 않는다.

## P0.5 — 유사 문자 오인 방지와 두 모델 성능 개선

현재 Chandra 비전 모델과 좌표 기반 스트로크 모델은 같은 문자의 자연스러운 왜곡 품질은 학습하지만, `い↔り`처럼 다른 문자를 목표 글자로 잘못 인정하는 상황을 직접 최적화하지 않는다.

핵심 방향:

- `identity`와 `written form quality`를 분리
- 유사 문자 confusion graph와 top-K competitor 사용
- full-character 및 critical-stroke hard negative 학습
- target-vs-competitor margin과 ambiguity 도입
- 좌표 모델의 다중 해상도·곡률·획 관계 특징 강화
- Chandra의 explicit difference interaction과 critical-region 학습
- 전역 고정 가중치 대신 confidence-aware fusion 비교
- 모델의 structured evidence를 LLM이 교사식 문장으로만 변환

### C0 — 먼저 해야 할 baseline

- [ ] `configs/confusions/` schema와 `kana_seed_v1.yaml` 생성
- [ ] `い↔り`를 mandatory pair로 등록
- [ ] `kanji/03044.svg`, `kanji/0308a.svg` 기반 deterministic fixture 생성
- [ ] 현재 stroke/Chandra/hybrid checkpoint의 `い→り`, `り→い` 양방향 false acceptance 측정
- [ ] target true acceptance, pairwise margin, candidate accuracy, calibration 측정
- [ ] 자동 template-neighbor mining prototype 추가
- [ ] 실제 RunPod에서 exact SHA/checkpoint로 baseline 재현
- [ ] `docs/validation/CONFUSION_BASELINE_<YYYYMMDD>.md` 작성

baseline이 고정되기 전에는 model v2나 LLM 구현을 시작하지 않는다.

### C1 — hard-negative 데이터

- [ ] `written_char`, `target_char`, `competitor`, `is_target`, `pair_id`, ambiguity를 포함한 공통 sample contract
- [ ] competitor 전체 글자 제출 negative
- [ ] critical-stroke transplant negative
- [ ] morph/ambiguous sample과 soft label 정책
- [ ] model-mined hard-negative queue
- [ ] train/validation/test seed family 분리 테스트
- [ ] 실제 이미지 데이터 writer split

### C2 — 좌표 기반 스트로크 모델 v2

- [ ] target-match head와 ranking loss
- [ ] prototype candidate retrieval
- [ ] 12/24/48점 multi-resolution ablation
- [ ] tangent·curvature·arc length 특징 ablation
- [ ] 획 사이 상대 위치·길이·각도 relation encoder
- [ ] SupCon, angular margin, Soft-DTW를 각각 독립 ablation
- [ ] confusion pair와 normal quality 회귀 평가
- [ ] 실제 RunPod 학습 및 checkpoint hash 보고

### C3 — Chandra 모델 v2

- [ ] hard-negative pair training
- [ ] user-target token difference/product interaction
- [ ] critical-region weighted pooling 또는 crop
- [ ] conventional ink/time/difference multi-view ablation
- [ ] 마지막 block unfreeze와 adapter/LoRA 후보 비교
- [ ] target-match, prototype, critical-region/evidence auxiliary head
- [ ] ETL 데이터의 라이선스 확인 후 writer-holdout identity 보조 평가
- [ ] 실제 RunPod 학습 및 VRAM/latency 보고

### C4 — hybrid와 uncertainty

- [ ] 기존 global static weight baseline 유지
- [ ] task별 static weight와 rule gate 비교
- [ ] uncertainty/model disagreement를 입력으로 하는 learned gate
- [ ] calibrated target-vs-competitor margin
- [ ] `AMBIGUOUS_BETWEEN_CHARACTERS` abstention
- [ ] confidence-gated cross-model distillation ablation

### C5 — 구조화 evidence와 deterministic 교사 피드백

- [ ] target/competitor pair delta profile
- [ ] critical stroke/segment/region
- [ ] stable evidence code
- [ ] anchor/vector/overlay
- [ ] 모든 code의 고정 fallback 문구
- [ ] attention 단독 설명 금지 및 기하 근거 검증

### C6 — LLM pedagogical renderer

이번 C6 구현은 명시적 vertical-slice 지시로 API부터 UI까지 연결한 것이다. C0~C5의 confusion
baseline·실제 competitor margin·구조화 evidence 품질 gate를 완료했다는 뜻은 아니다.

- [x] 명시적 `왜?` 비동기 UI와 `/coach/verbalize`, `/coach/summary` backend 경로
- [x] `teacher_feedback.v1` versioned input/output JSON Schema
- [x] locked decision field
- [x] strict structured output + 유한 approved-language option + semantic validator
- [x] cache, timeout, refusal, 5xx fallback
- [x] 원시 좌표·이미지·사용자 식별 정보 기본 전송 금지
- [ ] 반복 오류, `왜?`, hint 상승, 문자/세션 요약에서만 호출
- [x] 최소 1,000 로컬 synthetic case에서 validator·fallback·모순 mutation 평가
- [ ] 실제 Luna generation 1,000건의 hallucination/locked-decision batch gate
- [ ] deterministic template 대비 명확성·실행 가능성·비용·latency 비교
- [ ] 공개 Edge 사용자별 rate limit·일일 비용 예산 설정

구현·실제 Luna 호출 기록: [`validation/GPT56_LUNA_TEACHER_FEEDBACK_20260808.md`](validation/GPT56_LUNA_TEACHER_FEEDBACK_20260808.md)

### 모델 품질 합격 조건

- [ ] `い↔り` competitor false acceptance 상대 감소 70% 이상
- [ ] `い↔り` target true acceptance 감소 2 percentage point 이내
- [ ] confusion macro false acceptance 상대 감소 50% 이상
- [ ] worst-10 pair 중 8개 이상 개선
- [ ] normal quality/direction/order 회귀 통제
- [ ] LLM locked decision 보존 100%
- [ ] invented score/evidence와 target/competitor 뒤바꿈 0건
- [ ] 실제 RunPod 환경·checkpoint·config·registry hash 기록
- [ ] 실험 종료 후 Pod Stop 확인

수치가 충족되지 않으면 결과를 숨기지 않고 data, representation, loss, calibration, domain gap으로 원인을 분해한다.

## 채점/연습 화면

- 문자를 변경하면 이전에 작성하던 획이 캔버스에 남지 않고 완전히 초기화되도록 수정
- 예시 문자(참고용 한자) 렌더링을 더 부드럽게 개선

## 한자 지원 범위

- 형태가 여러 개인 한자(이체자)를 전부 지원
- 한자는 읽는 법(음/훈)이 여러 개인 경우가 많으므로, 상황(문맥)을 먼저 제시하고 그에 맞는 한자를 쓰게 하는 기능 추가
- 문제 출제는 초보자들이 많이 쓰는 상용한자 위주로 구성

## 데이터 축적

- 사용자 쓰기 데이터(획순, 필압/좌표 등 학습에 필요한 정보)를 저장하여 이후 모델 학습에 활용
- 실제 사용자 데이터를 학습에 재사용하기 전에 동의, 보존 기간, 삭제 경로를 설계
- 외부 ETL 데이터와 사용자 데이터 원본은 Git에 커밋하지 않고 라이선스·동의·writer/user split을 기록
