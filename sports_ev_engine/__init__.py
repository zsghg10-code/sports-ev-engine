__version__ = "3.6.1-autolearn-kbo-starter-fix"

# Preserve the v3.4.25 KBO safety hotfix.
from .kbo_safety_patch import install as _install_v3425_kbo_safety
_install_v3425_kbo_safety()
del _install_v3425_kbo_safety

# v3.6.1: a complete, game-matched Naver 1-9 lineup with both displayed
# starters can override stale KBO GameCenter starter fields instead of leaving
# every market permanently in STARTER CONFLICT / REVIEW.
from .kbo_starter_authority_fix import install as _install_kbo_starter_authority
_install_kbo_starter_authority()
del _install_kbo_starter_authority

from .providers import baseball_advanced as _baseball_advanced
from .providers import live_baseball as _live_baseball
_baseball_advanced.KBO_SAFETY_BUILD = "3.6.1-kbo-starter-authority"
_live_baseball.KBO_SAFETY_BUILD = "3.6.1-kbo-starter-authority"
_baseball_advanced.BASEBALL_ADVANCED_BUILD = "3.4.23"
_live_baseball.LIVE_BASEBALL_BUILD = "3.4.20"
del _baseball_advanced, _live_baseball

# All-sport review must be installed before app.py imports mlb_postgame helpers.
from .universal_postgame import install as _install_universal_postgame
_install_universal_postgame()
del _install_universal_postgame

# Conservative online probability learning on top of the existing adaptive layer.
from .self_learning import install as _install_self_learning
_install_self_learning()
del _install_self_learning

# Mark new immutable snapshots with the new model version.
from . import prediction_store as _prediction_store
_prediction_store.MODEL_VERSION = __version__
del _prediction_store

# Upload-only NHL/NFL/tab-order/model-validation integration.
from .v350_streamlit_hook import install as _install_v360_streamlit_hook
_install_v360_streamlit_hook()
del _install_v360_streamlit_hook
