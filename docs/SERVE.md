# 링고 채점 서비스 운영 가이드

## 구성

```
아이패드(애플펜슬) ──> Supabase app 페이지 ──> Supabase score 함수 ──> RunPod GPU 서버
                     (그리기 UI, 항상 켜짐)   (프록시+기록, 항상 켜짐)   (모델 추론, on/off)
```

- **웹앱 (짧은 링크)**: https://tnsxhhdmnbhgzqgwosto.supabase.co/functions/v1/lingo
  (아이패드 Safari에서 열고 "홈 화면에 추가"하면 앱처럼 사용 가능.
  실제 페이지는 RunPod pod가 직접 서빙하며 위 주소는 302 리다이렉트 —
  원본 주소: https://l8faq6mx5shxpc-8000.proxy.runpod.net/
  supabase.co 도메인 자체는 HTML을 렌더링하지 않고 항상 리다이렉트만 하므로
  pod가 꺼져 있으면 이 링크도 접속이 안 됨)
- **RunPod pod**: `lingo-scorer` (ID `l8faq6mx5shxpc`, NVIDIA L4 24GB, secure cloud EU-RO-1)
- **채점 서버 직접 주소**: https://l8faq6mx5shxpc-8000.proxy.runpod.net/health
- **제출 기록**: Supabase `submissions` 테이블 (문자, 획, 점수, 리포트 자동 저장)

## 켜기 / 끄기 (과금은 켜져 있는 동안만: 시간당 $0.39 + 볼륨 보관 소액)

- **Windows 자동 실행(권장)**: `run_lingo.bat` 더블클릭. Pod를 켜고 서버가
  준비될 때까지 기다린다. 사용을 마친 뒤 Enter/Ctrl+C를 누르거나 창을
  닫으면 Pod도 자동으로 Stop된다. 첫 실행 때만 RunPod API key를 입력하며,
  Windows DPAPI로 현재 사용자에게만 복호화되게 저장된다. 키를 바꾸려면
  `run_lingo.bat -ResetKey`를 실행한다. Pod가 이미 켜져 있으면 현재 로컬 창이
  제어를 인계하고, 창을 닫을 때 함께 Stop한다.
- **끄기**: RunPod 콘솔(https://console.runpod.io/pods) → lingo-scorer → Stop
- **켜기**: 같은 화면에서 Start → 1~2분 뒤 서버 자동 기동(모델 로드 포함 2~3분)
- Claude에게 "채점 서버 켜줘/꺼줘"라고 해도 됨 (runpod MCP 연결됨)
- 웹앱 상단 상태표시등: 초록=온라인, 노랑=모델 로드 중, 빨강=꺼짐

pod를 **Stop**하면 GPU 과금이 멈추고 /workspace 볼륨(코드·체크포인트·모델캐시)은
유지된다. **Terminate**는 볼륨까지 삭제되므로 주의.

## 자동 기동 원리

pod env `JUPYTER_CONFIG_DIR=/workspace/.jupyter` 때문에 부팅 때마다 Jupyter가
`/workspace/.jupyter/jupyter_server_config.py`를 실행 →
`/workspace/lingo/serve.sh` → venv 활성화 후 uvicorn(포트 8000) 기동.

## 서버 SSH 접속 (관리용)

```
ssh -i ~/.ssh/lingo_runpod -p <포트> root@<IP>
```
IP/포트는 pod 재시작 때마다 바뀜 — RunPod 콘솔 Connect 탭에서 확인.
로그: `/workspace/logs/server.log`

## 파일 위치 (pod 볼륨 /workspace)

- `lingo/scorer/` 코드, `lingo/checkpoints/chandra_scorer.pt` 체크포인트
- `lingo/kanji/` KanjiVG SVG 템플릿
- `hf/` Chandra 백본 캐시 (~16GB, 첫 로드 때 다운로드 후 재사용)
