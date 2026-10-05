"""Cross-sport post-game review for Sports EV Engine v3.6.

MLB keeps its detailed inning/starter/bullpen reviewer. Every other settled sport
gets a conservative score/line/CLV based review. The generic reviewer never calls
a losing pick GOOD PICK merely because pregame EV was positive.
"""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import json
import math

from .prediction_store import load_settled
from . import persistent_store
from .providers import mlb_postgame as _mlb

_ORIGINAL_MLB_ANALYZE = _mlb.analyze_settled_mlb
_ORIGINAL_REVIEWS = _mlb.postgame_reviews
_REVIEW_PATH = "data/postgame_reviews.jsonl"
_INSTALLED = False


def _num(v, default=None):
    try:
        x = float(v)
        return x if math.isfinite(x) else default
    except (TypeError, ValueError):
        return default


def _load_local(path=_REVIEW_PATH):
    p = Path(path)
    if not p.exists():
        return []
    out = []
    with p.open(encoding="utf-8") as f:
        for line in f:
            try:
                row = json.loads(line)
                if isinstance(row, dict):
                    out.append(row)
            except Exception:
                pass
    return out


def _append(path, rows):
    if not rows:
        return
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    with p.open("a", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False, sort_keys=True, default=str) + "\n")
    persistent_store.mirror("postgame_review", rows, id_field="fingerprint")


def _family(snapshot):
    fam = str(snapshot.get("sport_family") or "")
    if fam:
        return fam
    sk = str(snapshot.get("sport_key") or "")
    if sk == "baseball_mlb":
        return "baseball_mlb"
    if sk == "baseball_kbo":
        return "baseball_kbo"
    if sk == "baseball_npb":
        return "baseball_npb"
    if sk == "americanfootball_nfl":
        return "football_nfl"
    if sk == "icehockey_nhl":
        return "hockey_nhl"
    if sk.startswith("soccer_"):
        return "soccer_national" if "uefa_nations" in sk or "world_cup" in sk else "soccer_club"
    return sk or "unknown"


def _tolerance(family, market):
    if family.startswith("soccer"):
        return 0.55 if market in {"totals", "spreads"} else 1.0
    if family == "hockey_nhl":
        return 1.05
    if family == "football_nfl":
        return 3.25 if market in {"totals", "spreads"} else 8.25
    if family.startswith("baseball"):
        return 1.05 if market in {"totals", "spreads"} else 2.0
    return 1.0


def _side(snapshot):
    sel = str(snapshot.get("selection") or "")
    if sel == str(snapshot.get("home_team") or ""):
        return "home"
    if sel == str(snapshot.get("away_team") or ""):
        return "away"
    if "draw" in sel.lower() or sel.strip().lower() in {"x", "tie"}:
        return "draw"
    return None


def _result_label(snapshot):
    w = _num(snapshot.get("settle_win"), 0.0) or 0.0
    p = _num(snapshot.get("settle_push"), 0.0) or 0.0
    l = _num(snapshot.get("settle_loss"), 0.0) or 0.0
    if w > l and w > 0:
        return "WIN"
    if p > 0 and l == 0 and w == 0:
        return "PUSH"
    if w > 0 and p > 0 and l == 0:
        return "PARTIAL_WIN"
    if l > 0 and p > 0:
        return "PARTIAL_LOSS"
    return "LOSS"


def classify_generic(snapshot):
    family = _family(snapshot)
    market = str(snapshot.get("market") or "")
    result = _result_label(snapshot)
    hs = _num(snapshot.get("home_score"), 0.0) or 0.0
    aws = _num(snapshot.get("away_score"), 0.0) or 0.0
    point = _num(snapshot.get("point"))
    model_p = _num(snapshot.get("model_win_prob"))
    clv = _num(snapshot.get("clv_market_prob_pp"))
    side = _side(snapshot)
    tol = _tolerance(family, market)

    base = {
        "postgame_class": "REVIEW",
        "postgame_class_ko": "추가 검토",
        "postgame_reason": "결과만으로 사전 분석의 품질을 단정하지 않음",
        "postgame_quality": "REVIEW",
        "tail_event": False,
        "model_miss_candidate": False,
    }

    if result == "PUSH":
        return {**base, "postgame_class": "PUSH", "postgame_class_ko": "적특/푸시",
                "postgame_reason": "정산 결과가 푸시라 방향성 학습에서는 제외", "postgame_quality": "NEUTRAL"}
    if result == "PARTIAL_WIN":
        return {**base, "postgame_class": "PARTIAL_WIN", "postgame_class_ko": "부분 적중",
                "postgame_reason": "아시안 라인 부분 적중", "postgame_quality": "CONFIRMED"}
    if result == "PARTIAL_LOSS":
        return {**base, "postgame_class": "PARTIAL_LOSS", "postgame_class_ko": "부분 미적중",
                "postgame_reason": "아시안 라인 부분 미적중 · Bernoulli calibration에서는 제외", "postgame_quality": "NEUTRAL"}

    if result == "WIN":
        clv_note = ""
        if clv is not None:
            clv_note = f" · CLV {clv:+.2f}%p"
        return {
            **base,
            "postgame_class": "MODEL_CONFIRMATION",
            "postgame_class_ko": "사전 방향 확인",
            "postgame_reason": f"사전 확률 방향과 실제 결과가 일치{clv_note}",
            "postgame_quality": "CONFIRMED",
        }

    miss_distance = None
    context = ""
    if market == "h2h":
        if side == "home":
            margin = hs - aws
        elif side == "away":
            margin = aws - hs
        elif side == "draw":
            margin = -abs(hs - aws)
        else:
            margin = None
        if margin is not None:
            miss_distance = abs(min(0.0, margin))
            context = f"최종점수 {aws:g}:{hs:g} · 선택 방향 마진 {margin:+.1f}"
    elif market == "spreads" and point is not None:
        if side == "home":
            cover = hs + point - aws
        elif side == "away":
            cover = aws + point - hs
        else:
            cover = None
        if cover is not None:
            miss_distance = abs(min(0.0, cover))
            context = f"최종점수 {aws:g}:{hs:g} · 핸디 적용 마진 {cover:+.1f}"
    elif market == "totals" and point is not None:
        total = hs + aws
        over = "over" in str(snapshot.get("selection") or "").lower()
        signed = total - point if over else point - total
        miss_distance = abs(min(0.0, signed))
        context = f"최종 합계 {total:g} · 기준 {point:g} · 방향 마진 {signed:+.1f}"

    positive_clv = clv is not None and clv >= 1.0
    negative_clv = clv is not None and clv <= -1.0
    close = miss_distance is not None and miss_distance <= tol

    if close and positive_clv:
        return {
            **base,
            "postgame_class": "GOOD_PRICE_CLOSE_LOSS",
            "postgame_class_ko": "좋은 가격 + 접전 미적중",
            "postgame_reason": f"{context} · 허용 오차 {tol:g} 이내 · 마감시장도 픽 방향으로 {clv:+.2f}%p 이동",
            "postgame_quality": "GOOD_PRICE_VARIANCE",
        }
    if close:
        return {
            **base,
            "postgame_class": "CLOSE_LOSS_REVIEW",
            "postgame_class_ko": "접전/라인 근처 미적중",
            "postgame_reason": f"{context} · 기준선에서 작은 차이로 미적중",
            "postgame_quality": "VARIANCE_OR_THIN_EDGE",
        }
    if negative_clv and miss_distance is not None:
        return {
            **base,
            "postgame_class": "MODEL_MISS_WITH_NEGATIVE_CLV",
            "postgame_class_ko": "모델 미스 후보 + 역CLV",
            "postgame_reason": f"{context} · 마감시장이 반대 방향으로 {abs(clv):.2f}%p 이동",
            "postgame_quality": "MODEL_MISS_CANDIDATE",
            "model_miss_candidate": True,
        }
    if miss_distance is not None and miss_distance > tol * 2:
        ptxt = f" · 사전확률 {model_p*100:.1f}%" if model_p is not None else ""
        return {
            **base,
            "postgame_class": "MODEL_DIRECTION_MISS",
            "postgame_class_ko": "사전 방향성 실패 후보",
            "postgame_reason": f"{context} · 기준선에서 충분히 멀리 이탈{ptxt}",
            "postgame_quality": "MODEL_MISS_CANDIDATE",
            "model_miss_candidate": True,
        }
    return {**base, "postgame_reason": context or base["postgame_reason"]}


def build_generic_review(snapshot, classification):
    family = _family(snapshot)
    return {
        "fingerprint": snapshot.get("fingerprint"),
        "event_id": snapshot.get("event_id"),
        "sport_key": snapshot.get("sport_key"),
        "sport_family": family,
        "commence_time": snapshot.get("commence_time"),
        "home_team": snapshot.get("home_team"),
        "away_team": snapshot.get("away_team"),
        "market": snapshot.get("market"),
        "selection": snapshot.get("selection"),
        "point": snapshot.get("point"),
        "best_odds": snapshot.get("best_odds"),
        "consensus_prob": snapshot.get("consensus_prob"),
        "model_win_prob": snapshot.get("model_win_prob"),
        "break_even": snapshot.get("break_even"),
        "edge_pp": snapshot.get("edge_pp"),
        "ev_roi": snapshot.get("ev_roi"),
        "v3_decision_status": snapshot.get("v3_decision_status"),
        "robust_positive_ratio": snapshot.get("robust_positive_ratio"),
        "closing_odds": snapshot.get("closing_odds"),
        "closing_consensus_prob": snapshot.get("closing_consensus_prob"),
        "closing_point": snapshot.get("closing_point"),
        "clv_market_prob_pp": snapshot.get("clv_market_prob_pp"),
        "clv_line_points": snapshot.get("clv_line_points"),
        "home_score": snapshot.get("home_score"),
        "away_score": snapshot.get("away_score"),
        "settle_win": snapshot.get("settle_win"),
        "settle_push": snapshot.get("settle_push"),
        "settle_loss": snapshot.get("settle_loss"),
        **classification,
        "reviewed_at": datetime.now(timezone.utc).isoformat(),
        "review_engine": "universal-postgame-v3.6",
    }


def analyze_settled_all(settled_path="data/settled_predictions.jsonl",
                        review_path=_REVIEW_PATH, provider=None):
    # Preserve MLB's richer pitch/inning reviewer first.
    mlb_info = _ORIGINAL_MLB_ANALYZE(
        settled_path=settled_path, review_path=review_path, provider=provider
    )
    existing = _ORIGINAL_REVIEWS(review_path)
    done = {str(x.get("fingerprint") or "") for x in existing if x.get("fingerprint")}
    rows = []
    errors = list(mlb_info.get("errors") or [])
    for s in load_settled(settled_path):
        if _family(s) == "baseball_mlb":
            continue
        fp = str(s.get("fingerprint") or "")
        if not fp or fp in done:
            continue
        try:
            cls = classify_generic(s)
            rows.append(build_generic_review(s, cls))
            done.add(fp)
        except Exception as exc:
            errors.append(
                f"{s.get('sport_key')}/{s.get('home_team')}-{s.get('away_team')}: "
                f"{type(exc).__name__}: {exc}"
            )
    _append(review_path, rows)
    return {
        "reviewed": int(mlb_info.get("reviewed") or 0) + len(rows),
        "mlb_reviewed": int(mlb_info.get("reviewed") or 0),
        "other_reviewed": len(rows),
        "errors": errors,
    }


def postgame_reviews_all(path=_REVIEW_PATH):
    rows = _ORIGINAL_REVIEWS(path)
    out = []
    for r0 in rows:
        r = dict(r0)
        fam = _family(r)
        r.setdefault("sport_family", fam)
        # Existing v3.4 UI calls every "spreads" row a baseball run line.
        # Return a display-safe value for non-baseball reviews only.
        if not fam.startswith("baseball") and r.get("market") == "spreads":
            r["source_market"] = "spreads"
            r["market"] = "스프레드"
        out.append(r)
    return out


def install():
    global _INSTALLED
    if _INSTALLED:
        return
    _INSTALLED = True
    _mlb.analyze_settled_mlb = analyze_settled_all
    _mlb.postgame_reviews = postgame_reviews_all
