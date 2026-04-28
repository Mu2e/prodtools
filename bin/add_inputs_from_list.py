#!/usr/bin/env python3
import argparse
import json
import os
import sys


def main() -> None:
    parser = argparse.ArgumentParser(description="Append dataset names from a list into input_data of evntuple.json")
    parser.add_argument("list_file", help="Path to text file with dataset names (one per line)")
    parser.add_argument(
        "--json",
        dest="json_path",
        default=os.path.join("data", "mdc2020", "evntuple.json"),
        help="Path to evntuple.json (default: data/mdc2020/evntuple.json)",
    )
    args = parser.parse_args()

    # Read JSON file
    with open(args.json_path, "r") as f:
        data = json.load(f)

    # Get existing dataset names (avoid duplicates)
    existing = {next(iter(item.keys())) for item in data[0]["input_data"]}

    # Read new datasets from text file
    with open(args.list_file, "r") as f:
        new_datasets = [line.strip() for line in f if line.strip() and not line.strip().startswith("#")]

    # Add only new datasets
    added = 0
    for dataset in new_datasets:
        if dataset not in existing:
            data[0]["input_data"].append({dataset: 1})
            existing.add(dataset)
            added += 1

    if added == 0:
        print("No new items to add (all already present)")
        return

    # Write back JSON file
    with open(args.json_path, "w") as f:
        json.dump(data, f, indent=2)

    print(f"Added {added} item(s) to {args.json_path}")


if __name__ == "__main__":
    main()


