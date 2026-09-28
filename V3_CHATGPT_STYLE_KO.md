# Sports EV Engine v3.0.0 — ChatGPT-style Analysis Engine

## 목적

v3.0.0은 GPT 모델 자체를 복제하지 않습니다. 대신 사람이 ChatGPT에 한 경기씩 요청했을 때 수행하던 분석 절차를 **재현 가능하고 검증 가능한 단계**로 분해합니다.

`독립 전력/득점 모델 → 실제 컨텍스트 → 반대 시나리오 → 시장 비교 → EV → 27개 스트레스 테스트 → FINAL 후보 → 경기 후 검증`

모든 확률과 EV는 모델 추정치이며 수익을 보장하지 않습니다. 스트레스 범위는 통계적 신뢰구간이 아닙니다.

## v3 분석 순서

1. **Independent model**
   - 축구: 대회 Elo + 상대강도 보정 최근 득실 + Poisson score matrix.
   - 야구: 최근 득실 + 선발/라인업/고급 신호 + Negative Binomial run matrix.
   - 이 단계는 가능한 범위에서 현재 배당과 분리해서 원확률을 만듭니다.

2. **Deep context ledger (축구, 선택적)**
   - 최근 3경기 이상에서 실제 제공된 xG/xGA.
   - 확정 선발 XI와 시즌 출전시간/평점 기반 라인업 강도.
   - API-Football injury 응답과 선수 중요도를 이용한 공격/수비 영향.
   - 최근 일정에서 휴식일.
   - PPDA/Big Chances는 공급자가 실제로 제공할 때만 기록합니다.
   - 없는 값은 리그 평균이나 임의값으로 채우지 않고 `MISSING`으로 남깁니다.

3. **Counter-case engine**
   - 작은 최근 표본, 라인업 미확정, 모델-시장 큰 괴리, 높은 uncertainty,
     부족한 deep-signal coverage, FINAL 이전 단계 등을 실패 경로로 기록합니다.

4. **Market calibration**
   - The Odds API 북메이커 컨센서스의 무마진 확률과 독립모델을 혼합합니다.
   - 원모델과 시장의 괴리가 너무 크면 `REVIEW/OUTLIER` 게이트가 유지됩니다.

5. **27-scenario robustness**
   - 축구: 홈 득점률 90/100/110% × 원정 90/100/110% × 모델 비중 80/100/120% = 27개.
   - 야구: 예상득점 92/100/108% × 상대 92/100/108% × 모델 비중 80/100/120% = 27개.
   - 각 시나리오에서 현재 배당 기준 EV를 다시 계산합니다.

6. **v3 판정**
   - `ROBUST`: 85% 이상 시나리오가 +EV이고 EV 10백분위도 양수.
   - `SENSITIVE`: base EV는 양수지만 가정 변화에 민감.
   - `FRAGILE`: +EV가 소수 가정에서만 유지.
   - `REVIEW`: 모델-시장 괴리 게이트.
   - `PASS`: base EV ≤ 0.
   - `DATA_HOLD`: 필요한 데이터/가격이 부족.
   - 자동 다폴은 `ROBUST`이면서 기존 데이터품질/FINAL/반증 게이트도 통과한 행만 사용합니다.

## 축구 deep-context 사용

클럽 축구 탭의 `v3 정밀 컨텍스트`를 켜면 킥오프 24시간 이내 경기에서 API-Football의 현재 경기 ID를 검증한 뒤 다음 데이터를 요청합니다.

- fixture statistics: 최근 xG/Big Chances(실제 반환 시)
- lineups: 확정 XI
- injuries: 결장/부상 응답
- players: 시즌 출전시간/평점/포지션 → 선수 중요도
- fixture history: 휴식일

해당 API/플랜이 값을 제공하지 않으면 분석은 중단되지 않지만 `MISSING`과 uncertainty로 남습니다. 무료 A매치 모드에서 deep-context를 시도하지 않은 경우, “수집하지 않았다”는 이유만으로 deep coverage 페널티를 추가하지 않습니다.

## 불변 예측 저장과 사후평가

분석 실행 시 다음을 `data/prediction_snapshots.jsonl`에 append-only로 저장합니다.

- event ID / kickoff / teams / market / selection / line
- 당시 best odds / consensus probability
- raw independent probability / final model probability
- BE / Edge / EV / conservative EV / uncertainty
- v3 robustness 결과 / counter-case / data quality / model version

동일한 가격+모델 상태는 fingerprint로 중복 저장하지 않습니다. 가격 또는 확률이 바뀌면 새 스냅샷으로 저장되며 과거 값은 덮어쓰지 않습니다.

`python settle_once.py` 또는 앱의 **모델 검증** 탭은 The Odds API scores에서 최근 종료 결과를 연결합니다.

평가지표:

- Brier score
- Log loss
- 적중률
- 실현 ROI
- 종목 × 마켓 × v3 판정 그룹별 성과

아시안 0.25 라인의 half-win/half-push 등은 실현 ROI에는 정확히 포함합니다. 부분 적특은 이진 Bernoulli 결과가 아니므로 Brier/Log loss 표본에서는 제외합니다.

## 운영

```bash
pip install -r requirements.txt
streamlit run app.py
```

정산을 스케줄러에서 실행하려면:

```bash
python settle_once.py
```

기본 Secrets:

```toml
THE_ODDS_API_KEY = "..."
API_FOOTBALL_KEY = "..."   # 축구 deep context/클럽 분석에서 사용
TELEGRAM_BOT_TOKEN = "..." # 선택
TELEGRAM_CHAT_ID = "..."   # 선택
```

## 검증 상태

- Python 전체 compile 검사 통과.
- v3 reasoning/store/parlay + 기존 핵심 정책 테스트 통과.
- 현재 제작 환경에는 `streamlit` 패키지가 설치되어 있지 않고 외부 패키지 설치 네트워크가 차단되어 있어 `streamlit.testing.AppTest` 1개만 이 환경에서 실행하지 못했습니다.
- 배포 환경에서 `pip install -r requirements.txt` 후 전체 테스트를 다시 실행할 수 있습니다.
