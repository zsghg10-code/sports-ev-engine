Sports EV Engine v3.6.2 — KBO Stability / Regression Control

핵심 원인
- KBO 구조모델에서 최근 팀/라인업 폼을 이미 반영한 뒤 adaptive ensemble에서 recent_form을 다시 투표시키는 중복 경로가 있었습니다.
- v3.4.25는 KBO 최근득점 확률 코어 자체도 바꿨습니다.
- generic calibration은 KBO도 40개 표본부터 활성화될 수 있어 작은 표본에서 확률이 흔들릴 수 있었습니다.
- counter_case_risk=HIGH인데 ROBUST가 표시될 수 있어 사용자가 과신하기 쉬웠습니다.

v3.6.2 수정
1. KBO 최근득점 확률 코어를 pre-v3.4.25 방식으로 롤백.
2. KBO adaptive ensemble의 recent_form 중복투표 제거.
3. KBO calibration 활성 표본 40 -> 120 unique settled picks.
4. 최근 선발 5경기 조기강판/대량실점 meltdown risk 감사필드 추가.
5. counter HIGH면 ROBUST 금지.
6. 시장 역이동 -2.0%p 이상이면 최대 SENSITIVE, -3.5%p 이상이면 REVIEW.
7. UNDER + counter HIGH + 역이동/선발붕괴 위험이면 REVIEW.
8. 결과를 보고 -1.5 마핸을 강제로 올리지는 않음. 기존 score distribution으로 독립 EV 계산.

적용
- ZIP을 저장소 루트에 그대로 업로드/덮어쓰기
- Commit changes
- Streamlit Reboot
- KBO 전체 자동분석 다시 실행

확인
- model version: 3.6.2-kbo-stability
- counter_case_risk=HIGH인 KBO 픽이 ROBUST로 남지 않음
- 강한 역이동 픽 SENSITIVE/REVIEW
- MLB/NPB/축구/NHL/NFL 확률 코어는 이번 패치에서 건드리지 않음
