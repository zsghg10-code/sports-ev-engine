# Sports EV Engine v3.4.3 — xG Source Fallback

v3.4.2의 경기별 설명/후보 게이트 구조를 유지하면서 A매치 xG 수집을 보강했습니다.

- API-Football 최근 팀 경기에서 xG/xGA 직접 수집
- 무료 A매치 기록 모드에서도 모델링 pool의 fixture id 유무와 무관하게 xG 조회
- API-Football xG 불완전 시 ESPN 공개 match-summary Expected Goals fallback
- 양 팀 각각 최소 3개 실측 xG 표본이 없으면 MISSING
- xG 소스/fallback/표본 수를 UI 및 저장 스냅샷에 기록
- 추정 xG 생성 금지

자세한 내용: `V3_4_3_XG_FALLBACK_KO.md`
