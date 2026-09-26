"""Run from the repository root: python -m decision_runtime --help."""

from __future__ import annotations

import argparse
import signal
import sys
from pathlib import Path

from .annotations import finalize_reviews, prepare_review
from .artifacts import read_json, write_new
from .calibration import Calibration
from .calibration_workflow import fit_calibration, freeze_plan
from .contracts import DecisionError, MAX_BODY_BYTES, Policy, canonical_json, fingerprint, parse_json
from .development import compare_development, prepare_development
from .engine import DecisionEngine
from .evaluation import evaluate, load_dataset
from .evaluation_guard import validate_evaluation_profile, validate_evaluation_start
from .model_store import DEFAULT_STORE, download
from .qualification import qualify
from .server import make_server


def main() -> int:
    backend = None
    old_sigterm = None
    parser = argparse.ArgumentParser(description="Agat local typed decisions (experimental shadow runtime)")
    commands = parser.add_subparsers(dest="command", required=True)
    fetch = commands.add_parser("download", help="Download pinned weights; the only network model operation")
    fetch.add_argument("name", choices=("decider-2b", "qwen3.5-2b-base"))
    fetch.add_argument("--store", type=Path, default=DEFAULT_STORE)
    for name in ("profile", "score", "serve", "evaluate"):
        sub = commands.add_parser(name)
        sub.add_argument("--manifest", type=Path, required=True)
        sub.add_argument("--policy", type=Path, help="Operator-owned policy JSON; never read from model input")
        sub.add_argument("--max-tokens", type=int, default=2048)
        sub.add_argument("--cache-limit-mib", type=int, help="MLX reusable-buffer cache limit, 0..4096 MiB; 0 disables cache")
        sub.add_argument("--inference-timeout-ms", type=int,
                         help="Opt-in isolated inference process, 100..10000 ms; timeout stops it until restart")
        sub.add_argument("--calibration", type=Path, help="Fitted artifact bound to this exact model and question schema")
        if name == "serve":
            sub.add_argument("--port", type=int, default=8766)
            sub.add_argument("--exit-on-backend-unavailable", action="store_true",
                             help="Exit 75 after delivering a failed isolated-backend response; restart belongs to the service manager")
        elif name == "score":
            sub.add_argument("--input", type=Path, required=True)
        elif name == "profile":
            sub.add_argument("--output", type=Path, required=True)
        else:
            sub.add_argument("--dataset", type=Path, required=True)
            sub.add_argument("--split", choices=("development", "calibration", "holdout"), default="development")
            sub.add_argument("--output", type=Path, required=True)
            sub.add_argument("--reverse-options", action="store_true")
            sub.add_argument("--plan", type=Path, help="Frozen v2 experiment; required before calibration/holdout scoring")
            sub.add_argument("--frozen-calibration", type=Path,
                             help="Prior fit artifact required for holdout preflight; does not transform raw logits")
    prepare = commands.add_parser("prepare-review", help="Create a blind review template without model predictions")
    prepare.add_argument("--pool", type=Path, required=True)
    prepare.add_argument("--seed", required=True)
    prepare.add_argument("--output", type=Path, required=True)
    finalize = commands.add_parser("finalize-review", help="Resolve two independent reviews into grouped splits")
    finalize.add_argument("--first", type=Path, required=True)
    finalize.add_argument("--second", type=Path, required=True)
    finalize.add_argument("--adjudication", type=Path)
    finalize.add_argument("--output", type=Path, required=True)
    development = commands.add_parser("prepare-development", help="Export approved assistant labels for development only")
    development.add_argument("--pool", type=Path, required=True)
    development.add_argument("--approval", type=Path, required=True)
    development.add_argument("--review", type=Path, required=True)
    development.add_argument("--split-reference", type=Path, required=True,
                             help="Original blank review package bound by the source review; its seed stays fixed")
    development.add_argument("--output", type=Path, required=True)
    comparison = commands.add_parser("compare-development", help="Compare raw candidate scores without consuming holdout")
    comparison.add_argument("--dataset", type=Path, required=True)
    comparison.add_argument("--scores", type=Path, nargs="+", required=True)
    comparison.add_argument("--output", type=Path, required=True)
    freeze = commands.add_parser("freeze", help="Freeze candidate, dataset, policy and criteria before fitting")
    freeze.add_argument("--dataset", type=Path, required=True)
    freeze.add_argument("--manifest", type=Path, required=True)
    freeze.add_argument("--criteria", type=Path, required=True)
    freeze.add_argument("--policy", type=Path)
    freeze.add_argument("--runtime-profile", type=Path, help="Raw execution profile exported before scoring; required for qualification pass")
    freeze.add_argument("--output", type=Path, required=True)
    for name in ("calibrate", "qualify"):
        sub = commands.add_parser(name)
        sub.add_argument("--dataset", type=Path, required=True)
        sub.add_argument("--scores", type=Path, required=True)
        sub.add_argument("--plan", type=Path, required=True)
        sub.add_argument("--output", type=Path, required=True)
        if name == "qualify":
            sub.add_argument("--calibration", type=Path, required=True)
    args = parser.parse_args()
    try:
        if args.command == "download":
            print(download(args.name, args.store))
            return 0
        if getattr(args, "output", None) and args.output.exists():
            raise ValueError("Output already exists; choose a new experiment path")
        if args.command == "prepare-review":
            write_new(args.output, prepare_review(read_json(args.pool), args.seed))
            print(args.output)
            return 0
        if args.command == "finalize-review":
            dataset = finalize_reviews(read_json(args.first), read_json(args.second),
                                       read_json(args.adjudication) if args.adjudication else None)
            write_new(args.output, dataset)
            print(canonical_json({"output": str(args.output), "splits": dataset["annotation"]["splitCounts"]}))
            return 0
        if args.command in {"prepare-development", "compare-development"}:
            if args.command == "prepare-development":
                result = prepare_development(args.pool, args.approval, args.review, args.split_reference)
            else:
                result = compare_development(load_dataset(args.dataset), [read_json(path) for path in args.scores])
            write_new(args.output, result)
            print(canonical_json({"output": str(args.output), "sha256": result["sha256"],
                                  "status": result.get("status", "development-only"),
                                  "cases": len(result.get("cases", result.get("dataset", {}).get("caseIds", [])))}))
            return int(any(m[order]["metrics"]["backendErrors"] for m in result.get("models", [])
                           for order in ("original", "reversed")))
        if args.command in {"freeze", "calibrate", "qualify"}:
            dataset = load_dataset(args.dataset)
            if args.command == "freeze":
                manifest = read_json(args.manifest)
                required = {"repository", "revision", "artifactSha256", "files"}
                if (not isinstance(manifest, dict) or not required <= manifest.keys()
                        or not isinstance(manifest["files"], dict) or "tokenizer.json" not in manifest["files"]):
                    raise ValueError("Invalid model manifest; use the download command")
                model = {k: manifest[k] for k in ("repository", "revision", "artifactSha256")}
                model["tokenizerSha256"] = manifest["files"]["tokenizer.json"]
                policy = Policy.from_dict(read_json(args.policy)) if args.policy else Policy()
                result = freeze_plan(dataset, policy, read_json(args.criteria), model,
                                     execution_profile=read_json(args.runtime_profile) if args.runtime_profile else None)
            elif args.command == "calibrate":
                result = fit_calibration(dataset, read_json(args.scores), read_json(args.plan))
            else:
                result = qualify(dataset, read_json(args.scores), read_json(args.calibration), read_json(args.plan))
            write_new(args.output, result)
            print(canonical_json({"output": str(args.output), "sha256": result["sha256"],
                                  "status": result.get("status", "created"), "fit": result.get("fit"),
                                  "failedGates": [g for g in result.get("gates", []) if not g["passed"]]}))
            return 2 if result.get("status") == "not_qualified" else 0
        # Validate data/configuration before loading expensive weights.
        if args.command == "serve" and args.exit_on_backend_unavailable and args.inference_timeout_ms is None:
            raise ValueError("--exit-on-backend-unavailable requires --inference-timeout-ms")
        policy = Policy.from_dict(parse_json(args.policy.read_bytes())) if args.policy else Policy()
        if args.command == "evaluate":
            dataset = load_dataset(args.dataset)
            if args.output.exists():
                raise ValueError("Evaluation output already exists; choose a new run path")
            if not any(c["split"] == args.split for c in dataset["cases"]):
                raise ValueError("Selected split is empty")
            plan = read_json(args.plan) if args.plan else None
            frozen_calibration = read_json(args.frozen_calibration) if args.frozen_calibration else None
            validate_evaluation_start(dataset, args.split, plan, frozen_calibration,
                                      reverse_options=args.reverse_options, calibrated=args.calibration is not None)
        elif args.command == "score":
            if args.input.stat().st_size > MAX_BODY_BYTES:
                raise ValueError("Input file is too large")
            raw = parse_json(args.input.read_bytes())
        from .mlx_backend import MlxBackend

        if args.inference_timeout_ms is not None:
            from .isolated import IsolatedBackend, mlx_factory
            def stop_runtime(_signal, _frame):
                raise KeyboardInterrupt
            old_sigterm = signal.signal(signal.SIGTERM, stop_runtime)
            backend = IsolatedBackend(mlx_factory, {"manifest": str(args.manifest.resolve()),
                                      "max_tokens": args.max_tokens, "cache_limit_mib": args.cache_limit_mib},
                                      timeout_ms=args.inference_timeout_ms)
        else:
            backend = MlxBackend(args.manifest, args.max_tokens, cache_limit_mib=args.cache_limit_mib)
        calibration = Calibration(read_json(args.calibration)) if args.calibration else None
        engine = DecisionEngine(backend, policy, calibration)
        if args.command == "profile":
            profile = engine.profile()
            write_new(args.output, profile)
            print(canonical_json({"output": str(args.output), "profileSha256": fingerprint(profile)}))
            return 0
        if args.command == "score":
            result = engine.decide(raw)
            print(canonical_json(result))
            return int(result["status"] == "error")
        if args.command == "evaluate":
            validate_evaluation_profile(plan, engine.profile())
            report = evaluate(engine, dataset, args.split, reverse_options=args.reverse_options)
            args.output.parent.mkdir(parents=True, exist_ok=True)
            with args.output.open("x", encoding="utf-8") as stream:
                stream.write(canonical_json(report) + "\n")
            print(canonical_json({"output": str(args.output), "metrics": report["metrics"],
                                  "optionOrder": report.get("optionOrder"), "qualityGate": report["qualityGate"]}))
            return int(bool(report["metrics"]["backendErrors"] or report.get("optionOrder", {}).get("reversedBackendErrors")))
        with make_server(engine, args.port, exit_on_backend_unavailable=args.exit_on_backend_unavailable) as server:
            print(f"Agat decision runtime: http://127.0.0.1:{server.server_port} (shadow)", flush=True)
            server.serve_forever()
            if args.exit_on_backend_unavailable and server.backend_failed.is_set():
                return 75
    except KeyboardInterrupt:
        return 130
    except (DecisionError, ValueError, OSError, ImportError, RuntimeError) as exc:
        # Startup diagnostics contain no input payload. Inference exceptions are sanitized by the engine.
        print(f"Decision runtime: {exc}", file=sys.stderr)
        return 1
    finally:
        if hasattr(backend, "close"):
            backend.close()
        if old_sigterm is not None:
            signal.signal(signal.SIGTERM, old_sigterm)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
