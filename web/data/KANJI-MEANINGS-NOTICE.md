# 한자 훈음·읽기 자료 출처

`kanji-meanings.json`은 `python scripts/build_kanji_meanings.py`로 만든 파생 데이터입니다.
각 한자는 `[훈음, 음독, 훈독]` 배열입니다.

- **훈음(한국어 뜻과 음)**: libhangul `data/hanja/hanja.txt`
  https://github.com/libhangul/libhangul — 아래 BSD-3-Clause 고지를 재배포 시 함께 보존하세요.
  변경: 한 글자 항목만 추출, 뜻을 최대 2개로 줄임, 한자가 섞인 설명 제거,
  일본 신자체는 구자체(예: 国→國)의 훈음을 사용, 일부 글자는 편집자가 직접 작성한
  훈음으로 대체(`OVERRIDES`, 일본 국자 포함).
- **음독·훈독, 구자체 대응**: EDRDG KANJIDIC2, CC BY-SA 4.0 — `KANJIDIC2-NOTICE.md` 참고.
- **신자체→구자체 보조 대응**: Unicode Unihan Database, Unicode License v3,
  https://www.unicode.org/license.txt

```
Copyright (c) 2005,2006 Choe Hwanjin
All rights reserved.

Redistribution and use in source and binary forms, with or without
modification, are permitted provided that the following conditions are met:

1. Redistributions of source code must retain the above copyright notice,
   this list of conditions and the following disclaimer.
2. Redistributions in binary form must reproduce the above copyright notice,
   this list of conditions and the following disclaimer in the documentation
   and/or other materials provided with the distribution.
3. Neither the name of the author nor the names of its contributors
   may be used to endorse or promote products derived from this software
   without specific prior written permission.

THIS SOFTWARE IS PROVIDED BY THE COPYRIGHT HOLDERS AND CONTRIBUTORS "AS IS"
AND ANY EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT LIMITED TO, THE
IMPLIED WARRANTIES OF MERCHANTABILITY AND FITNESS FOR A PARTICULAR PURPOSE
ARE DISCLAIMED. IN NO EVENT SHALL THE COPYRIGHT OWNER OR CONTRIBUTORS BE
LIABLE FOR ANY DIRECT, INDIRECT, INCIDENTAL, SPECIAL, EXEMPLARY, OR
CONSEQUENTIAL DAMAGES (INCLUDING, BUT NOT LIMITED TO, PROCUREMENT OF
SUBSTITUTE GOODS OR SERVICES; LOSS OF USE, DATA, OR PROFITS; OR BUSINESS
INTERRUPTION) HOWEVER CAUSED AND ON ANY THEORY OF LIABILITY, WHETHER IN
CONTRACT, STRICT LIABILITY, OR TORT (INCLUDING NEGLIGENCE OR OTHERWISE)
ARISING IN ANY WAY OUT OF THE USE OF THIS SOFTWARE, EVEN IF ADVISED OF THE
POSSIBILITY OF SUCH DAMAGE.
```
