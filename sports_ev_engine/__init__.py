__version__ = "3.5.0-nhl-nfl"

# v3.4.25 KBO live-safety hotfix remains installed unchanged.
from .kbo_safety_patch import install as _install_v3425_kbo_safety
_install_v3425_kbo_safety()
del _install_v3425_kbo_safety

# Compatibility with the existing v3.4.24 Streamlit entrypoint checks.
from .providers import baseball_advanced as _baseball_advanced
from .providers import live_baseball as _live_baseball

_baseball_advanced.KBO_SAFETY_BUILD = "3.4.25-kbo-safety"
_live_baseball.KBO_SAFETY_BUILD = "3.4.25-kbo-safety"
_baseball_advanced.BASEBALL_ADVANCED_BUILD = "3.4.23"
_live_baseball.LIVE_BASEBALL_BUILD = "3.4.20"

del _baseball_advanced, _live_baseball

# v3.5 upload-only NHL/NFL tab injection. No APPLY .bat/.py is needed.
from .v350_streamlit_hook import install as _install_v350_streamlit_hook
_install_v350_streamlit_hook()
del _install_v350_streamlit_hook
