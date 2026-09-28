# v3.0.1 FINAL Decision UI

이번 패치는 v3.0.0의 백엔드 분석을 바꾸는 패치가 아니라, 결과를 사용자가 원한 수동 분석 형식으로 직접 보여주는 UI 패치입니다.

각 경기 화면:

- 옵션
- 추정확률
- BE
- Edge
- EV
- 불확실성
- 상태(ROBUST/SENSITIVE/FRAGILE/REVIEW/PASS/DATA_HOLD)

그리고 바로 아래에:

- 모델 최우선 후보
- 토탈 최우선 후보
- 다폴 포함 여부
- 주요 실패경로
- 데이터 상태
- Model confidence
- 스트레스 시나리오 +EV 통과 개수 / P10 EV

클럽 축구, 국가대표 A매치, KBO/NPB 탭 모두 같은 FINAL Decision Layer를 사용합니다. 기존 상세 진단표는 그대로 유지합니다.
