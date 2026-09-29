# Sports EV Engine v3.4.15 — measured xG single-pass

## 이번 수정

- **실측 xG는 한 번만 반영**: 최근 득점/실점 기반 λ 위에 xG를 여러 단계로 누적하지 않고, 관측 득점률과 실측 xG를 하나의 latent scoring-rate 추정치로 단일 blend합니다.
- **`xg_form` + deep context 중복 차단**: CSV/과거기록 xG와 API-Football/FotMob/SofaScore xG가 동시에 있을 때 deep context의 완전한 4개 값만 우선 사용합니다. deep xG가 완전하지 않을 때만 검증된 `xg_form` 전체 세트를 fallback으로 사용하며 부분 값끼리 섞지 않습니다.
- **3경기 실측 xG 가중치**: A매치 45%, 5경기 이상 50% (클럽은 더 보수적). 극단적 오매칭 방지를 위해 각 팀 λ 이동은 base 대비 ±35% 범위에서 제한합니다. 이 계수는 calibration/backtest 대상으로 기록됩니다.
- **배포 혼합 방지**: app.py, `auto_national`, `deep_soccer_context`, `national_context_pipeline`, `auto_soccer`, `reasoning_engine`, `free_national`의 PATCH_BUILD가 모두 `3.4.15-xg-single-pass`인지 시작 시 확인합니다. 하나라도 구버전이면 분석하지 않고 재배포를 요구합니다.
- **xG 감사 필드 저장**: canonical source, application mode, blend weight, xG target λ를 prediction snapshot에 남깁니다.
- deep mode에서 `xG 상태=NOT_CHECKED`는 더 이상 정상 종결 상태가 아닙니다. 발생 시 `ERROR` invariant로 바꿔 자동후보를 차단합니다.

## 배포

GitHub 저장소 루트에 DEPLOY_ONLY ZIP의 내용 전체를 덮어쓰고 Commit 후 Streamlit Reboot를 하세요. 화면 상단이 반드시 `Sports EV Engine v3.4.15` / `BUILD v3.4.15-xg-single-pass`여야 합니다.
