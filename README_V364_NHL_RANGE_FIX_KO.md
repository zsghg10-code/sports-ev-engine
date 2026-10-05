# Sports EV Engine v3.6.4 NHL ESPN range fix

v3.6.3 적용 후에도 전부 DATA_HOLD가 뜨는 원인을 수정합니다.

- 원인: ESPN Site scoreboard에 `YYYYMMDD-YYYYMMDD` 범위를 직접 요청하는 방식이 현재 일부 팀 스포츠에서 실패/빈 응답이 될 수 있음.
- 수정: `YYYYMM` 월 단위 수집 + 로컬 기간 필터/중복제거. 월 요청 자체가 예외면 해당 월만 `YYYYMMDD` 일 단위 fallback.
- event_summary도 같은 합성 scoreboard를 사용하므로 부상/골리 조회가 range 문제로 같이 죽지 않음.
- `context_collection_errors` / `수집오류` 표시 추가.
- v3.6.3의 시즌초 prior-season 감쇠, PROVISIONAL/FINAL, NHL 2-way/3-way 시장 분리는 유지.

정상 기대값: 최근 경기 수집 성공 + 골리 미확정 => PROVISIONAL, 골리/컨텍스트 충족 => FINAL, 진짜 핵심 표본/가격 부재일 때만 DATA_HOLD.
