import importlib
import sys
import types


def _load_fix_with_stub():
    pkg = types.ModuleType("sports_ev_engine")
    pkg.__path__ = []
    safety = types.ModuleType("sports_ev_engine.kbo_safety_patch")

    def norm(v):
        import re
        return re.sub(r"[^0-9a-zA-Z가-힣ぁ-んァ-ヶ一-龯]+", "", str(v or "")).lower()

    def sig(h, a):
        return f"{norm(h)}|{norm(a)}"

    def original(official_home, official_away, naver_home, naver_away,
                 *, official_confirmed, naver_authoritative):
        return {
            "home_starter": official_home,
            "away_starter": official_away,
            "starter_confirmed": bool(official_confirmed),
            "starter_verified": bool(official_confirmed),
            "starter_source_conflict": bool(
                naver_authoritative and naver_home and
                norm(official_home) != norm(naver_home)
            ),
            "starter_override_applied": False,
            "starter_conflict_detail": "",
            "starter_source": "old",
            "starter_signature": sig(official_home, official_away),
        }

    safety._norm_name = norm
    safety.starter_signature = sig
    safety.reconcile_kbo_starters = original
    safety.PATCH_VERSION = "old"

    sys.modules["sports_ev_engine"] = pkg
    sys.modules["sports_ev_engine.kbo_safety_patch"] = safety

    spec = importlib.util.spec_from_file_location(
        "sports_ev_engine.kbo_starter_authority_fix",
        "sports_ev_engine/kbo_starter_authority_fix.py",
    )
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    mod.install()
    return safety


def test_full_confirmed_naver_overrides_stale_official():
    safety = _load_fix_with_stub()
    r = safety.reconcile_kbo_starters(
        "네일", "카라스코", "톨허스트", "올러",
        official_confirmed=True, naver_authoritative=True,
    )
    assert r["starter_confirmed"] is True
    assert r["starter_verified"] is True
    assert r["starter_source_conflict"] is False
    assert r["starter_override_applied"] is True
    assert r["starter_official_stale"] is True
    assert r["home_starter"] == "톨허스트"
    assert r["away_starter"] == "올러"


def test_partial_naver_does_not_bypass_old_safety():
    safety = _load_fix_with_stub()
    r = safety.reconcile_kbo_starters(
        "네일", "카라스코", "톨허스트", None,
        official_confirmed=True, naver_authoritative=True,
    )
    assert r["home_starter"] == "네일"
