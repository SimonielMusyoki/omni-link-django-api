"""Production settings entrypoint.

Loads environment variables from `.env.prod` located one level above the
project root (for example `/opt/omnilink/.env.prod` when the project lives at
`/opt/omnilink/project`).
"""

from pathlib import Path
import os


def _load_env_file(env_file: Path) -> None:
    """Load KEY=VALUE pairs into os.environ without overriding existing vars."""
    if not env_file.exists():
        return

    for raw_line in env_file.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue

        if line.startswith("export "):
            line = line[len("export ") :].strip()

        if "=" not in line:
            continue

        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")

        if key:
            os.environ.setdefault(key, value)


BASE_DIR = Path(__file__).resolve().parent.parent
ENV_FILE = BASE_DIR.parent / ".env.prod"

_load_env_file(ENV_FILE)

from .settings import *  # noqa: F401,F403,E402
