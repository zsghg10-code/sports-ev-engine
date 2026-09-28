# v3.2.0 — 영구 DB · CLV · 스마트 자동재분석

## 1) 영구 DB

기본 JSONL 저장을 유지하면서, Supabase가 설정되면 같은 레코드를 DB에도 이중 저장합니다.

Supabase SQL Editor에서 `supabase_schema.sql`을 한 번 실행한 뒤 Streamlit Secrets에:

```toml
SUPABASE_URL = "https://YOUR_PROJECT.supabase.co"
SUPABASE_SERVICE_ROLE_KEY = "..."
```

을 추가합니다. 서비스 역할 키는 소스 코드/GitHub에 넣지 않습니다.

저장 종류:
- prediction: 경기 전 immutable 예측
- market_observation: CLV/시장 이동용 배당 관측
- settled: 실제 결과 정산
- postgame_review: MLB 자동 사후복기
- refresh_event: 자동 재분석 원인 로그

DB 연결이 없거나 일시 실패해도 로컬 JSONL 저장은 계속됩니다.

## 2) CLV

분석 당시 best odds / consensus probability를 저장하고, smart worker가 킥오프 전 시장을 반복 관측합니다.
경기 정산 시 가장 늦은 pregame 관측값을 closing line으로 붙입니다.

- 같은 라인: closing odds, implied probability CLV, consensus probability CLV
- 라인이 달라짐: line CLV만 별도 저장

서로 다른 핸디/토탈 라인의 배당값을 직접 비교하지 않습니다.

## 3) 스마트 자동재분석

`smart_worker.py`는 저장된 예정 경기만 감시합니다.

시장 trigger:
- 2시간 초과: 2.0%p
- 30~120분: 1.5%p
- 마지막 30분: 1.0%p
- 4.0%p 이상: 강한 trigger
- handicap/total line 0.25 이상: trigger

라인업/선발:
- 킥오프 150분 이내 15분 간격 context check
- starter/lineup/availability signature 변경 시 재분석

재분석은 별도의 새 모델이 아니라 기존 탭의 모델 함수를 다시 호출합니다.

지원:
- 클럽축구: API-Football 연결 시 competition/deep context 재분석
- A매치: API-Football 연결 시 deep context, 미연결 시 공개 A매치 source로 재수집
- KBO/NPB: 공식 starter/lineup + advanced signals 재분석
- MLB: MLB Stats API + Savant deep context 재분석

## 4) 자동 정산

smart worker 각 cycle 끝에 The Odds API score 정산을 실행합니다.
MLB는 정산 뒤 postgame review도 자동 실행합니다.

## 5) 실행

계속 실행:

```bash
python smart_worker.py
```

한 번 실행:

```bash
python smart_once.py
```

기본 interval은 300초(5분)입니다.

```env
AUTO_REFRESH_REGION=eu
SMART_WORKER_INTERVAL_SECONDS=300
```
