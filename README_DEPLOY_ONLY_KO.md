# v3.5.0 NHL/NFL — DEPLOY ONLY

이 패키지는 **APPLY_V350.bat 실행이 필요 없습니다.**

기준: 현재 GitHub main의 `v3.4.25-kbo-safety`.

## 업로드할 파일
GitHub 저장소의 `sports_ev_engine` 폴더로 들어가서 아래 4개 파일을 그대로 업로드/덮어쓰기:
- `__init__.py`
- `pro_sports.py`
- `pro_sports_ui.py`
- `v350_streamlit_hook.py`

그 다음:
1. `Commit changes`
2. Streamlit이 자동 재배포되기를 기다리거나
3. 필요하면 Streamlit 앱 `⋮` → `Reboot`

## 작동 방식
기존 `app.py`를 바꾸지 않습니다.
` sports_ev_engine ` 패키지가 import될 때 Streamlit의 메인 탭 생성 지점에 안전한 hook을 설치하고,
기존 탭 뒤에 `🏒 NHL 자동분석`, `🏈 NFL 자동분석` 두 탭을 자동으로 붙입니다.

따라서 v3.4.25의:
- KBO safety hotfix
- MLB totals calibration
- A매치 validation
- 기존 축구/KBO/NPB/MLB UI

를 그대로 유지합니다.

## NHL/NFL 분석
The Odds API 시장 배당 + 공개 최근 경기 데이터로 독립 확률을 계산하고,
시장 무마진 확률, 반증 검사, 불확실성, adaptive calibration,
스트레스 시나리오, EV/ROBUST 판정, 2~6폴, 스냅샷/정산 구조를 재사용합니다.

QB/선발 골리/부상 정보가 검증되지 않으면 추정으로 채우지 않고 MISSING 처리합니다.
