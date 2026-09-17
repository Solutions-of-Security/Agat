"""Fixed WASI Preview 1 host used by one-shot AGAT tool Jobs."""

from __future__ import annotations

import base64
import json
import os
from pathlib import Path
import sys

from wasmtime import Config, Engine, Linker, Module, Store, WasiConfig


def required_path(name: str) -> Path:
    value = os.environ.get(name, "")
    if not value:
        raise RuntimeError(f"missing {name}")
    return Path(value)


def bounded_bytes(path: Path, maximum: int) -> bytes:
    with path.open("rb") as source:
        payload = source.read(maximum + 1)
    if len(payload) > maximum:
        raise RuntimeError("invocation payload is too large")
    return payload


def main() -> None:
    module_path = required_path("AGAT_WASM_MODULE")
    input_bytes = bounded_bytes(required_path("AGAT_TOOL_INPUT_FILE"), 128 * 1024)
    credential_bytes = bounded_bytes(required_path("AGAT_TOOL_CREDENTIAL_FILE"), 128 * 1024)
    json.loads(input_bytes)
    json.loads(credential_bytes)

    max_output = max(1, min(4 * 1024 * 1024, int(os.environ.get("AGAT_MAX_OUTPUT_BYTES", "1048576"))))
    fuel = max(1_000_000, int(os.environ.get("AGAT_WASM_FUEL", "100000000")))
    stdout_path = Path("/tmp/agat-tool-stdout.json")
    stderr_path = Path("/tmp/agat-tool-stderr.txt")

    config = Config()
    config.consume_fuel = True
    engine = Engine(config)
    module = Module.from_file(engine, str(module_path))
    linker = Linker(engine)
    linker.define_wasi()
    wasi = WasiConfig()
    wasi.argv = ["agat-tool"]
    wasi.env = [
        ("AGAT_TOOL_INPUT_B64", base64.b64encode(input_bytes).decode("ascii")),
        ("AGAT_TOOL_CREDENTIAL_B64", base64.b64encode(credential_bytes).decode("ascii")),
    ]
    wasi.stdout_file = str(stdout_path)
    wasi.stderr_file = str(stderr_path)
    store = Store(engine)
    store.set_wasi(wasi)
    store.set_fuel(fuel)
    instance = linker.instantiate(store, module)
    start = instance.exports(store).get("_start")
    if start is None:
        raise RuntimeError("WASI module does not export _start")
    start(store)

    output = bounded_bytes(stdout_path, max_output)
    result = json.loads(output)
    encoded = json.dumps(result, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    if len(encoded) > max_output:
        raise RuntimeError("tool output is too large")
    sys.stdout.buffer.write(encoded)
    sys.stdout.buffer.write(b"\n")


if __name__ == "__main__":
    try:
        main()
    except Exception as error:  # The guest stderr is intentionally never copied to the Job log.
        print(f"WASI sandbox failed ({type(error).__name__})", file=sys.stderr)
        raise SystemExit(1)
