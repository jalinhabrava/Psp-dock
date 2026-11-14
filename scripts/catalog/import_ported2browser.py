#!/usr/bin/env python3
"""Import the complete discovery index without fetching any game resources.

Offline:
  python scripts/catalog/import_ported2browser.py --html build/catalog-probe/sbs.html
Verify reproducibility:
  python scripts/catalog/import_ported2browser.py --html build/catalog-probe/sbs.html --check
Explicit metadata refresh:
  python scripts/catalog/import_ported2browser.py --refresh
"""
from __future__ import annotations

import argparse
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from psp_dock.media.web_port_catalog import (
    CatalogError, DEFAULT_TIMEOUT, MIN_CATALOG_ENTRIES, SOURCE_URL,
    parse_catalog_file, refresh_catalog, serialize_catalog, write_catalog_snapshot,
)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--html", type=Path, help="Saved UTF-8 index HTML (offline)")
    mode.add_argument("--refresh", action="store_true", help="Fetch only the public HTML index")
    parser.add_argument("--output", type=Path, default=ROOT / "web/catalog-snapshot.json")
    parser.add_argument("--source-url", default=SOURCE_URL)
    parser.add_argument("--min-entries", type=int, default=MIN_CATALOG_ENTRIES)
    parser.add_argument("--timeout", type=float, default=DEFAULT_TIMEOUT)
    parser.add_argument("--check", action="store_true", help="Check deterministic output without writing")
    args = parser.parse_args(argv)
    if args.check and args.refresh:
        parser.error("--check requires --html")
    try:
        if args.refresh:
            snapshot = refresh_catalog(args.output, source_url=args.source_url,
                                       timeout=args.timeout, min_entries=args.min_entries)
        else:
            snapshot = parse_catalog_file(args.html, source_url=args.source_url,
                                          min_entries=args.min_entries)
            if args.check:
                if args.output.read_bytes() != serialize_catalog(snapshot):
                    print(f"Snapshot differs: {args.output}", file=sys.stderr)
                    return 1
            else:
                write_catalog_snapshot(args.output, snapshot)
        print(f"{snapshot['catalog_total']} unique discovery games; "
              f"{len(serialize_catalog(snapshot))} bytes; {args.output}")
        return 0
    except (CatalogError, OSError, ValueError) as error:
        print(f"Catalogue import failed: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
