# Sports EV Engine v3.6.0 — AUTOLEARN / 전종목 사후복기

기준: 현재 v3.5.0 NHL/NFL 버전.

## 이번에 바뀌는 것
- 모델 검증 탭의 MLB 전용 자동복기를 **전종목 복기**로 확장
  - 클럽축구 / A매치 / KBO / NPB / MLB / NHL / NFL
- MLB는 기존처럼 이닝·선발·불펜까지 정밀복기
- 그 외 종목은 최종점수 + 시장라인 + CLV로:
  - 사전 방향 확인
  - 접전/라인 근처 미적중
  - 좋은 가격 + 접전 미적중
  - 역CLV + 모델 미스 후보
  - 방향성 실패 후보
  를 보수적으로 분류
- Streamlit 앱이 실행되어 있을 때는 수동 버튼 없이 자동 정산/복기
- CLV는 무한 폴링 대신 경기 약 120분/20분 전 두 체크포인트만 자동 저장
- 정산 데이터가 쌓이면 종목×마켓별 **온라인 calibration 학습** 자동 활성화
  - 기존 `adaptive_model.py`의 PAVA/isotonic calibration 사용
  - 일반 마켓 최소 40 resolved pregame 표본
  - MLB totals 최소 60 표본
  - calibration bias / Model Brier vs Market Brier / 평균 CLV를 검증탭에서 같이 표시
- 과거 결과를 보고 코드를 스스로 바꾸거나 feature weight를 무검증 자동수정하는 방식은 사용하지 않음
- immutable pregame snapshot / no-lookahead 원칙 유지

## 화면 탭 순서
자동축구 → A매치 → KBO/NPB → MLB → NHL → NFL → 나머지 탭

## 평소처럼 업로드 + Reboot
GitHub `sports_ev_engine` 폴더에 아래 6개를 업로드/덮어쓰기:
- `__init__.py`
- `v350_streamlit_hook.py`
- `universal_postgame.py`
- `self_learning.py`
- `automation_tick.py`
- `auto_learn_once.py`

그 뒤 Commit changes → Streamlit Reboot.

이것만 해도 **앱이 실행 중이거나 다시 열릴 때 자동 정산/복기/학습**됩니다.

## 앱을 닫아도 경기 종료 후 24시간 자동으로 돌리려면
Streamlit Community Cloud 자체는 앱이 sleep 상태일 때 Python 코드를 계속 실행하지 않습니다.
그래서 ZIP의:
`.github/workflows/sports_ev_autolearn.yml`
도 저장소에 추가합니다.

그리고 GitHub 저장소:
Settings → Secrets and variables → Actions → New repository secret

아래 3개를 한 번만 등록:
- `THE_ODDS_API_KEY`
- `SUPABASE_URL`
- `SUPABASE_SERVICE_ROLE_KEY`

이 3개는 현재 Streamlit Secrets에 쓰는 값과 동일한 값을 복사하면 됩니다.
그 뒤 GitHub Actions가 20분마다 깨어나지만, Odds API는 실제로:
- CLV 체크포인트가 도달했거나
- 종료 예상 시간이 지난 미정산 경기가 있을 때만
조회합니다.

**Supabase가 없는 상태에서는 GitHub Actions 실행 사이에 학습 기록을 영구 보존할 수 없으므로 unattended 모드는 의도적으로 중단됩니다.**

## 왜 '완전 자가학습 AI'가 아니라 meta-learning인가
결과가 안 좋았다고 모델 코드를 자동으로 수정하면 과적합과 look-ahead가 생길 수 있습니다.
v3.6은 실제 사전 예측만 학습자료로 쓰고, 기존 PAVA/isotonic calibration을 자동으로 갱신합니다.
즉 머신러닝처럼 데이터가 쌓일수록 확률 calibration이 변하지만,
한두 경기 결과 때문에 모델 구조나 feature weight가 멋대로 바뀌지는 않습니다.
