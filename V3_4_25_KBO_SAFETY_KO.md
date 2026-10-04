# Sports EV Engine v3.4.25 — KBO Safety Hotfix

## 왜 수정했나
2026-10-04 두산-삼성처럼 경기 직전 실제 선발이 예고/기존 수집값과 달라졌는데도 이전 선발 기준 확률·EV가 남는 경로를 차단합니다. 같은 날 NC-SSG totals에서 드러난 단일 대량득점/최근 득점 과대반영과 얇은 Edge 다폴 승격도 함께 보수화합니다.

## 변경점
- KBO FINAL 상태에서도 Naver pregame 1~9 타순이 완성되면 선발명을 추가 교차검증합니다.
- KBO GameCenter와 Naver의 선발명이 충돌하면 `STARTER CONFLICT`로 강등하고 기존 픽/EV/다폴을 즉시 HOLD합니다.
- 충돌 시 과거 GameCenter pitcherId를 폐기하고 현재 선발명으로 투수 ID/최근 기록을 다시 조회합니다.
- 최근 10경기 득점은 시즌 평균 기준 ±3.5점 winsorize 후 80%만 최근값으로 반영하여 13득점 같은 단일 폭발의 다음날 과대반영을 완화합니다.
- KBO totals 스트레스 범위를 기존 ±8%에서 `-12/-6/0/+6/+12%`로 넓히고 불확실성 +0.75%p를 추가합니다.
- KBO totals 단일 후보는 Edge 2.5%p 이상, 다폴은 Edge 4.0%p·보수 EV +2%·stress positive 90%·p10 EV +1.5%를 요구합니다.
- KBO 승패/핸디 다폴은 Edge 3.0%p 이상을 요구합니다.
- Daily Combo가 `v3_parlay_eligible=False`인 SENSITIVE 픽을 다시 다폴로 승격시키던 경로를 차단합니다.
- 야구 경기 45분 전부터는 분석 스냅샷 10분, 45~120분 전에는 30분을 넘으면 단일/다폴 모두 재분석 대기로 전환합니다.
- Top 조합 여러 개를 표시할 때 한 픽이 가능한 모든 티켓의 공통축이 되지 않도록 노출을 분산합니다(대안이 없으면 완화).

## 기존 기능 보존
MLB totals calibration, A매치 validation, 기존 KBO/NPB 수집·UI·정산 구조는 변경하지 않습니다. 확률 모델 자체를 갈아엎지 않고, 입력 신선도와 후보 승격/스트레스 경로만 보수적으로 패치합니다.
