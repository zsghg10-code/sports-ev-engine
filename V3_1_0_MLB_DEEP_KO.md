# Sports EV Engine v3.1.0 — MLB 최고정밀 계층

이번 버전은 MLB v3.0.3 위에 pitch-level/Statcast 정밀 계층을 추가한다.

## 실제 반영 신호

1. Whiff / Chase / Zone / Contact
2. Statcast xwOBA / Barrel / HardHit
3. GB/FB / HR/FB / BABIP 회귀
4. 구종별 구사율 변화 + Savant RV/100/Whiff/xwOBA 기반 `Stuff proxy`
5. 선발 최근 투구수 / 휴식 / workload
6. 최근 3일 불펜 정확 투구수 / 연투
7. 확정 타순 타자별 좌우 OPS 합산
8. 상대 선발 구종 × 현재 라인업 구종별 xwOBA
9. MLB 공식 부상·복귀 transaction + 경기 임박 뉴스 alert
10. 확정 라인업 변경 감지
11. Odds API 컨센서스 가격의 관측 시점별 이동
12. roof 구조/상태 + 주심 배정
13. 이동거리 / 시차 / 실제 휴식시간
14. 최근 3시즌 BvP
15. 최근 10경기 불펜 재사용/연투 운용 패턴

## no-fake 원칙

- 실제 source가 없으면 `MISSING`.
- 공개 데이터에서 독점 `Stuff+`를 가장하지 않는다. 대신 Baseball Savant의 RV/100, Whiff%, xwOBA와 실제 구사율을 이용한 `Stuff proxy`라고 명시한다.
- 심판 성향은 검증된 최근 홈플레이트 경기 표본만 표시하며, 검증되지 않은 임의 토탈 계수는 넣지 않는다.
- 뉴스 제목은 alert/context 용도이며 기사 제목만으로 득점 기대값을 직접 변경하지 않는다.
- BvP는 합산 30 PA 미만이면 방향 보정에 사용하지 않는다.
- 시장 이동은 첫 관측 후부터 자동 축적된다. 이전 관측이 없으면 `MISSING`이다.

## 모델 결합

기존 독립 득점모델 → 선발/타선/불펜 → v3.1 정밀신호 → 시장 캘리브레이션 → 반증 → 27개 스트레스 시나리오 → EV/ROBUST 판정 순서다.
정밀신호 전체가 독립모델을 과도하게 덮지 못하도록 최종 득점환경 보정은 제한된다.
