# Sports EV Engine API-Football 쿼터 보호 DEPLOY 패치

기준 저장소: `zsghg10-code/sports-ev-engine`  
패치 제작 시 확인한 main HEAD: `e86f8821186f8d01872aa1504ebd300b063c1673`

이번 수정은 **배당 수집은 정상인데 API-Football 정밀 컨텍스트가 24경기 전체에 반복 호출되면서 요청 제한에 걸리는 문제**를 줄이기 위한 배포 패치입니다.

적용 후 구조는 **전체 경기 1차 분석 → 상위 N경기만 정밀 분석**입니다. 기본 N은 5이고 UI에서 2~8로 조절할 수 있습니다. 정밀 대상이 아닌 경기는 미확인 정보를 만들어 FINAL로 올리지 않고 REVIEW/STAGED_OUT 성격으로 남깁니다.

추가로 대회 fixture pool의 최근 경기 ID를 xG 수집에 재사용하고, 확정 라인업·결장·예상XI에 필요하지 않은 선수 시즌 페이지 호출을 생략합니다. HTTP 429에서는 Retry-After를 읽고, 일일 잔여량/분당 잔여량/실제 호출 수/캐시 수/보류 시간을 화면에 표시합니다.

## 적용

이 ZIP의 파일들을 **sports-ev-engine 저장소 루트**에 풀어놓고 Windows에서는 `APPLY_QUOTA_GUARD_PATCH.bat`을 실행하세요.

터미널이라면:

```bash
python APPLY_QUOTA_GUARD_PATCH.py
```

성공하면 실제 런타임 파일인 `app.py`, `sports_ev_engine/providers/api_football.py`, `sports_ev_engine/deep_soccer_context.py`가 수정되고, 존재하는 중복 복사본도 가능한 경우 동기화합니다. 원본은 `_quota_guard_backup/`에 자동 백업합니다.

패치 스크립트는 모든 필수 marker를 먼저 확인하고 **수정된 전체 Python 파일을 ast.parse로 검증한 후에만 디스크에 기록**합니다. 기록 후 `py_compile`도 실행합니다.

적용 뒤 클럽 축구 탭에서 다음 문구가 보이면 정상입니다.

- `정밀 컨텍스트 최대 경기 수`
- `API-Football 쿼터 보호: 1차 XX경기 → 정밀수집 N경기`
- `API-Football 실제 호출 ... · 일일 잔여 ... · 분당 잔여 ... · 캐시 ...`

이미 **오늘 일일 한도를 모두 소진했다면 패치 적용 자체가 quota를 복구하지는 않습니다.** 다음 reset 이후부터 같은 전체분석의 요청 소모를 크게 줄이는 구조입니다.
