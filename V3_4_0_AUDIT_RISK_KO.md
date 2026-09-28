# Sports EV Engine v3.4.0 — Audited Risk Loop

## v3.4 신규 기능

1. **Source Health + Fallback**
   - 경기별로 배당/모델/라인업/xG·부상/Statcast/불펜 소스의 실제 수집 상태를 표시합니다.
   - A매치 라인업은 API-Football confirmed XI를 우선하고, 미게시 시 검증된 ESPN 공개 starter를 fallback으로 사용합니다.
   - 공식 라인업이 아직 없으면 `PROBABLE`을 별도 단계로 유지합니다. `PROBABLE`은 확정으로 승격하지 않습니다.
   - MLB에서 Savant 일부 데이터가 실패해도 MLB Stats API play-by-play의 Whiff/Chase/Zone/Contact·구속 신호는 fallback으로 사용할 수 있으며, Savant 전용 xwOBA/Barrel/HardHit은 MISSING으로 남깁니다.

2. **PROBABLE → CONFIRMED 2단계 라인업 모델**
   - 공식 startXI 전에는 시즌 player importance + availability로 낮은 가중치의 `MODEL-PROJECTED XI`를 만들 수 있습니다.
   - projected XI는 확인된 라인업 효과의 40%만 허용하며 추가 불확실성을 유지합니다.
   - 공식 startXI가 오면 즉시 `FINAL`로 교체됩니다.

3. **Blind Paper-Trading / No-lookahead lock**
   - 모든 분석 snapshot에 `paper_locked`, `paper_eligible`, `paper_minutes_before`, `lookahead_guard`를 저장합니다.
   - 킥오프 이후 생성된 snapshot은 Calibration, Correlation, Replay/Backtest 학습 표본에서 제외됩니다.
   - 결과를 본 뒤 과거 예측을 덮어쓰지 않습니다.

4. **Feature Attribution**
   - 독립/정밀모델, 시장 anchor, 최근폼, calibration의 확률 기여를 감사용으로 분해합니다.
   - xG/라인업/결장 같은 비선형 컨텍스트는 signal ledger와 residual로 분리합니다. Shapley 인과기여로 가장하지 않습니다.

5. **Model Drift 경보**
   - 종목×마켓별 최근 표본과 과거 baseline의 Brier/ROI/CLV를 비교합니다.
   - Brier 악화 또는 CLV 하락이 임계값을 넘으면 WATCH/ALERT를 표시합니다.
   - ALERT 시장은 `오늘의 베스트 조합`에서 자동 제외하고, WATCH는 보수 감점합니다.

6. **Bankroll / Drawdown 시뮬레이터**
   - fractional Kelly, 픽당 노출 상한, 회차 총노출 상한을 적용합니다.
   - Monte Carlo로 100회 후 자금분포, 최대 drawdown, 초기자금 50% 이하 도달 확률을 표시합니다.
   - 수익보장이 아니라 입력확률이 맞다는 가정의 위험 시뮬레이션입니다.

### 새 UI

`🛡️ 데이터·리스크` 탭에서 Source Health, Blind Paper lock, Feature Attribution, Drift, Bankroll/Drawdown을 확인합니다.

---

