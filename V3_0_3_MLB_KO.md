# v3.0.3 MLB 완전자동 분석

기존 MLB 탭은 일정/예고선발 조회 전용이었다. v3.0.3부터 A매치와 같은 v3 FINAL Decision Layer로 연결한다.

## KST 날짜 수정
MLB Stats API의 `date=`는 한국 날짜가 아니다. 선택한 KST 날짜의 전날/당일/다음날 MLB 일정을 함께 조회한 뒤 실제 `gameDate` UTC를 Asia/Seoul로 변환해서 정확히 해당 KST 날짜만 남긴다.

## 자동 근거
실제로 반환된 경우에만 사용한다.
- 시즌 팀 득점/실점 및 승률
- 최근 6~12경기 득실/승률, 최근 구간 OPS
- 예고선발 시즌 ERA/WHIP/K-BB와 최근 최대 5경기 ERA/K%/BB%/K-BB%
- 최근 3경기 불펜 구원 이닝
- 상대 선발 좌/우에 대한 팀 OPS 스플릿
- 최근 3등판 FF/SI 구속 변화
- 구장 좌표 기반 날씨
- 확정 타순 1~9 및 라인업 단계
- The Odds API 시장 no-vig 확률, 베스트 배당, BE/Edge/EV
- 반증 경로와 27개 스트레스 시나리오

미수집 값은 MISSING으로 남기고 불확실성을 올린다.
