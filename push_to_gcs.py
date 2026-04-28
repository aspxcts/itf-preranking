#!/usr/bin/env python3
"""
push_to_gcs.py — Upload local pipeline output to GCS so the live app picks it up.

Run this on your local machine after any pipeline step to push results to
the itf-preranking-data bucket without needing a GCP Cloud Run refresh.

Usage
-----
    # After running main.py:
    python push_to_gcs.py --after main

    # After running calculate_rankings.py:
    python push_to_gcs.py --after calculate

    # After all three (main + calculate_rankings + merge_rankings):
    python push_to_gcs.py --after merge

    # Upload everything unconditionally:
    python push_to_gcs.py --all

    # Preview what would be uploaded without actually uploading:
    python push_to_gcs.py --all --dry-run

    # Specific week (if output files are date-stamped):
    python push_to_gcs.py --after merge --week 2026-04-20

Prerequisites
-------------
    gcloud auth application-default login
    pip install google-cloud-storage
"""

from __future__ import annotations

import argparse
import datetime
import os
import sys
from pathlib import Path

GCS_BUCKET = os.environ.get("GCS_BUCKET", "itf-preranking-data")

# Files produced at each pipeline stage
_STAGE_FILES: dict[str, list[str]] = {
    "main": [
        "latest_points_earned.json",
    ],
    "calculate": [
        "latest_points_earned.json",
        "latest_player_breakdowns.json",
    ],
    "merge": [
        "latest_points_earned.json",
        "latest_player_breakdowns.json",
        "latest_merged_rankings.json",
    ],
}


def _week_monday(anchor: datetime.date) -> datetime.date:
    return anchor - datetime.timedelta(days=anchor.weekday())


def _pipeline_date() -> datetime.date:
    """Mirror the same Monday-grace-period logic used in app.py."""
    now = datetime.datetime.now(datetime.timezone.utc)
    d = now.date()
    if d.weekday() == 0 and now.hour < 16:
        d -= datetime.timedelta(days=1)
    return d


def _files_for_stage(stage: str, monday: datetime.date) -> list[str]:
    return list(_STAGE_FILES.get(stage, []))


def upload(files: list[str], dry_run: bool = False) -> None:
    try:
        from google.cloud import storage
    except ImportError:
        print("[push] google-cloud-storage not installed.")
        print("       Run: pip install google-cloud-storage")
        sys.exit(1)

    output_dir = Path("output")
    client = storage.Client()
    bucket = client.bucket(GCS_BUCKET)

    uploaded = 0
    skipped = 0
    for fname in files:
        local = output_dir / fname
        if not local.exists():
            print(f"[push] SKIP  {fname}  (not found locally)")
            skipped += 1
            continue
        size_kb = local.stat().st_size / 1024
        if dry_run:
            print(f"[push] DRY   {fname}  ({size_kb:.1f} KB)  → gs://{GCS_BUCKET}/{fname}")
        else:
            blob = bucket.blob(fname)
            blob.upload_from_filename(str(local), content_type="application/json")
            blob.cache_control = "public, max-age=300"
            blob.patch()
            print(f"[push] OK    {fname}  ({size_kb:.1f} KB)  → gs://{GCS_BUCKET}/{fname}")
        uploaded += 1

    suffix = " (dry run)" if dry_run else ""
    print(
        f"\n[push] {uploaded} file(s) uploaded{suffix}, {skipped} skipped."
    )
    if not dry_run and uploaded:
        print(f"[push] Live app will reflect changes within ~5 minutes (GCS cache TTL).")


def main() -> None:
    global GCS_BUCKET
    parser = argparse.ArgumentParser(
        description="Upload local ITF pipeline output to GCS."
    )
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument(
        "--after",
        choices=["main", "calculate", "merge"],
        metavar="STAGE",
        help="Upload files produced up to this pipeline stage: main | calculate | merge",
    )
    group.add_argument(
        "--all",
        action="store_true",
        help="Upload all output files regardless of stage",
    )
    parser.add_argument(
        "--week",
        default=None,
        metavar="YYYY-MM-DD",
        help="Any date in the target week (defaults to current week).",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print what would be uploaded without actually uploading.",
    )
    parser.add_argument(
        "--bucket",
        default=GCS_BUCKET,
        help=f"GCS bucket name (default: {GCS_BUCKET})",
    )
    args = parser.parse_args()

    GCS_BUCKET = args.bucket

    anchor = (
        datetime.date.fromisoformat(args.week) if args.week else _pipeline_date()
    )
    monday = _week_monday(anchor)
    print(f"[push] Week: {monday.isoformat()}  Bucket: gs://{GCS_BUCKET}/")

    if args.all:
        all_files = list(dict.fromkeys(
            f
            for stage in ("main", "calculate", "merge")
            for f in _files_for_stage(stage, monday)
        ))
        files = all_files
    else:
        files = _files_for_stage(args.after, monday)

    print(f"[push] Files to upload: {files}\n")
    upload(files, dry_run=args.dry_run)


if __name__ == "__main__":
    main()
