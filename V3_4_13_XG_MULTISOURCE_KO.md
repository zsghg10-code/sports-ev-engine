# v3.4.13 A매치 xG 다중소스 진단

- API-Football xG 실패와 무관하게 ESPN -> FotMob -> SofaScore 실제 측정 xG를 순차 확인합니다.
- 국가대표 최근 경기 이름 매칭은 Czech Republic/Czechia, Turkey/Türkiye 등 기존 alias를 적용합니다.
- 최근 xG 커버리지가 드문 A매치를 위해 최근 후보경기 최대 12개까지 확인해 각 팀 실제 xG 3경기를 채웁니다.
- SofaScore는 경기 날짜/홈·원정/킥오프가 일치한 종료 경기의 `Expected goals` 통계만 사용합니다.
- 추정 xG를 생성하지 않습니다. 실제 측정값이 없으면 MISSING을 유지합니다.
- A매치 상태표에 `xG 후보경기`와 `xG 진단`을 추가해 후보경기 부족과 공급자 xG 부재를 구분합니다.
