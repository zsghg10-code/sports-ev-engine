# Sports EV Engine v3.6.3 NHL Integrity Fix

## 수정 핵심
- NHL 최근 경기 조회 45일 → 시즌 초 직전 시즌 감쇠 fallback 포함 260일
- 500경기 API 제한을 피하도록 장기 조회를 60일 단위로 분할
- 현재 시즌 1.0 가중 / 직전 시즌 0.42 기반 + 시간 감쇠
- `DATA_HOLD / PROVISIONAL / FINAL` 분리
  - DATA_HOLD: 핵심 득점표본/가격 자체가 없음
  - PROVISIONAL: 확률·EV 계산 가능, 선발 골리 등 최종 컨텍스트 미확정
  - FINAL: 양쪽 선발 골리 명시 확인 + 최소 컨텍스트 충족
- 선발 골리는 odds/로스터 순서로 추측하지 않고 ESPN payload의 명시적 starter 표지만 인정
- shots against까지 수집해서 shot-share 진단 보강
- NHL h2h를 `OT_INCLUDED_2WAY` / `REGULATION_3WAY`로 분리
- PROVISIONAL에서는 공식 다폴 제외, UI에서 별도 잠정 조합만 표시
- 골리 확정 후 재실행하면 FINAL 승격 가능

## 교체 파일
- `sports_ev_engine/pro_sports.py`
- `sports_ev_engine/pro_sports_ui.py`
- `tests/test_v363_nhl_integrity.py` (신규)

## 적용 후 기대 화면
기존처럼 NHL 전체가 `DATA_HOLD / signal_coverage=0 / robust_ev=None`으로 일괄 막히지 않습니다.
시즌 초에는 대부분 `PROVISIONAL`로 확률·EV·stress 결과가 계산되고, 선발 골리 확인 후 `FINAL` 후보만 공식 다폴에 들어갑니다.
