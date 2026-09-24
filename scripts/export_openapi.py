"""Write the public OpenAPI schema to docs/static/openapi.json (or the path given).

Usage:  python scripts/export_openapi.py [output-path]

No database or Redis is needed: the schema is built from the route definitions.
"""

import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

# Settings are validated at import time; these placeholders are never used to connect.
os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///:memory:")
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/0")
os.environ.setdefault("SECRET_KEY", "openapi-export-only-not-a-real-secret-0123456789")
os.environ.setdefault("APP_ENV", "development")

from app.main import create_app  # noqa: E402


def main() -> None:
    output = Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / "docs" / "static" / "openapi.json"
    output.parent.mkdir(parents=True, exist_ok=True)
    schema = create_app().openapi()
    output.write_text(json.dumps(schema, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    operations = sum(len(ops) for ops in schema["paths"].values())
    print(f"Wrote {output} ({operations} operations)")


if __name__ == "__main__":
    main()
