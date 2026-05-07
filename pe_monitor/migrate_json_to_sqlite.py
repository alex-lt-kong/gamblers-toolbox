"""One-shot: import existing pe_history/*.json files into the SQLite DB.

Safe to re-run — additive merge means existing DB rows are preserved. After a
successful run, the legacy pe_history/ directory can be removed manually.
"""

import json
from pathlib import Path

import config
import storage


def main() -> None:
    cfg = config.load_config()
    db_path = cfg["database_path"]
    storage.init_db(db_path)

    legacy_dir = Path(__file__).parent / "pe_history"
    if not legacy_dir.exists():
        print(f"No legacy directory at {legacy_dir} — nothing to migrate.")
        return

    json_files = sorted(legacy_dir.glob("*.json"))
    if not json_files:
        print(f"No JSON files in {legacy_dir} — nothing to migrate.")
        return

    print(f"Importing {len(json_files)} file(s) into {db_path}...\n")
    total_added = 0
    for path in json_files:
        ticker = path.stem
        with open(path) as f:
            rows = json.load(f)
        added = storage.merge_history(db_path, ticker, rows)
        skipped = len(rows) - added
        suffix = f" ({skipped} already present)" if skipped else ""
        print(f"  {ticker}: +{added} rows{suffix}")
        total_added += added

    print(f"\nDone. {total_added} rows imported.")
    print(f"Legacy data is still in {legacy_dir} — delete it when satisfied.")


if __name__ == "__main__":
    main()
