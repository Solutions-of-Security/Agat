#!/usr/bin/env python3
"""Acquire bounded, dated public IT-support snapshots from the official API."""
from __future__ import annotations

import argparse
import hashlib
from pathlib import Path
import sys
from urllib.request import urlopen

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from decision_runtime.artifacts import sealed
from decision_runtime.contracts import parse_json
from scripts.lib.decision_public_sources import ACQUISITION_SCHEMA, MODEL_REVISION, MODEL_URL, SITES, model_date, observation_window, private_directory, source_identity, source_url, write_json_new, write_raw_new
from scripts.lib.decision_shadow_pilot import require, timestamp, utc_now


def fetch(url):
    with urlopen(url, timeout=30) as response:
        require(response.status == 200 and response.url == url, "Public source endpoint changed")
        raw = response.read(4 * 1024 * 1024 + 1)
    require(len(raw) <= 4 * 1024 * 1024, "Public source response exceeds its bound")
    return raw


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--start-at", required=True)
    parser.add_argument("--end-at", required=True)
    parser.add_argument("--sites", nargs="+", choices=tuple(SITES), default=list(SITES))
    parser.add_argument("--max-pages-per-site", type=int, choices=range(1, 11), default=5)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        start, end = observation_window({"startAt": args.start_at, "endAt": args.end_at})
        require(end <= timestamp(utc_now(), "now").timestamp(), "Use a completed observation window")
        require(len(set(args.sites)) == len(args.sites), "Duplicate source site")
        commit, files = source_identity(ROOT)
        directory = private_directory(ROOT, args.output_dir)
    except Exception as error:
        print(f"Cannot prepare public acquisition: {error}", file=sys.stderr); return 1
    report = {"schemaVersion": ACQUISITION_SCHEMA, "status": "failed", "capturedAt": utc_now(),
        "window": {"startAt": args.start_at, "endAt": args.end_at}, "sites": args.sites,
        "sourceCommit": commit, "sourceFiles": files, "apiVersion": "2.3", "sort": "creation", "order": "asc",
        "filter": "withbody", "pageSize": 100, "maxPagesPerSite": args.max_pages_per_site, "pages": [],
        "modelMetadata": None, "failure": None, "routingEnabled": False, "qualification": "not_assessed"}
    total = 0
    try:
        model_url = MODEL_URL
        model_raw = fetch(model_url); model = parse_json(model_raw)
        require(model_date(model) < start, "Public question window predates the pinned checkpoint")
        write_raw_new(directory / "model-revision.json", model_raw)
        report["modelMetadata"] = {"file": "model-revision.json", "url": model_url,
            "sha256": hashlib.sha256(model_raw).hexdigest(), "bytes": len(model_raw), "revision": MODEL_REVISION, "lastModified": model["lastModified"]}
        for site in args.sites:
            for page in range(1, args.max_pages_per_site + 1):
                url = source_url(site, page, start, end)
                raw = fetch(url); total += len(raw)
                require(total <= 64 * 1024 * 1024, "Acquisition total exceeds its bound")
                filename = f"{site}-page-{page}.json"; write_raw_new(directory / filename, raw)
                report["pages"].append({"site": site, "page": page, "file": filename, "url": url,
                    "sha256": hashlib.sha256(raw).hexdigest(), "bytes": len(raw)})
                value = parse_json(raw)
                require(isinstance(value, dict) and not any(k in value for k in ("error_id", "error_name", "error_message"))
                        and isinstance(value.get("items"), list) and len(value["items"]) <= 100 and type(value.get("has_more")) is bool,
                        "Invalid public API response")
                # Preserve a failed receipt and stop rather than issuing another throttled request.
                require("backoff" not in value, "API requested backoff; preserve this attempt and acquire again after the stated delay")
                print(f"Source {site} page {page}: {len(value['items'])} questions; has_more={value['has_more']}", flush=True)
                if not value["has_more"]: break
            else:
                raise ValueError("Source pagination exceeds its bound; acquisition is incomplete")
        require(source_identity(ROOT) == (commit, files), "Acquisition sources changed")
        report["status"] = "completed"
    except Exception as error:
        report["failure"] = {"type": type(error).__name__, "message": str(error)[:1000]}
    write_json_new(directory / "acquisition.json", sealed(report))
    print(f"Public acquisition {report['status']}: {directory}")
    return 0 if report["status"] == "completed" else 1


if __name__ == "__main__": raise SystemExit(main())
