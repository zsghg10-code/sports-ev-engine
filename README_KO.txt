# Sports EV Engine v3.6.1 KBO starter-authority hotfix

증상
- 라인업 1~9가 모두 확정인데도 `starter_confirmed=False`
- `STARTER_CONFLICT`
- `robust_positive_ratio=0`, `robust_ev_p10=None`
- 모든 KBO 선택지가 `검토 후보`

원인
- v3.4.25 안전패치가 KBO GameCenter의 오래된 선발명과 이미 확정된 Naver 라인업의 실제 선발명이 다르면
  무조건 충돌로 막도록 되어 있었습니다.

수정
- 날짜/팀이 매칭된 Naver 경기 + source_game_id + 양 팀 1~9 완성 + 양 팀 선발명 존재 시 Naver를 당일 최신 권위 소스로 사용합니다.
- GameCenter 선발명이 다르면 `stale official`로 기록하고 Naver 선발로 교체합니다.
- `starter_confirmed=True`, `starter_verified=True`, `stage=FINAL` 경로를 복구합니다.
- 기존 v3.4.25의 old pitcherId 폐기/이름 기준 재조회 로직을 그대로 활용하여 최근 선발/상대전적 데이터도 새 선발명으로 재조회합니다.
- 부분 라인업/선발명 누락 때는 기존 안전게이트를 그대로 유지합니다.

적용
1. ZIP을 풀어 저장소 루트에 그대로 덮어씁니다.
2. GitHub에 `sports_ev_engine/__init__.py`와 새 파일 `sports_ev_engine/kbo_starter_authority_fix.py`를 업로드/커밋합니다.
3. Streamlit 앱을 Reboot합니다.
4. `선택 종목 전체 자동분석`을 다시 실행합니다.

정상 확인
- 오늘 KBO 5경기에서 `STARTER_CONFLICT`가 사라져야 합니다.
- `starter_confirmed`가 체크되어야 합니다.
- 실제 선발명이 오늘 확정 라인업과 같아야 합니다.
- `starter_vs_opponent_used`는 해당 선수의 상대전적 데이터가 실제 존재할 때만 체크됩니다. 비어 있어도 `starter_confirmed`를 다시 False로 만들면 안 됩니다.
- FINAL/단일/조합 여부는 그 뒤 EV·강건성 게이트가 정상적으로 다시 판단합니다.

주의
이 패치는 FINAL 기준을 느슨하게 만든 것이 아니라, '완성된 당일 라인업 + 양쪽 선발명'을 오래된 GameCenter 선발 필드보다 최신 데이터로 취급하는 수정입니다.
