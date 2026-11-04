"""Qwen3.5 direct letter logits on Apple Silicon, without generated tokens."""

from __future__ import annotations

import importlib.metadata
import os
import platform
import time
from pathlib import Path

from . import implementation_sha256
from .contracts import DecisionError, LETTERS, Request, parse_json
from .engine import Scores
from .model_store import verify_manifest

PROMPT_VERSION = "agat.state-first.letters.v1"


def wired_limit_bytes(limit_mib: int | None) -> int | None:
    if limit_mib is not None and (type(limit_mib) is not int or not 0 <= limit_mib <= 65536):
        raise ValueError("wired_limit_mib must be an integer between 0 and 65536, or None")
    return None if limit_mib is None else limit_mib * 1024 * 1024


def configure_wired_limit(mx, limit_bytes: int | None) -> None:
    if limit_bytes is None:
        return
    version = platform.mac_ver()[0]
    major = version.split(".")[0]
    if platform.system() != "Darwin" or not major.isdigit() or int(major) < 15:
        raise RuntimeError("Explicit wired memory requires macOS 15 or newer")
    device = mx.device_info()
    total, available = device.get("memory_size"), device.get("max_recommended_working_set_size")
    if type(total) is not int or type(available) is not int or total <= 0 or available <= 0:
        raise RuntimeError("MLX device does not expose a valid wired memory budget")
    if limit_bytes >= total or limit_bytes > available:
        raise ValueError("Wired memory exceeds the device budget or is not below total memory")
    # Apply once before load/evaluation. This is a per-process residency budget,
    # not an allocation cap or a change to the system's wired limit.
    mx.set_wired_limit(limit_bytes)


def prompt_parts(request: Request) -> tuple[str, str]:
    # State and question are encoded separately to match the decider training format.
    # Questions are evaluated independently, so no question sees another question's text/answer.
    options = "".join(f"\n({LETTERS[i]}) {o.description}" for i, o in enumerate(request.options))
    return f"Context:\n{request.state}", f"\n\nQuestion: {request.question}\nOptions:{options}\nAnswer: ("


def encode_request(tokenizer, request: Request, max_tokens: int) -> tuple[list[int], list[int]]:
    state, question = prompt_parts(request)
    ids = tokenizer.encode(state, add_special_tokens=False) + tokenizer.encode(question, add_special_tokens=False)
    if len(ids) > max_tokens:
        raise DecisionError("context_too_long", "Input exceeds token limit; no silent truncation")
    label_ids = []
    for letter in LETTERS[:len(request.options)]:
        encoded = tokenizer.encode(letter, add_special_tokens=False)
        if len(encoded) != 1 or tokenizer.decode(ids + encoded) != state + question + letter:
            raise DecisionError("unsupported_tokenizer", "Labels must be unique single-token continuations")
        label_ids.append(encoded[0])
    if len(set(label_ids)) != len(label_ids):
        raise DecisionError("unsupported_tokenizer", "Label token collision")
    return ids, label_ids


class MlxBackend:
    def __init__(self, manifest_path: Path, max_tokens: int = 2048, *, cache_limit_mib: int | None = None,
                 wired_limit_mib: int | None = None):
        if type(max_tokens) is not int or not 64 <= max_tokens <= 4096:
            raise ValueError("max_tokens must be between 64 and 4096")
        if cache_limit_mib is not None and (type(cache_limit_mib) is not int or not 0 <= cache_limit_mib <= 4096):
            raise ValueError("cache_limit_mib must be an integer between 0 and 4096, or None")
        wired_bytes = wired_limit_bytes(wired_limit_mib)
        started = time.perf_counter()
        manifest, snapshot = verify_manifest(manifest_path)
        config = parse_json((snapshot / "config.json").read_bytes())
        if config.get("model_type") not in {"qwen3_5", "qwen3_5_text"} or config.get("model_file"):
            raise ValueError("Only the built-in Qwen3.5 architecture is supported")
        if config.get("quantization") or config.get("quantization_config"):
            raise ValueError("Quantized models require separate qualification")
        # This code path cannot fetch weights or tokenizer files. Downloads are an explicit command.
        os.environ["HF_HUB_OFFLINE"] = "1"
        os.environ["TRANSFORMERS_OFFLINE"] = "1"
        import mlx.core as mx
        from mlx_lm import load

        if not mx.metal.is_available():
            raise RuntimeError("This backend requires Apple Silicon with Metal")
        configure_wired_limit(mx, wired_bytes)
        self.mx = mx
        # mlx-lm 0.31.3 implements this text backbone in qwen3_5, but does not map
        # Transformers' qwen3_5_text alias. Its ModelArgs accepts the flat text config.
        self.model, self.tokenizer = load(str(snapshot), model_config={"model_type": "qwen3_5"},
                                         tokenizer_config={"local_files_only": True, "trust_remote_code": False})
        # This optimized head path deliberately supports only this pinned, unquantized architecture.
        if not type(self.model).__module__.endswith(".qwen3_5"):
            raise ValueError("Only Qwen3.5 models are supported by this scoring backend")
        self.text_model = self.model.language_model
        tied = self.text_model.args.tie_word_embeddings
        self.head = self.text_model.model.embed_tokens if tied else self.text_model.lm_head
        if hasattr(self.head, "bits"):
            raise ValueError("Quantized heads require a separately qualified scoring path")
        # Bound reusable allocator buffers, not live tensors or total process RAM.
        # This setting is process-wide; the CLI owns one model per process.
        cache_limit_bytes = None if cache_limit_mib is None else cache_limit_mib * 1024 * 1024
        if cache_limit_bytes is not None:
            mx.set_cache_limit(cache_limit_bytes)
        self.max_tokens = max_tokens
        self.identity = {
            "repository": manifest["repository"], "revision": manifest["revision"],
            "artifactSha256": manifest["artifactSha256"],
            "tokenizerSha256": manifest["files"]["tokenizer.json"],
            "promptVersion": PROMPT_VERSION, "backend": "mlx-qwen3.5-letter-logits",
            "mlxVersion": importlib.metadata.version("mlx"),
            "mlxLmVersion": importlib.metadata.version("mlx-lm"),
            "implementationSha256": implementation_sha256(),
            "maxInputTokens": max_tokens, "quantization": "none", "allocatorCacheLimitBytes": cache_limit_bytes,
        }
        if wired_bytes is not None:
            self.identity["allocatorWiredLimitBytes"] = wired_bytes
        self.load_ms = round((time.perf_counter() - started) * 1000, 3)

    def score(self, request: Request) -> Scores:
        ids, labels = encode_request(self.tokenizer, request, self.max_tokens)
        mx = self.mx
        # Each request has a fresh forward pass, no cross-request KV/recurrent state.
        hidden = self.text_model.model(mx.array([ids]))[:, -1, :]
        weights = self.head.weight[mx.array(labels)]
        logits = (hidden @ weights.T).astype(mx.float32)[0]
        mx.eval(logits)
        return Scores(logits.tolist(), len(ids))

    def peak_memory_bytes(self) -> int:
        return int(self.mx.get_peak_memory())
