"""Build the shipped metadata inventory and a spreadsheet-friendly public CSV."""
import argparse
import csv
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from psp_dock.media.official_port_inventory import build_inventory


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--capture', type=Path, required=True)
    parser.add_argument('--probes', type=Path)
    parser.add_argument('--requirements', type=Path)
    parser.add_argument('--observations', type=Path)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--csv', type=Path, required=True)
    args = parser.parse_args()
    capture = json.loads(args.capture.read_text(encoding='utf-8'))
    probes = json.loads(args.probes.read_text(encoding='utf-8')) if args.probes else {}
    requirements = json.loads(args.requirements.read_text(encoding='utf-8')) if args.requirements else {}
    observations = json.loads(args.observations.read_text(encoding='utf-8')) if args.observations else {}
    inventory = build_inventory(capture, probes, requirements, observations)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(inventory, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    args.csv.parent.mkdir(parents=True, exist_ok=True)
    fields = ['id', 'title', 'author', 'original_platforms', 'category', 'detail_url',
              'direct_candidates', 'link_status', 'verification_state',
              'requires_disc_image', 'requires_original_files', 'optional_original_files',
              'file_requirement_review', 'controls_declared', 'documented_controls',
              'additional_controls', 'runtime_observation', 'runtime_verified',
              'gamepad_tested', 'description']
    with args.csv.open('w', encoding='utf-8-sig', newline='') as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for game in inventory['games']:
            row = {key: game[key] for key in fields}
            for key in ('original_platforms', 'direct_candidates', 'documented_controls'):
                row[key] = ' | '.join(row[key])
            for key in ('controls_declared', 'additional_controls', 'file_requirement_review', 'runtime_observation'):
                row[key] = json.dumps(row[key], ensure_ascii=False)
            # Public titles/descriptions must remain inert in spreadsheet apps.
            for key, value in row.items():
                if isinstance(value, str) and value.lstrip().startswith(('=', '+', '-', '@')):
                    row[key] = "'" + value
            writer.writerow(row)
    print(json.dumps(inventory['summary'], ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
