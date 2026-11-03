"""The adversarial suite reuses the API unit tests' world builders (`World`, `signed_in`, ...)."""

import sys
from pathlib import Path

UNIT_API = Path(__file__).resolve().parents[1] / "unit" / "api"
if str(UNIT_API) not in sys.path:
    sys.path.insert(0, str(UNIT_API))
