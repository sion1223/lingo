# 선택형 모양 예시용 글꼴

세 TTF는 아래 제작자 공식 저장소의 고정 revision에서 받은 **변경하지 않은 원본**입니다.
서브셋·변환·윤곽 추출을 하지 않았습니다. 파일별 URL, SHA-256, 원저작권 표시,
OFL 파일 해시, cmap 문자 범위는 `manifest.json`에 있습니다.

| 글꼴 | 공식 출처 | 고정 revision | 함께 배포하는 고지 |
|---|---|---|---|
| Klee One Regular | https://github.com/fontworks-fonts/Klee | 8b0532731b63ad8a445ca341d8d7d941079b83ab | klee-one/OFL.txt |
| Zen Kurenaido Regular | https://github.com/googlefonts/zen-kurenaido | cd751ced55d4ebb1fb2fbd90602eb9351dd03fd6 | zen-kurenaido/OFL.txt |
| Yomogi Regular | https://github.com/satsuyako/YomogiFont | 8551d68706679b88ce6ff40e093a993e546e593e | yomogi/OFL.txt |

배포 확인: 2026-10-03. Google Fonts 각 가족의 `METADATA.pb`가 위 공식 source revision과
TTF 경로를 가리키는 것도 확인했습니다. Klee·Zen·Yomogi 각 저작권자의
OFL 원문을 원본 그대로 동봉했습니다.

세 배포본의 라이선스는 SIL Open Font License 1.1입니다. 앱 내 포함·웹폰트 사용은
허용되며 재배포 시 원저작권 표시와 라이선스를 보존해야 합니다. 폰트 파일만 따로
판매할 수 없습니다. 폰트 또는 파생본에는 OFL을 유지하며, 수정본이 있다면 Reserved
Font Name 및 저작자 이름을 통한 보증·홍보 제한을 다시 확인해야 합니다.
Yomogi 프로젝트 README의 파생본 명칭 안내도 따라야 합니다. 이 변경은 수정본을
만들지 않았습니다. 앱의 일반 코드·글꼴을 사용한 문서가 OFL로 바뀌는 것은 아닙니다.

화면에 선택된 가족명과 해당 OFL 링크를 제공합니다. 정부 지정·공인·권장 서체로
표시하지 않습니다. 글꼴은 **완성 모양 참고 전용**이며 획순·필압·속도 데이터를
제공하지 않습니다. 글꼴 선택이 따라쓰기 경로, 코치의 템플릿, 최종 점수를 바꾸지 않습니다.
선택한 글꼴에 문자가 없거나 로딩이 실패하면 기본 글꼴 표시임을 명시합니다.

전체 파일 약 17.0 MB이며 브라우저는 선택한 가족만 읽습니다.
라이선스 원본 보존과 오프라인 정적 호스팅을 위해 현재는 TTF를 자체 제공하며,
추후 압축·서브셋은 별도 라이선스·명칭 검토 후 진행하세요.

검증용 메타데이터 재생성: `python scripts/audit_handwriting_fonts.py`
(개발 시에만 fontTools 필요; 앱 런타임 의존성 아님).
