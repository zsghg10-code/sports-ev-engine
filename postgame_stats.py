"""Aggregate objective post-game failure classes into model-improvement hints."""
from __future__ import annotations
from collections import Counter,defaultdict
from .providers.mlb_postgame import postgame_reviews


def failure_statistics(reviews=None,min_flag_n=5):
    reviews=list(reviews if reviews is not None else postgame_reviews())
    latest={}
    for r in reviews:
        try: point=round(float(r.get("point")),4)
        except (TypeError,ValueError): point=None
        ident=(r.get("event_id"),r.get("market"),r.get("selection"),point)
        if ident not in latest or str(r.get("reviewed_at") or "")>str(latest[ident].get("reviewed_at") or ""):
            latest[ident]=r
    losses=[r for r in latest.values() if float(r.get("settle_loss") or 0)>0]
    groups=defaultdict(list)
    for r in losses: groups[(str(r.get("market") or ""),str(r.get("postgame_class_ko") or "REVIEW"))].append(r)
    rows=[]
    market_tot=Counter(str(r.get("market") or "") for r in losses)
    for (m,c),arr in groups.items():
        n=len(arr); total=market_tot[m]
        rows.append({"market":m,"cause":c,"n":n,"market_losses":total,"share":n/max(1,total)})
    rows.sort(key=lambda x:(x["market"],-x["n"]))
    hints=[]
    cause_tot=Counter(str(r.get("postgame_class_ko") or "REVIEW") for r in losses)
    for cause,n in cause_tot.most_common():
        share=n/max(1,len(losses))
        if n>=min_flag_n and share>=.20:
            if "불펜" in cause or "후반" in cause:
                hint="불펜 exact workload/후반 tail 가중치와 언더 robust gate 재검토"
            elif "선발" in cause:
                hint="선발 recent Stuff/workload/조기강판 위험 가중치 재검토"
            elif "방향성" in cause:
                hint="해당 마켓 독립모델·calibration 및 total/run environment 재검토"
            else:
                hint="반복 실패유형 — 관련 feature 가중치/데이터 커버리지 검토"
            hints.append({"cause":cause,"n":n,"share":share,"suggestion":hint})
    return {"losses":len(losses),"rows":rows,"hints":hints}
