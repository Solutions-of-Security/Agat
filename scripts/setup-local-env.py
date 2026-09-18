#!/usr/bin/env python3
"""Create a local Compose configuration without overwriting existing secrets."""
import argparse
import os
from pathlib import Path
import re
import secrets


def create_config(example: Path, output: Path, port: int, model: str) -> None:
    if not 1024 <= port <= 65535:
        raise ValueError("port must be between 1024 and 65535")
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:/@-]{0,199}", model):
        raise ValueError("invalid model name")
    values = {key: secrets.token_hex(32) for key in (
        "AGAT_ADMIN_TOKEN", "AGAT_ENROLLMENT_TOKEN", "AGAT_CREDENTIALS_KEY", "AGAT_SEARCH_SECRET"
    )}
    values.update({"AGAT_HTTP_PORT": str(port),
                   "AGAT_A2A_PUBLIC_BASE_URL": f"http://127.0.0.1:{port}",
                   "AGAT_WORKER_MODELS": model,
                   "AGAT_WEB_ENABLED": "false"})
    lines = []
    for line in example.read_text().splitlines():
        key = line.split("=", 1)[0]
        lines.append(f"{key}={values.pop(key)}" if key in values else line)
    lines.extend(f"{key}={value}" for key, value in values.items())
    descriptor = os.open(output, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "w") as stream:
        stream.write("\n".join(lines) + "\n")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=8787)
    parser.add_argument("--model", default="llama3.2:latest")
    args = parser.parse_args()
    root = Path(__file__).resolve().parent.parent
    try:
        create_config(root / ".env.example", root / ".env", args.port, args.model)
    except (FileExistsError, ValueError) as error:
        parser.exit(1, f"Configuration not written: {error}\nExisting .env files are never replaced.\n")
    print(f"Created .env with separate random secrets. Local URL: http://127.0.0.1:{args.port}")
    print("Keep .env private. Copy AGAT_ADMIN_TOKEN from that file when the UI asks for it.")


if __name__ == "__main__":
    main()
