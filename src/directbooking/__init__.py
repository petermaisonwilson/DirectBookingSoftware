__version__ = "0.0.1"

# Build 008 compatibility repair: make annual year copying transactional/verified
# and add the safe Delete year control before any UI modules import AnnualConfigurationTab.
from .annual_config_repair import apply_annual_config_repair

apply_annual_config_repair()

# Build 009 annual-grid safety: required blanks are highlighted and block Save;
# explicit zeroes remain valid but receive a gentle review highlight.
from .annual_grid_safety import apply_annual_grid_safety

apply_annual_grid_safety()
