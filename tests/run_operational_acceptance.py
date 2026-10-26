"""Run unchanged business assertions against the selected operational backend."""
import os
from pathlib import Path
import subprocess
import sys

SCENARIOS = (
    'web_v1_foundation_test.py',
    'online_setup015_test.py',
    'web_v1_client_register_test.py',
    'web_v1_enquiry_search_test.py',
    'web_v1_operations_navigation_test.py',
    'web_v1_setup_maintenance_test.py',
    'web_v1_setup_year_audit_test.py',
    'web_v1_ordering_test.py',
    'web_v1_pricing_usability_test.py',
    'web_v1_addon_when_test.py',
    'web_v1_addon_person_pricing_test.py',
    'web_v1_addon_picker_test.py',
    'web_v1_hold_settings_test.py',
    'web_v1_availability_foundation_test.py',
    'web_v1_availability_calendar_test.py',
    'web_v1_booking_edit_hold_warning_test.py',
    'web_v1_full_enquiry_test.py',
    'web_v1_availability_enquiry_bridge_test.py',
    'web_v1_booking_workflow_test.py',
)


def main():
    failures = []
    for scenario in SCENARIOS:
        print('\n=== Operational acceptance:', scenario, '===', flush=True)
        result = subprocess.run([sys.executable, str(Path(__file__).parent / scenario)])
        if result.returncode:
            failures.append(scenario)
    if failures:
        raise SystemExit('Operational acceptance failed: ' + ', '.join(failures))
    print(f'All {len(SCENARIOS)} operational acceptance scenarios passed', flush=True)


if __name__ == '__main__':
    main()
