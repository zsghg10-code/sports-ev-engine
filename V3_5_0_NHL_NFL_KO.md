# Sports EV Engine v3.5.0 — NHL/NFL 자동분석

기준 버전: **v3.4.25-kbo-safety**

## 추가 기능
- `🏒 NHL 자동분석`, `🏈 NFL 자동분석`
- The Odds API h2h / spreads / totals 자동 수집 및 무마진 컨센서스
- ESPN 공개 경기 피드의 최근 경기·득실·휴식일 factual context
- NFL: shrink된 최근 득실 → 예상 득점/마진/토탈 분포
- NHL: Poisson score matrix → ML/퍽라인/토탈 확률
- QB/선발 골리/부상 미검증 시 MISSING 처리 및 불확실성 페널티
- 반증 경로 / 모델-시장 충돌 / 스트레스 시나리오
- adaptive calibration + 최종 확률 기준 EV/ROBUST 재계산
- FINAL Decision Layer: 추정확률 | BE | Edge | EV | 불확실성 | 상태
- 2~6폴, 불변 예측 스냅샷, CLV, 자동 정산, 모델 검증
- `오늘의 베스트 조합`에 NHL/NFL 통합

## 기존 기능 보존
v3.4.25 KBO safety hotfix, MLB totals calibration, A매치 validation 및 기존 축구/야구 기능은 유지합니다.

## 적용
1. ZIP 내용을 **v3.4.25 저장소 루트**에 풉니다.
2. Windows: `APPLY_V350.bat`, 그 외: `python APPLY_V350_PATCH.py`
3. `python -m pytest -q tests/test_v350_nhl_nfl.py`
4. 변경/추가 파일을 GitHub에 커밋 후 Streamlit Reboot.

## 원칙
공개 소스에서 확인되지 않은 QB·선발 골리·부상 영향은 시장배당에서 역추정하지 않습니다. MISSING으로 표시하고 불확실성 및 다폴 게이트에 반영합니다. NFL/NHL 자체 정산 표본이 쌓이면 기존 generic calibration이 자동 활성화됩니다.
