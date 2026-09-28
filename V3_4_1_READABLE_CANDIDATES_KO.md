# Sports EV Engine v3.4.1 — Readable Reasons + Candidate Gate Split

## 1. 사람용 설명과 내부 감사코드 분리

FINAL 화면은 이제 다음을 별도로 표시합니다.

- 선정/판정 이유
- 주요 실패경로
- 데이터/모델 리스크
- 데이터 상태
- Model confidence

`최근 표본 6경기 + 불확실성 10.5pp + 신호 커버리지 17%`처럼 서로 성격이 다른 경고를 전부 "주요 실패경로"에 몰아 넣지 않습니다.

구체적인 전술/선수 근거가 없는 경우에는 실패경로를 만들어내지 않고, "정밀 신호 부족으로 특정 전술적 실패경로를 단정하기 어렵다"고 표시합니다.

기존 selection_reason / counter_case_summary / signal ledger는 삭제하지 않고 상세 감사 expander 안에 그대로 유지합니다.

## 2. `가정 변화 통과`와 `오늘의 베스트 조합`의 관계 수정

v3.4.0에서는 반증위험 HIGH, drift ALERT, 오래된 snapshot 등의 안전게이트에서 탈락한 픽을 `오늘의 베스트 조합` 후보표에서도 완전히 숨겼습니다.

이 때문에 개별 A매치 화면에서 `27/27 스트레스 +EV`로 표시된 픽도 베스트 조합 탭에서는 후보 0개처럼 보일 수 있었습니다.

v3.4.1에서는 두 단계를 분리합니다.

- `+EV 후보`: ROBUST/SENSITIVE이며 현재 모델 EV가 양수인 선택을 표시
- `조합 가능`: 위 후보 중 데이터 신선도/반증위험/drift/adaptive gate까지 통과한 선택

안전게이트에서 탈락한 픽은 `검토 후보`로 계속 보이고, `조합 제외 이유`를 함께 표시합니다.

예:

- Hungary 승 — +EV 후보 / 검토 후보
- 이유: `반증/데이터 위험 HIGH`
- 실제 2폴/3폴 생성에는 미사용

따라서 후보가 사라지는 것과 실제 조합에서 제외되는 것을 구분할 수 있습니다.

## 3. 조합 확률 계산

실제 1/2/3폴 생성기는 `daily_combo_eligible=True`인 행만 사용합니다.
검토 후보는 후보표에는 남지만 조합 확률/EV 계산에는 들어가지 않습니다.

## 4. 기존 모델 계산 유지

27개 stress scenario, calibration, ensemble, correlation, source health, paper lock, postgame 분석 등의 수치 모델은 변경하지 않았습니다. 이번 패치는 주로 후보 gate의 표현과 후보 visibility를 일치시키는 UI/선별 수정입니다.
