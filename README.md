# Sports EV Engine v2

v1의 EV/축구/MLB/NFL/다폴 엔진 위에 **오늘 경기 자동 불러오기** 흐름을 추가한 버전입니다.

## v2 추가 기능
- The Odds API에서 실시간 배당 자동 수집
- 사용 가능한 종목 목록 자동 로드
- 여러 북메이커 마진 제거(power de-vig)
- 시장 컨센서스 공정확률 계산
- 가장 좋은 배당과 시장 컨센서스의 가격 차이 자동 정렬
- MLB 일정/예고선발 자동 로드(키 불필요)
- API-Football 라인업/부상 조회
- 기존 독립모델 EV와 2~6폴 최적화 유지
- Streamlit Secrets 지원

## Streamlit Cloud API 키 연결
앱 오른쪽 아래 `Manage app` → `Settings` → `Secrets`에 입력:

```toml
THE_ODDS_API_KEY = "..."
API_FOOTBALL_KEY = "..."
```

## 중요한 구분
첫 화면의 `market_ev`는 **여러 북메이커 무마진 컨센서스 대비 가격 우위**입니다.
독립 스포츠 모델이 만든 진짜 예측 EV는 `후보 EV` 탭의 `model_win_prob`을 통해 계산합니다.

## GitHub 업데이트
기존 저장소에 이 ZIP의 파일을 덮어쓰면 Streamlit이 자동 재배포합니다.
