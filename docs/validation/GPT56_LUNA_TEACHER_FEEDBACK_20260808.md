# GPT-5.6 Luna 교사 피드백 검증 — 2026-08-08~09

## 검증 대상

- 브랜치: `codex/gpt56-luna-teacher-feedback-20260808`
- 요청 모델: `gpt-5.6-luna`
- API: OpenAI Responses API + Pydantic Structured Outputs
- 엔드포인트: `POST /coach/verbalize`, `POST /coach/summary`
- 계약: `teacher_feedback.v1`

Luna는 채점기 역할을 하지 않는다. 입력의 `decision_id`, `error_code`, `next_action`, evidence를
잠근다. 서버는 strategy별 안전 문구 후보를 만들고 Luna는 학습자 맥락과 호출 목적에 맞는 후보
하나를 선택한다. 후보를 섞거나 재작성한 출력은 semantic validator가 폐기된다.

## 로컬 합성 1,000-case gate

이 수치는 Luna generation 1,000건이 아니다. 결정론적 renderer, semantic validator, 가상
timeout/503 provider를 통과시킨 로컬 구조화 case 1,000건이다. 실제 Luna 1,000건 batch 품질 gate는
별도 미완료 항목으로 남긴다.

실행 명령:

```bash
python scripts/validate_teacher_feedback.py --cases 1000
```

결과:

| 항목 | 결과 |
|---|---:|
| strict schema 또는 정상 fallback | 1,000 / 1,000 |
| locked decision 보존 | 1,000 / 1,000 |
| timeout/가상 503 fallback | 1,000 / 1,000 |
| invented score 차단 | 125 / 125 |
| 존재하지 않는 획 번호 차단 | 125 / 125 |
| target/competitor 뒤바꿈 차단 | 125 / 125 |
| rejected 상태에서 다음 획 진행 문구 차단 | 125 / 125 |
| 복수 행동 지시 차단 | 125 / 125 |
| fallback latency p50 / p95 | 0.7333ms / 1.1817ms |

가상 provider 장애는 timeout 500회와 503/API error 500회를 번갈아 발생시켰다. 모든 경우 HTTP
교사 응답은 잠긴 필드를 유지한 결정론적 fallback으로 끝났다.

## 최종 계약의 실제 Luna 호출

실제 로컬 비밀키를 사용해 FastAPI `POST /coach/verbalize`를 호출했다. 키 값은 출력·로그·보고서에
노출하거나 저장하지 않았다.

| 항목 | 측정값 |
|---|---:|
| HTTP 상태 | 200 |
| 요청/보고 모델 | `gpt-5.6-luna` / `gpt-5.6-luna` |
| 응답 source | `luna` |
| 최종 finite-option 계약 호출 | 1회, HTTP 200 / Luna output 통과 |
| endpoint / provider latency | 16,884.98ms / 16,862.54ms |
| input / output tokens | 811 / 124 |
| cached input tokens | 0 |
| 추정 호출 비용 | $0.00155500 |
| locked fields 보존 | 통과 |

실제 반환 문장:

> 2획: 쓴 모양이 り에 더 가깝습니다. 강조된 획을 본보기에 맞춰 다시 써 보세요.

비용은 검증 당시 [공식 Luna 모델 페이지](https://developers.openai.com/api/docs/models/gpt-5.6-luna)의
standard 가격인 입력 $1.00/1M tokens, 출력 $6.00/1M tokens로 계산했다. 이 값은 가격 변경 시
다시 계산해야 한다.

`reasoning=low`를 사용한 초기 안전성 시도는 semantic validator에서 폐기되어 정상 fallback으로
강등됐다. latency-sensitive selector에는 `reasoning=none`을 명시했다. 자유 문장을 regex만으로
검증하는 방식은 동의어 우회 가능성이 있어, Luna가 서버 승인 옵션 하나만 고르게 하고 regex는
방어 계층으로 남겼다. 위 1회는 이 최종 계약으로 다시 실행한 결과다. 반복 p50/p95와 실제
1,000-generation gate는 아직 완료로 간주하지 않는다.

## 회귀·프런트 검증

- 전체 Python 회귀: 133 passed
- 브라우저 모듈 테스트: 37 passed
- Edge TypeScript: Deno type-check 통과
- raw strokes, 이미지, 필압 시계열, session/attempt/user ID가 teacher 요청에 없음
- v1 문자 domain은 kana/CJK ideograph로 제한하고 나머지 선택 문자는 로컬 코치를 유지
- `requestId`, `attemptRevision`, `AbortController`, `decision_id`가 stale 응답을 차단
- UI 문자열은 `textContent`로만 렌더링
- `.env.local`은 Git ignore 대상이며 OpenAI 키는 서버 환경에서만 읽음
- 공개 teacher 경로는 선택적 shared token 인증과 프로세스당 동시 호출 제한을 지원
- `LINGO_SERVICE_MODE=teacher-only`에서는 실제 lifespan이 채점 모델을 preload하지 않음

## RunPod 관계

이 실제 Luna 검증에는 Chandra/Hybrid 체크포인트나 GPU Pod가 필요하지 않았다. teacher-only 모드는
두 채점 모델의 startup preload도 건너뛴다. Edge는 `TEACHER_BASE_URL`을 `RUNPOD_BASE_URL`과 따로
받으므로 Luna 설명만 CPU origin에 배포할 수 있다. 반면 기존 심층 `/score`를 원격 운영하려면 삭제된
기존 Pod 대신 새 RunPod 배포가 필요하다. 새 공개 origin이나 유료 리소스 생성은 이번 범위에서
수행하지 않았다.
