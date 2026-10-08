#!/usr/bin/env python3
"""Prepare whole public option-order diagnostics offline; no weight inference."""
import argparse
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from decision_runtime.artifacts import sealed
from decision_runtime.contracts import canonical_json, parse_json
from decision_runtime.model_store import sha256_file, verify_manifest
from scripts.lib.decision_public_load import validate_context
from scripts.lib.decision_public_load_verification import sources_at, CONTEXT_PATHS
from scripts.lib.decision_public_context import verify_profile
from scripts.lib.decision_public_permutations import SCHEMA, RULE, BUDGET, SOURCE_PATHS, variants, token_inventory, summary, validate_profile
from scripts.lib.decision_public_sources import pinned_input, private_directory, write_json_new
from scripts.lib.decision_shadow_pilot import require, utc_now

SPEC = importlib.util.spec_from_file_location("permutation_tokenizer", ROOT/"scripts/profile-public-support-context.py")
profiler = importlib.util.module_from_spec(SPEC); SPEC.loader.exec_module(profiler)
SPEC = importlib.util.spec_from_file_location("permutation_source_freezer", ROOT/"scripts/run-decision-arrival-rate.py")
launcher = importlib.util.module_from_spec(SPEC); SPEC.loader.exec_module(launcher)
PROFILE_PATH = "docs/qualification/local-decisions/performance/profiles/runtime-0.12.3-wired-4096.json"


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    for key in ("context-profile", "manifest", "runtime-python", "output-dir"): parser.add_argument("--"+key, type=Path, required=True)
    parser.add_argument("--context-profile-file-sha256", required=True)
    args = parser.parse_args(argv)
    try:
        private = (ROOT/"docs/private").resolve()
        require(args.output_dir.resolve().is_relative_to(private) and args.output_dir.resolve() != private
                and not args.output_dir.exists() and not args.output_dir.is_symlink(), "Use a new private output directory")
        raw = pinned_input(args.context_profile, args.context_profile_file_sha256, 32*1024*1024)
        context = validate_context(parse_json(raw)); sources_at(ROOT, context["sourceCommit"], context["sourceFiles"], CONTEXT_PATHS)
        inputs = variants(context)
        imports = {str(Path(module.__file__).resolve().relative_to(ROOT)) for module in list(sys.modules.values())
                   if getattr(module, "__file__", None) and str(module.__file__).endswith(".py") and Path(module.__file__).resolve().is_relative_to(ROOT)}
        paths = sorted(set(SOURCE_PATHS)|imports); identity = launcher.frozen_sources(paths)
        profile_raw = pinned_input(ROOT/PROFILE_PATH, context["profileFileSha256"], 1024*1024)
        require(parse_json(profile_raw) == context["profile"], "Frozen profile bytes differ")
        manifest, snapshot = verify_manifest(args.manifest); verify_profile(context["profile"], manifest)
        require(sha256_file(args.manifest) == context["manifestFileSha256"], "Frozen manifest differs")
        requests = (canonical_json([row["request"] for row in inputs])+"\n").encode()
        require(len(requests) <= 8*1024*1024, "Variant tokenizer payload exceeds its bound")
        requirements = dict(line.split("==") for line in (ROOT/"decision_runtime/requirements-mlx.txt").read_text().splitlines() if line and not line.startswith("#"))
        environment = {**os.environ, "HF_HUB_OFFLINE": "1", "TRANSFORMERS_OFFLINE": "1", "HF_HUB_DISABLE_TELEMETRY": "1"}
        child = subprocess.run([str(args.runtime_python.absolute()), "-B", "-c", profiler.TOKENIZER_CODE, str(snapshot), *requirements],
                               cwd=ROOT, env=environment, input=requests, capture_output=True, timeout=60)
        require(child.returncode == 0 and len(child.stdout) <= 1024*1024, "Offline variant tokenizer failed or exceeded its output bound")
        tokenized = parse_json(child.stdout); require(set(tokenized) == {"rows", "environment"}, "Unexpected tokenizer output")
        require(tokenized["environment"] == context["tokenizerEnvironment"] == {"python": "3.13.12", "machine": "arm64", "packages": requirements}, "Pinned tokenizer environment differs")
        rows = token_inventory(context, tokenized["rows"])
        report = sealed({"schemaVersion": SCHEMA, "status": "tokenized_without_inference", "createdAt": utc_now(),
            "sourceCommit": identity[0], "sourceFiles": identity[1], "originalContextFileSha256": args.context_profile_file_sha256,
            "originalContextSealSha256": context["sha256"], "originalPoolSha256": context["poolSha256"],
            **{key: context[key] for key in ("profileFileSha256", "profileSha256", "manifestFileSha256", "model", "tokenizerEnvironment")},
            "rule": RULE, "budget": BUDGET, "summary": summary(context, rows), "inputs": rows,
            "stateTranslationApplied": False, "inputTruncationApplied": False, "semanticOptionsChanged": False, "statisticalIndependenceVerified": False,
            "classificationAccuracyMeasured": False, "referenceLabels": 0, "predictions": 0, "modelCalls": 0, "calibrationRequestsTokenized": 0,
            "holdoutRequestsTokenized": 0, "ownersAppointed": False, "sloAccepted": False, "routingEnabled": False, "qualification": "not_assessed"})
        validate_profile(report, context, args.context_profile_file_sha256)
        require(launcher.frozen_sources(paths) == identity and pinned_input(args.context_profile, args.context_profile_file_sha256, 32*1024*1024) == raw
                and pinned_input(ROOT/PROFILE_PATH, context["profileFileSha256"], 1024*1024) == profile_raw
                and sha256_file(args.manifest) == context["manifestFileSha256"] and verify_manifest(args.manifest)[0] == manifest, "Sources, context or model changed")
        directory = private_directory(ROOT, args.output_dir); write_json_new(directory/"permutation-context.json", report)
        print(json.dumps({"status": report["status"], **report["summary"]})); return 0
    except Exception as error:
        print("Cannot profile public option permutations: "+type(error).__name__, file=sys.stderr); return 1


if __name__ == "__main__": raise SystemExit(main())
