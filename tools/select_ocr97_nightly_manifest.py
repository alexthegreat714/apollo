"""
Select the OCR97 nightly manifest for tonight's run based on a 4-night rotation.

Rotation is date-driven: day_of_year % 4 maps to nights 1-4.
The original 3-doc baseline manifest is always available as fallback.

Outputs a single line: the absolute path to the manifest to use.
"""
from __future__ import annotations

import json
import sys
from datetime import date, timezone
from pathlib import Path

APOLLO_ROOT = Path(__file__).resolve().parents[1]
CONFIG_DIR = APOLLO_ROOT / "config"
NIGHT_DIR = CONFIG_DIR / "ocr97_night_manifests"
BASELINE = CONFIG_DIR / "ocr97_real_documents_manifest.json"

_ROTATION = [
    NIGHT_DIR / "night_1_irs_forms_gauntlet.json",
    NIGHT_DIR / "night_2_charts_annual_reports.json",
    NIGHT_DIR / "night_3_dense_text_multicolumn.json",
    NIGHT_DIR / "night_4_stress_mixed.json",
    NIGHT_DIR / "night_5_academic_twocolumn.json",
    NIGHT_DIR / "night_6_nist_tech_standards.json",
    NIGHT_DIR / "night_7_irs_long_publications.json",
    NIGHT_DIR / "night_8_statistical_data_releases.json",
    NIGHT_DIR / "night_9_international_chartdense.json",
    NIGHT_DIR / "night_10_legal_regulatory.json",
]


def select() -> Path:
    today = date.today()
    slot = today.timetuple().tm_yday % len(_ROTATION)
    chosen = _ROTATION[slot]
    if chosen.exists():
        return chosen
    # Fallback: try other night manifests in order
    for candidate in _ROTATION:
        if candidate.exists():
            return candidate
    return BASELINE


def main() -> None:
    chosen = select()
    today = date.today()
    slot = today.timetuple().tm_yday % len(_ROTATION)
    night_label = f"night_{slot + 1}"
    info = {
        "manifest": str(chosen),
        "night": night_label,
        "date": today.isoformat(),
        "day_of_year": today.timetuple().tm_yday,
    }
    # If --json flag passed, print JSON; otherwise just print the path.
    if "--json" in sys.argv:
        print(json.dumps(info, indent=2))
    else:
        print(str(chosen))


if __name__ == "__main__":
    main()
