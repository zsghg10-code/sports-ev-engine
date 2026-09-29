# v3.4.5 — 넓은 +EV 후보풀 + A매치 다중 xG fallback

## 오늘의 베스트 조합 후보 계층

기준 EV가 양수인 선택지는 v3 판정이 REVIEW/PASS여도 더 이상 숨기지 않습니다.

화면에서 다음 세 단계로 분리합니다.

- **검토 후보**: 기준 +EV이지만 REVIEW/FRAGILE/데이터·드리프트·반증 안전게이트 때문에 실제 베팅 후보로 승격하지 않음
- **단일 후보**: 보수 EV와 기본 안전게이트는 통과했지만 2폴 이상용 보수 적중확률 50% 기준 등 다폴 게이트를 통과하지 않음
- **조합 가능**: ROBUST/SENSITIVE + 보수 EV + 데이터/드리프트/반증/신선도 게이트 + 다폴용 보수확률 기준까지 통과

`best_combos()`는 1픽에서는 `단일 후보`까지 사용할 수 있고, 2폴 이상에서는 `조합 가능`만 사용합니다.

## A매치 xG 수집 cascade

xG는 추정해서 만들지 않고 실제 측정값만 사용합니다.

1. API-Football 최근 경기 statistics의 Expected Goals
2. ESPN 공개 match-summary의 명시적 팀 xG
3. FotMob 공개 match-details의 All-period `expected_goals` 팀 통계

각 팀 최근 **3경기 측정 xG/xGA**가 모두 확보되어야 reasoning engine의 recent_xG 신호로 반영합니다.

- 3/3 + 3/3: 모델 반영
- 1~2개만 확보: `부분수집`으로 표시하되 모델에는 미반영
- 전부 실패: `MISSING`

화면에는 `xG 소스`, `xG 시도`, `홈/원정 표본`을 함께 표시합니다.

FotMob 경로는 비공식 공개 JSON 경로이므로 fail-soft로만 사용하며 실패 시 분석 전체를 중단하지 않습니다. 일별 목록과 match detail은 프로세스 캐시를 사용해 반복 호출을 줄입니다.
