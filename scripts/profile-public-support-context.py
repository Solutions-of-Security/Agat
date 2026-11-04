#!/usr/bin/env python3
"""Count exact public development prompts offline; never load weights for inference."""
import argparse
import hashlib
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
from scripts.lib.decision_public_context import (
    PROFILE_SCHEMA, context_result, development_cases, reconstruct, verify_profile,
)
from scripts.lib.decision_public_sources import pinned_input, private_directory, write_json_new
from scripts.lib.decision_shadow_pilot import require, utc_now

SOURCE_PATHS = ["decision_runtime", "scripts/profile-public-support-context.py", "scripts/lib/decision_public_context.py",
                "scripts/lib/decision_public_sources.py", "scripts/lib/decision_shadow_pilot.py"]
TOKENIZER_CODE = '''import importlib.metadata,json,platform,sys
from transformers import AutoTokenizer
from decision_runtime.contracts import Request,parse_json
from decision_runtime.mlx_backend import prompt_parts,encode_request
tokenizer=AutoTokenizer.from_pretrained(sys.argv[1],local_files_only=True,trust_remote_code=False)
rows=[]
for raw in parse_json(sys.stdin.buffer.read(8388609)):
 request=Request.from_dict(raw)
 parts=[len(tokenizer.encode(part,add_special_tokens=False)) for part in prompt_parts(request)]
 ids,labels=encode_request(tokenizer,request,sum(parts))
 assert len(ids)==sum(parts) and len(labels)==len(request.options)
 rows.append({'id':request.id,'inputSha256':request.input_sha256,'partTokens':parts,
              'inputTokens':sum(parts),'labelContinuationsVerified':True})
print(json.dumps({'rows':rows,'environment':{'python':platform.python_version(),'machine':platform.machine(),
 'packages':{name:importlib.metadata.version(name) for name in sys.argv[2:]}}}))'''


def source_identity(root):
    commit = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=root, text=True, timeout=5).strip()
    names = subprocess.check_output(["git", "ls-tree", "-r", "--name-only", commit, "--", *SOURCE_PATHS], cwd=root, text=True, timeout=5).splitlines()
    require(all(name in names or any(member.startswith(name + "/") for member in names) for name in SOURCE_PATHS),
            "Commit all context profiler sources before use")
    require(not subprocess.check_output(["git", "ls-files", "--others", "--exclude-standard", "--", *SOURCE_PATHS], cwd=root, timeout=5),
            "Untracked context profiler source")
    sources = {}
    for name in names:
        raw = (root / name).read_bytes()
        committed = subprocess.check_output(["git", "show", f"{commit}:{name}"], cwd=root, timeout=5)
        require(raw == committed, "Context profiler source drift")
        sources[name] = hashlib.sha256(raw).hexdigest()
    return commit, sources


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--import-receipt", type=Path, required=True)
    parser.add_argument("--import-file-sha256", required=True)
    parser.add_argument("--acquisition", type=Path, required=True)
    parser.add_argument("--profile", type=Path, required=True)
    parser.add_argument("--profile-file-sha256", required=True)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--runtime-python", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        private = (ROOT / "docs/private").resolve()
        require(args.output_dir.absolute().resolve().is_relative_to(private) and args.output_dir.resolve() != private
                and not args.output_dir.exists() and not args.output_dir.is_symlink(), "Use a new private output directory")
        identity = source_identity(ROOT)
        imported, acquisition, pool = reconstruct(ROOT, args.import_receipt, args.import_file_sha256, args.acquisition)
        selected = development_cases(pool)
        profile_raw = pinned_input(args.profile, args.profile_file_sha256, 1024 * 1024)
        profile = parse_json(profile_raw)
        manifest_sha = sha256_file(args.manifest)
        manifest, snapshot = verify_manifest(args.manifest)
        verify_profile(profile, manifest)
        requirements = dict(line.split("==") for line in (ROOT / "decision_runtime/requirements-mlx.txt").read_text().splitlines()
                            if line and not line.startswith("#"))
        requests = (canonical_json([case["request"] for case in selected]) + "\n").encode()
        require(len(requests) <= 8 * 1024 * 1024, "Tokenization input exceeds its bound")
        child = subprocess.run([str(args.runtime_python.absolute()), "-B", "-c", TOKENIZER_CODE, str(snapshot), *requirements],
            cwd=ROOT, env={**os.environ, "HF_HUB_OFFLINE": "1", "TRANSFORMERS_OFFLINE": "1", "HF_HUB_DISABLE_TELEMETRY": "1"},
            input=requests, capture_output=True, timeout=60)
        require(child.returncode == 0 and len(child.stdout) <= 1024 * 1024, "Offline tokenizer failed or exceeded its output bound")
        tokenized = parse_json(child.stdout)
        require(set(tokenized) == {"rows", "environment"}, "Tokenizer output contract differs")
        environment = tokenized["environment"]
        require(environment.get("python") == "3.13.12" and environment.get("machine") == "arm64"
                and environment.get("packages") == requirements, "Use the pinned native tokenizer dependency runtime")
        value = context_result(pool, selected, tokenized["rows"], profile)
        require(source_identity(ROOT) == identity and sha256_file(args.manifest) == manifest_sha,
                "Context sources or model manifest changed during tokenization")
        require(verify_manifest(args.manifest)[0] == manifest, "Model/tokenizer artifact drift")
        require(pinned_input(args.profile, args.profile_file_sha256, 1024 * 1024) == profile_raw,
                "Profile changed during tokenization")
        again = reconstruct(ROOT, args.import_receipt, args.import_file_sha256, args.acquisition)
        require(again == (imported, acquisition, pool), "Public source receipts changed during tokenization")
        report = sealed({"schemaVersion": PROFILE_SCHEMA, "status": "observed", "createdAt": utc_now(),
                         "sourceCommit": identity[0], "sourceFiles": identity[1],
                         "importFileSha256": args.import_file_sha256, "importSha256": imported["sha256"],
                         "acquisitionFileSha256": imported["acquisitionFileSha256"], "acquisitionSha256": acquisition["sha256"],
                         "profileFileSha256": args.profile_file_sha256, "profileSha256": hashlib.sha256(canonical_json(profile).encode()).hexdigest(),
                         "profile": profile, "manifestFileSha256": manifest_sha,
                         "model": {key: manifest[key] for key in ("repository", "revision", "artifactSha256")},
                         "tokenizerEnvironment": environment, **value})
        directory = private_directory(ROOT, args.output_dir)
        write_json_new(directory / "context-profile.json", report)
        print(json.dumps({"status": report["status"], "developmentCases": value["developmentCases"],
                          "contextEligibleCases": value["contextEligibleCases"], "contextTooLongCases": value["contextTooLongCases"],
                          "inputTokenRange": value["inputTokenRange"], "output": str(directory / "context-profile.json")}))
        return 0
    except Exception as error:
        print(f"Cannot profile public development context: {type(error).__name__}", file=sys.stderr)
        return 1


if __name__ == "__main__": raise SystemExit(main())
