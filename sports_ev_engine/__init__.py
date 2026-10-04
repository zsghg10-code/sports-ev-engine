__version__ = "3.4.25-kbo-safety"

# v3.4.25 is a targeted live-safety hotfix layered on the v3.4.24 models.
# Install the local monkey patches first.
from .kbo_safety_patch import install as _install_v3425_kbo_safety
_install_v3425_kbo_safety()
del _install_v3425_kbo_safety

# Compatibility with the existing v3.4.24 Streamlit entrypoint.
# The safety patch wraps these providers but must not overwrite their base-build
# identifiers, because app.py intentionally verifies those base modules.
from .providers import baseball_advanced as _baseball_advanced
from .providers import live_baseball as _live_baseball

_baseball_advanced.KBO_SAFETY_BUILD = __version__
_live_baseball.KBO_SAFETY_BUILD = __version__

_baseball_advanced.BASEBALL_ADVANCED_BUILD = "3.4.23"
_live_baseball.LIVE_BASEBALL_BUILD = "3.4.20"

del _baseball_advanced, _live_baseball
