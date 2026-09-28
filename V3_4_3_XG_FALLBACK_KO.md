# v3.4.3 — A매치 xG 수집/fallback 개선

## 변경점

A매치 무료 기록 모드에서 라인업은 ESPN 공개소스 fallback으로 확보되지만 xG는 `미수집`으로 남던 문제를 개선했습니다.

### xG 수집 순서

1. API-Football에서 대상 경기의 팀 ID를 확인합니다.
2. 각 팀의 최근 종료 경기 목록을 API-Football에서 직접 조회합니다.
3. 최근 경기의 `fixtures/statistics`가 명시적으로 반환한 Expected Goals만 사용합니다.
4. 양 팀 각각 최소 3경기 xG/xGA 표본이 확보되면 정밀 컨텍스트에 반영합니다.
5. API-Football 표본이 완전하지 않으면 ESPN 공개 match summary에서 명시적으로 제공된 Expected Goals를 fallback으로 확인합니다.
6. ESPN에서도 양 팀 각 3경기 표본을 확보하지 못하면 xG는 `MISSING`으로 유지합니다.

어떤 단계에서도 슈팅·점유율·스코어로 xG를 임의 추정하지 않습니다.

## UI

A매치 수집상태 표에 다음 컬럼을 추가했습니다.

- `xG`: 반영 / 미수집
- `xG 소스`: API-Football 또는 ESPN public match-summary xG fallback
- `xG 표본`: 홈/원정 실제 사용 경기 수

`데이터·리스크` 소스 상태에도 xG fallback 여부와 사용 소스를 표시합니다.
