# v3.4.11 — A매치 KST 날짜 자동발견 보강

- API-Football `/fixtures` 조회를 선택 KST 날짜 + `timezone=Asia/Seoul` 방식으로 변경했습니다.
- Africa Cup of Nations - Qualification(AFCON 예선), CONCACAF Nations League, Gulf/Arab Cup, 성인 친선전 등 The Odds API 비활성 대회도 날짜별 fixture-first discovery 대상으로 유지합니다.
- 상단 제공 상태 표는 The Odds API 상태와 다중소스 처리 상태를 분리해 표시합니다.
- `전체 A매치 · 다중소스 자동발견` 선택 시 분석 버튼 전에도 선택 날짜의 API-Football 성인 A매치 일정을 사전 확인해 대회별 경기 수를 표시합니다.
- 실제 배당이 없으면 일정은 표시하되 EV는 만들지 않습니다.
- 발견 진단에 전체 fixture 수, 성인 A매치 accepted 수, 사용 timezone, 제외 대회 예시를 표시합니다.
- 빌드 변경 시 이전 A매치 discovery/lineup session cache를 초기화합니다.
