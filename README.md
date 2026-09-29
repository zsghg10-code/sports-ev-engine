# Sports EV Engine v3.4.12

최신 변경: `V3_4_12_XG_FAILSOFT_KO.md`

# Sports EV Engine v3.4.10 — NPB official starter fallback + clearer lineup labels

- NPB announcement page가 다음날로 넘어간 뒤에도 월간 공식 일정의 `先発:` 이름을 선발 확정 근거로 사용합니다.
- 선수 profile/id 해석이 실패해도 공식 일정에 이름이 있으면 선발 자체를 버리지 않습니다.
- UI의 `선발 오더`를 `타순 1~9 확정`으로 변경해 선발투수와 타순을 구분합니다.
- `선발확인` 컬럼은 `선발투수 확정`으로 표시합니다.
- v3.4.9 KBO Naver lineup fallback 및 이전 기능은 그대로 유지합니다.

---

# Sports EV Engine v3.4.9 — KBO Naver lineup fallback

KBO batting-order collection is now fail-soft: KBO official GameCenter remains primary; when it has not returned a complete lineup, the engine checks Naver Sports public preview and promotes the lineup only when both teams have complete batting orders 1–9. v3.4.8 match-safety guards remain active.

# Sports EV Engine v3.4.7 — NPB starter section fix

- NPB announced-starter parsing now continues across Central/Pacific League sub-headings under the same date.
- Fixes cases where lineups were confirmed but Pacific League starters displayed as missing.
- Schedule-detail fallback remains enabled.
- All v3.4.6 A-match discovery and prior features are retained.

# Sports EV Engine v3.4.6 — All A-match Multi-source Discovery

- `🏆 오늘의 베스트 조합`은 기준 +EV 전체를 숨기지 않고 `검토 후보 / 단일 후보 / 조합 가능`으로 분리합니다. 실제 2~3폴 게이트는 기존보다 느슨하게 하지 않습니다.
- A매치 xG는 `API-Football → ESPN → FotMob` 실제 측정 xG cascade를 사용합니다. 각 팀 최근 3경기 표본이 완성된 경우에만 모델에 반영하고, 1~2경기는 부분수집으로만 표시합니다.
- 자세한 내용: `V3_4_5_WIDE_CANDIDATES_XG_KO.md`.

# Sports EV Engine v3.4.4 — Baseball Diagnostics Visibility

v3.4.4 keeps v3.4.3 xG fallback and fixes a KBO/NPB UI inconsistency:

- Base-positive EV rows are always shown in the detailed baseball diagnostics, even when REVIEW or lineup-not-final gates block candidacy.
- The UI separates `조합 가능`, `단일 +EV 후보`, and `검토 후보`.
- Each hidden-gate reason is shown explicitly (model-market disagreement, STARTER CONFIRMED, high counter-case risk, conservative EV <= 0, ensemble conflict, etc.).
- Empty-state copy no longer claims there is no positive EV when positive-EV REVIEW rows exist.

v3.4.2의 경기별 설명/후보 게이트 구조를 유지하면서 A매치 xG 수집을 보강했습니다.

- API-Football 최근 팀 경기에서 xG/xGA 직접 수집
- 무료 A매치 기록 모드에서도 모델링 pool의 fixture id 유무와 무관하게 xG 조회
- API-Football xG 불완전 시 ESPN 공개 match-summary Expected Goals fallback
- 양 팀 각각 최소 3개 실측 xG 표본이 없으면 MISSING
- xG 소스/fallback/표본 수를 UI 및 저장 스냅샷에 기록
- 추정 xG 생성 금지

자세한 내용: `V3_4_3_XG_FALLBACK_KO.md`
## v3.4.6 — All senior internationals, dynamic multi-source discovery

The A-match tab no longer depends only on a static The Odds API competition list. `전체 A매치 · 다중소스 자동발견` discovers senior national-team fixtures by KST date from API-Football and merges prices with this priority: The Odds API active international keys first, API-Football pre-match odds second. Fixtures without real prices remain visible as schedule-active / odds-missing and are not assigned synthetic EVs. See `V3_4_6_ALL_AMATCH_KO.md`.

## v3.4.8 — KBO match safety
- KBO same-day doubleheader matching now uses official `G_TM` against KST kickoff.
- Reversed provider home/away is explicitly detected and starter/lineup fields are remapped.
- KBO confirmation flags are normalized safely and lineup `G_ID` is sanity-checked before FINAL promotion.
