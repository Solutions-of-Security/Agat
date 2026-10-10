#!/usr/bin/env python3
"""Prepare one self-contained Russian HTML form from pinned blank and translation files."""
import argparse
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from decision_runtime.contracts import parse_json
from scripts.lib.decision_public_sources import pinned_input, private_directory, write_json_new
from scripts.lib.decision_review_form import MAX_REVIEW_BYTES, render_form


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("review", "localization"):
        parser.add_argument("--" + name, type=Path, required=True)
        parser.add_argument("--" + name + "-file-sha256", required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        raw = pinned_input(args.review, args.review_file_sha256, MAX_REVIEW_BYTES)
        localized = pinned_input(args.localization, args.localization_file_sha256, MAX_REVIEW_BYTES)
        html, manifest = render_form(parse_json(raw), parse_json(localized), args.review_file_sha256,
                                     args.localization_file_sha256, ROOT / "scripts/review-form")
        directory = private_directory(ROOT, args.output_dir)
        with (directory / "review-form.html").open("x", encoding="utf-8") as output:
            output.write(html)
        (directory / "review-form.html").chmod(0o600)
        write_json_new(directory / "manifest.json", manifest)
        with (directory / "review.blank.json").open("xb") as output:
            output.write(raw)
        (directory / "review.blank.json").chmod(0o600)
    except Exception as error:
        print("Не удалось подготовить форму: " + str(error)[:300], file=sys.stderr)
        return 1
    print("Форма готова: " + str(directory / "review-form.html"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
