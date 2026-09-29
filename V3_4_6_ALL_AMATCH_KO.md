# v3.4.6 — 모든 성인 A매치 다중소스 자동발견

- A매치 대회 목록을 고정 whitelist에만 의존하지 않습니다.
- `전체 A매치 · 다중소스 자동발견`은 선택 KST 날짜의 API-Football 전체 fixture를 조회하고 성인 국가대표 대회를 동적으로 판별합니다.
- 대상 예: UEFA/CONCACAF Nations League, World Cup/대륙 예선, AFCON/Africa Cup 계열, Asian Cup, Gold Cup, Gulf/Arab Cup, Copa América, 성인 국제 친선전, EAFF/SAFF/WAFF/COSAFA/CECAFA/OFC Nations 등.
- 배당 우선순위: The Odds API 활성 국제대회 → API-Football pre-match odds fallback.
- The Odds API에 대회 키가 없어도 API-Football 사전 배당이 있으면 h2h/total/지원되는 handicap을 같은 EV 파이프라인으로 분석합니다.
- 일정은 발견됐지만 어느 연결 배당 소스에도 가격이 없으면 `일정 활성 · 배당 없음`으로 표시하고 EV는 만들지 않습니다.
- 중복 경기는 The Odds API를 우선하고 API-Football fallback은 해당 이벤트가 없을 때만 추가합니다.
- U17/U20/U21/U23/여자/올림픽/클럽 대회는 성인 A매치 풀에서 제외합니다.

중요: '모든 A매치'는 연결된 fixture provider가 발견한 성인 국가대표 일정을 뜻합니다. 배당이 실제로 제공되지 않는 경기에 가상의 가격을 생성하지 않습니다.
