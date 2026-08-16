__version__ = "0.0.1"

# Build 008 compatibility repair: make annual year copying transactional/verified
# and add the safe Delete year control before any UI modules import AnnualConfigurationTab.
from .annual_config_repair import apply_annual_config_repair

apply_annual_config_repair()

# Build 009 annual-grid safety: required blanks are highlighted and block Save;
# explicit zeroes remain valid but receive a gentle review highlight.
from .annual_grid_safety import apply_annual_grid_safety

apply_annual_grid_safety()

# Build 010/011 Add-on integration: Element Type defaults with individual Element
# overrides are copied/deleted alongside the established annual setup.
from .addon_integration import apply_addon_year_integration

apply_addon_year_integration()

from .build010_ui import apply_build010_labels

apply_build010_labels()

from .build011_ui import apply_build011_labels

apply_build011_labels()
