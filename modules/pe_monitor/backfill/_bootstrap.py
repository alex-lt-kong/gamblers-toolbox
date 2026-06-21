"""Put pe_monitor/ on sys.path so these relocated backfill tools can import
the app's config/storage/fetcher modules. Import this before importing them."""

import sys
from pathlib import Path

_PE_DIR = Path(__file__).resolve().parent.parent
if str(_PE_DIR) not in sys.path:
    sys.path.insert(0, str(_PE_DIR))
