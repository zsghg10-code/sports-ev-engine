
import itertools, math

def optimize_parlays(df, sizes=(2,3,4,5,6), top_n=10):
    results={}
    if "v3_parlay_eligible" in df.columns:
        usable=df[df["v3_parlay_eligible"].fillna(False).astype(bool)].copy()
    else:
        usable=df[
            (df["grade"].isin(["A","B","C"])) &
            (df["conservative_ev_roi"]>0) &
            (~df["sanity"].isin(["OUTLIER_SHRUNK","HIGH_DISAGREEMENT"]))
        ].copy()

    records=usable.to_dict("records")
    for n in sizes:
        rows=[]
        for combo in itertools.combinations(records,n):
            if len({x["event_id"] for x in combo})<n:
                continue
            odds=math.prod(float(x["best_odds"]) for x in combo)
            hit=math.prod(float(x["model_win_prob"]) for x in combo)

            # conservative haircut for same competition and model uncertainty
            if len({x.get("sport_key","") for x in combo})==1 and n>=4:
                hit*=0.98**(n-3)

            avg_unc=sum(float(x["uncertainty_pp"]) for x in combo)/n
            high_dis=sum(1 for x in combo if x.get("sanity")=="HIGH_DISAGREEMENT")
            hit*=0.985**high_dis

            # v3 favours compact combinations: every extra leg after 2 carries a
            # small survival/correlation penalty even when each leg is individually robust.
            if n>2: hit*=0.985**(n-2)
            ev=hit*odds-1
            score=ev+0.20*hit-0.005*avg_unc-0.012*max(0,n-2)
            rows.append({
                "조합":" + ".join(x["display_pick"] for x in combo),
                "배당":odds,
                "근사 적중확률":hit,
                "근사 EV":ev,
                "고괴리 픽 수":high_dis,
                "점수":score,
            })
        rows.sort(key=lambda x:x["점수"],reverse=True)
        results[n]=rows[:top_n]
    return results
