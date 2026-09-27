
from __future__ import annotations
import pandas as pd

from sports_ev_engine.models.soccer_auto import build_lambdas, score_matrix, price_from_matrix, norm_name
from sports_ev_engine.competition_form import team_recent_form_from_pool
from sports_ev_engine.core.ev import analyze_bet

def _side_for_row(r):
    home=norm_name(r["home_team"]); away=norm_name(r["away_team"]); sel=norm_name(r["selection"])
    if r["market"]=="h2h":
        if sel==home:return "home"
        if sel==away:return "away"
        if "draw" in sel:return "draw"
    elif r["market"]=="totals":
        return "over" if "over" in sel else "under"
    elif r["market"]=="spreads":
        if sel==home:return "home"
        if sel==away:return "away"
    return None

def analyze_event(event_rows, competition_pool, recent_n=6):
    first=event_rows.iloc[0]
    home=first["home_team"]; away=first["away_team"]
    cutoff=first.get("commence_time")
    fixtures=competition_pool.get("fixtures",[])

    hf=team_recent_form_from_pool(fixtures,home,cutoff,recent_n=recent_n)
    af=team_recent_form_from_pool(fixtures,away,cutoff,recent_n=recent_n)

    if not hf or not af:
        missing=[]
        if not hf: missing.append(home)
        if not af: missing.append(away)
        return pd.DataFrame(), {
            "status":"data_failed",
            "home":home,"away":away,
            "reason":"same-competition completed fixtures unavailable: " + ", ".join(missing)
        }

    hl,al=build_lambdas(hf,af)
    matrix=score_matrix(hl,al)

    sample=min(hf["matches"],af["matches"])
    base_unc=4.0 + (2.0 if sample<5 else 0.0) + (1.0 if sample<3 else 0.0)

    rows=[]
    for _,r in event_rows.iterrows():
        side=_side_for_row(r)
        if side is None:
            continue
        line=None
        if r["market"] in {"totals","spreads"}:
            if pd.isna(r.get("point")):
                continue
            line=float(r["point"])
        w,p,l=price_from_matrix(matrix,r["market"],side,line)
        ev=analyze_bet(float(r["best_odds"]),w,p,base_unc)
        display=f'{r["home_team"]}-{r["away_team"]} | {r["selection"]}'
        if line is not None:
            display += f' {line:+g}' if r["market"]=="spreads" else f' {line:g}'
        d=r.to_dict()
        d.update({
            "display_pick":display,
            "model_win_prob":w,
            "push_prob":p,
            "model_lose_prob":l,
            "home_lambda":hl,
            "away_lambda":al,
            "home_recent_gf":hf["gf"],
            "home_recent_ga":hf["ga"],
            "away_recent_gf":af["gf"],
            "away_recent_ga":af["ga"],
            "home_form_matches":hf["matches"],
            "away_form_matches":af["matches"],
            "uncertainty_pp":base_unc,
            "break_even":ev.break_even,
            "edge_pp":ev.edge_pp,
            "ev_roi":ev.ev_roi,
            "conservative_ev_roi":ev.conservative_ev_roi,
            "kelly_scaled":ev.kelly_scaled,
            "grade":ev.grade,
        })
        rows.append(d)

    return pd.DataFrame(rows), {
        "status":"ok","home":home,"away":away,
        "home_form":hf,"away_form":af,
        "home_lambda":hl,"away_lambda":al,
    }
