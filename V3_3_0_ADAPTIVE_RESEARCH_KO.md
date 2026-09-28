# v3.3.0 Adaptive Research

이 버전은 분석항목을 단순히 늘리는 것이 아니라 모델이 자기 확률을 검증·교정하고 변화 이유를 감사할 수 있게 합니다.

- calibration은 40개 resolved unique pick 미만에서 비활성
- refresh snapshot 중복은 학습 표본에서 제거
- 앙상블 충돌 14%p 이상은 REVIEW
- correlation은 20개 이상 겹치는 KST day 표본이 있을 때만 사용
- replay는 저장 snapshot만 사용해 no-lookahead 유지
- 실패원인 반복은 수정 후보만 띄우며 자동 weight mutation은 하지 않음

운영 순서: 각 종목 분석 → smart_worker로 재분석/CLV → 자동 정산 → 모델 연구소에서 calibration/backtest/실패원인 확인.
