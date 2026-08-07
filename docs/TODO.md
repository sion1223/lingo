# 개선 계획

## P0 — 실시간 선생님형 필기 피드백

현재의 `글자 완성 → 채점하기 → 사후 총평` 흐름을 `필기 중 관찰 → 획 종료 즉시 교정 → 다음 획 안내 → 비동기 최종채점` 흐름으로 개선한다.

- 전체 구현 계약: [`REALTIME_TUTOR_IMPLEMENTATION.md`](REALTIME_TUTOR_IMPLEMENTATION.md)
- 다음 실행·RunPod 검증 계약: [`NEXT_PHASE_RUNPOD_VALIDATION.md`](NEXT_PHASE_RUNPOD_VALIDATION.md)
- Codex 저장소 지시: [`../AGENTS.md`](../AGENTS.md)
- 구현 순서: Phase 0 기준선/테스트 → Phase 1 브라우저 로컬 코치 → Phase 2 경량 서버 코치 → Phase 3 적응형 개입 → Phase 4 비동기 심층채점/데이터 루프
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
