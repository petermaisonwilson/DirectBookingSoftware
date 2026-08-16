__version__ = "0.0.1"

# Build 008 compatibility repair: make annual year copying transactional/verified
# and add the safe Delete year control before any UI modules import AnnualConfigurationTab.
from .annual_config_repair import apply_annual_config_repair

apply_annual_config_repair()
