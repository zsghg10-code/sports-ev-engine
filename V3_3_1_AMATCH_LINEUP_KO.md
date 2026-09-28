# v3.3.1 A매치 라인업 수정

## 원인
1. 무료 기록 모드에서는 정밀 컨텍스트 체크가 켜져 있어도 API-Football 라인업 수집이 실행되지 않던 연결 오류가 있었습니다.
2. API-Football startXI가 양 팀 11명으로 들어와도 국제경기(`international=True`)에서는 `lineup_confirmed`로 승격하지 않던 조건 오류가 있었습니다.
3. 화면의 라인업 상태가 `event_context`가 아니라 `manual_context`만 보던 표시 오류가 있었습니다.

## 수정
- 무료 기록 + API-Football 정밀 컨텍스트 혼합 허용
- A매치도 API-Football 11+11 확인 시 CONFIRMED
- 포메이션/선발 명단 표시
- Probable Lineups는 확정으로 오인하지 않음
