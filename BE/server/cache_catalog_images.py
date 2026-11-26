#!/usr/bin/env python3
"""Prepare card/thumb JPEGs for all catalog photo/menu/review images.

Existing immutable v1 JPEGs are used as the backfill input, so this does not
download the old catalog again. New URLs are downloaded once for both profiles.

Audit rows contain source URLs, so --report must be outside the public media
directory. Re-running safely skips completed files. --limit is for smoke tests.
"""
from __future__ import annotations

import argparse
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
import itertools
import json
from pathlib import Path
import time

from app import catalog_media


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", type=Path, required=True)
    parser.add_argument("--media-dir", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--workers", type=int, default=8)
    parser.add_argument("--retries", type=int, default=1)
    parser.add_argument("--limit", type=int, help="Smoke test only; normal runs process every URL")
    args = parser.parse_args(argv)
    if not 1 <= args.workers <= 32:
        parser.error("--workers must be between 1 and 32")
    if not 0 <= args.retries <= 3:
        parser.error("--retries must be between 0 and 3")
    if args.limit is not None and args.limit < 1:
        parser.error("--limit must be positive")
    if not args.data_dir.is_dir():
        parser.error("--data-dir does not exist")
    for filename in ("photos.jsonl", "menus.jsonl", "reviews.jsonl"):
        if not (args.data_dir / filename).is_file():
            parser.error(f"missing catalog source file: {filename}")
    if args.report.resolve().is_relative_to(args.media_dir.resolve()):
        parser.error("--report must be outside the public media directory")
    source = catalog_media.iter_catalog_urls(args.data_dir)
    if args.limit is not None:
        source = itertools.islice(source, args.limit)
    urls = iter(source)
    args.report.parent.mkdir(parents=True, exist_ok=True)
    counts = {"generated": 0, "cached": 0, "failed": 0}
    profile_counts = {variant: {"generated": 0, "cached": 0, "failed": 0}
                      for variant in catalog_media.VARIANTS}
    totals = {"network_bytes": 0, "generated_dst_bytes": 0}
    started = last_progress = time.monotonic()
    # Append each completed item so an interrupted run retains its audit trail.
    with args.report.open("a", encoding="utf-8", buffering=1) as report:
        with ThreadPoolExecutor(max_workers=args.workers) as pool:
            pending = set()
            exhausted = False
            while pending or not exhausted:
                while not exhausted and len(pending) < args.workers * 2:
                    try:
                        url = next(urls)
                    except StopIteration:
                        exhausted = True
                        break
                    pending.add(pool.submit(catalog_media.cache_image, url,
                                            args.media_dir, args.retries))
                if not pending:
                    break
                finished, pending = wait(pending, timeout=10, return_when=FIRST_COMPLETED)
                for future in finished:
                    result = future.result()
                    report.write(json.dumps(result, ensure_ascii=False) + "\n")
                    counts[result["status"]] += 1
                    totals["network_bytes"] += result["network_bytes"]
                    for variant, profile in result["profiles"].items():
                        profile_counts[variant][profile["status"]] += 1
                        if profile["status"] == "generated":
                            totals["generated_dst_bytes"] += profile["dst_bytes"]
                now = time.monotonic()
                if now - last_progress >= 10:
                    print(json.dumps({"event": "progress", **counts, **totals,
                                      "elapsed_seconds": round(now - started, 1)}), flush=True)
                    last_progress = now
    print(json.dumps({"event": "complete", **counts, **totals, "profiles": profile_counts,
                      "elapsed_seconds": round(time.monotonic() - started, 1)}), flush=True)
    return 1 if counts["failed"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
