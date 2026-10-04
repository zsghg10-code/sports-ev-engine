__version__ = "3.4.25-kbo-safety"

# v3.4.25 is a targeted live-safety hotfix layered on the v3.4.24 models.
# Import-time installation is network-free: it only wraps local functions.
from .kbo_safety_patch import install as _install_v3425_kbo_safety
_install_v3425_kbo_safety()
del _install_v3425_kbo_safety
