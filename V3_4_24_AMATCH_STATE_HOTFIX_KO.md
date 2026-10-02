# v3.4.24 A매치 후보 상태 HOTFIX 1

적용 대상: 현재 GitHub main v3.4.24-national-validation

수정:
- `scenario_candidate` / `v3_candidate`를 최종 상태로 덮어쓰던 문제 제거
- `FINAL_BET / PROVISIONAL / COMBO_EXCLUDE / NO_BET`는 `national_*` 필드로 분리
- `REVIEW`, `HIGH counter-case risk` 등이 데이터 미확인 때문에 잠정 후보로 잘못 승격되던 순서 버그 수정
- 상세 진단에 해당 경기의 전체 최종/잠정 후보 수와 선택명을 표시
  - 승무패/대표 O-U 요약에 안 보이는 +1, +1.5 같은 핸디캡 후보도 확인 가능
- 기존 app.py와 핵심 모델 build contract는 건드리지 않아 v3.4.24에 바로 덮어쓸 수 있음

업로드:
ZIP을 풀고 저장소 루트 기준으로
`sports_ev_engine/national_policy.py`
`sports_ev_engine/analysis_view.py`
두 파일을 같은 경로에 덮어쓴 뒤 Commit changes → Streamlit Reboot.
