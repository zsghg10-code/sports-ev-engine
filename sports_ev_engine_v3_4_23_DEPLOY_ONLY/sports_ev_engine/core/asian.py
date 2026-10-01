
def _split(line):
    q = round(float(line)*4)
    if abs(float(line)*4-q) > 1e-9:
        raise ValueError("Asian line must be in 0.25 increments")
    if q % 2 == 1:
        return (q-1)/4, (q+1)/4
    return None

def settle_total_under(goals, line):
    sp = _split(line)
    if sp:
        a = settle_total_under(goals, sp[0])
        b = settle_total_under(goals, sp[1])
        return tuple((x+y)/2 for x,y in zip(a,b))
    if abs(line-round(line)) < 1e-9:
        n = int(round(line))
        if goals < n: return 1.,0.,0.
        if goals == n: return 0.,1.,0.
        return 0.,0.,1.
    return (1.,0.,0.) if goals < line else (0.,0.,1.)

def settle_total_over(goals, line):
    w,p,l = settle_total_under(goals,line)
    return l,p,w

def settle_home_handicap(hg, ag, line):
    sp = _split(line)
    if sp:
        a = settle_home_handicap(hg,ag,sp[0])
        b = settle_home_handicap(hg,ag,sp[1])
        return tuple((x+y)/2 for x,y in zip(a,b))
    score = hg-ag+line
    if score > 0: return 1.,0.,0.
    if abs(score) < 1e-12: return 0.,1.,0.
    return 0.,0.,1.
