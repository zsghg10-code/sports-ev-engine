# v3.4.12 — A매치 xG fail-soft 분리

- API-Football fixture/statistics 요청이 실패하거나 rate-limit 되어도 ESPN/FotMob measured-xG fallback을 계속 시도합니다.
- 공개 xG fallback은 API-Football fixture id에 의존하지 않습니다.
- 각 팀 최근 3경기 measured xG가 모두 확보된 경우만 모델에 반영합니다.
- 1~2경기만 확보되면 부분수집 표본으로만 표시하고 모델에는 반영하지 않습니다.
- `xG 시도`에는 항상 실제 cascade인 `API-Football → ESPN → FotMob`을 표시합니다.
- 어느 소스에서도 실제 xG를 얻지 못하면 MISSING으로 유지하며 값을 추정하지 않습니다.

- 전체 A매치 자동발견 모드에서는 ESPN 최근 기록 풀도 활성 The Odds API 대회에 한정하지 않고 전체 국제대회(friendly/UEFA/CAF/AFC/CONCACAF/CONMEBOL/OFC)를 수집해 공개 xG fallback 표본 누락을 줄입니다.
