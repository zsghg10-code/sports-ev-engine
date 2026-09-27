
import itertools, math

def optimize_parlays(df, sizes=(2,3,4,5,6), top_n=10):
    results = {}
    usable = df[(df["grade"].isin(["A","B","C"])) & (df["conservative_ev_roi"] > 0)].copy()
    records = usable.to_dict("records")
    for n in sizes:
        rows = []
        for combo in itertools.combinations(records, n):
            if len({x["event_id"] for x in combo}) < n:
                continue
            odds = math.prod(float(x["best_odds"]) for x in combo)
            hit = math.prod(float(x["model_win_prob"]) for x in combo)
            # cross-event correlation haircut for same competition
            same_comp = len({x.get("sport_key","") for x in combo}) == 1
            if same_comp and n >= 4:
                hit *= 0.98 ** (n-3)
            ev = hit*odds - 1
            avg_unc = sum(float(x["uncertainty_pp"]) for x in combo)/n
            score = ev + 0.20*hit - 0.005*avg_unc
            rows.append({
                "조합": " + ".join(x["display_pick"] for x in combo),
                "배당": odds,
                "근사 적중확률": hit,
                "근사 EV": ev,
                "점수": score,
            })
        rows.sort(key=lambda x: x["점수"], reverse=True)
        results[n] = rows[:top_n]
    return results
