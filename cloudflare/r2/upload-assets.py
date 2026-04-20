#!/usr/bin/env python3
"""Upload SemeClaw assets to Cloudflare R2.

Usage:
    export AWS_ACCESS_KEY_ID=your-r2-access-key
    export AWS_SECRET_ACCESS_KEY=your-r2-secret-key
    export R2_ENDPOINT_URL=https://<account-id>.r2.cloudflarestorage.com
    python upload-assets.py
"""

from __future__ import annotations

import hashlib
import mimetypes
import os
import sys
from pathlib import Path
from typing import Any

# Optional: boto3 for S3-compatible upload
try:
    import boto3
    from botocore.config import Config
except ImportError:
    print("boto3 is required. Install: pip install boto3")
    sys.exit(1)


BUCKET_NAME = "semeclaw-assets"
ASSETS_DIR = Path(__file__).parent.parent.parent / "assets"
PUBLIC_BASE = "https://assets.semeclaw.com"

# Content-Type overrides
MIME_OVERRIDES: dict[str, str] = {
    ".mp3": "audio/mpeg",
    ".mp4": "video/mp4",
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".svg": "image/svg+xml",
    ".webp": "image/webp",
}


def get_s3_client() -> Any:
    """Create an S3 client configured for Cloudflare R2."""
    endpoint = os.environ.get("R2_ENDPOINT_URL")
    if not endpoint:
        raise RuntimeError("R2_ENDPOINT_URL environment variable is required")

    return boto3.client(
        "s3",
        endpoint_url=endpoint,
        aws_access_key_id=os.environ.get("AWS_ACCESS_KEY_ID"),
        aws_secret_access_key=os.environ.get("AWS_SECRET_ACCESS_KEY"),
        region_name="auto",
        config=Config(
            max_pool_connections=20,
            retries={"max_attempts": 3, "mode": "adaptive"},
        ),
    )


def sha256_file(path: Path) -> str:
    """Compute SHA-256 hex digest of a file."""
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            h.update(chunk)
    return h.hexdigest()


def upload_asset(client: Any, local_path: Path, key: str) -> None:
    """Upload a single file to R2."""
    ext = local_path.suffix.lower()
    content_type = MIME_OVERRIDES.get(ext) or mimetypes.guess_type(str(local_path))[0] or "application/octet-stream"

    checksum = sha256_file(local_path)

    print(f"  → {key} ({content_type}) sha256={checksum[:16]}...")

    extra_args: dict[str, Any] = {
        "ContentType": content_type,
        "Metadata": {"sha256": checksum},
    }

    # Cache immutable assets aggressively
    if ext in {".png", ".jpg", ".jpeg", ".svg", ".webp", ".mp3", ".mp4", ".css", ".js"}:
        extra_args["CacheControl"] = "public, max-age=31536000, immutable"

    client.upload_file(str(local_path), BUCKET_NAME, key, ExtraArgs=extra_args)


def main() -> int:
    print(f"Uploading assets from {ASSETS_DIR} to R2 bucket '{BUCKET_NAME}'")

    if not ASSETS_DIR.exists():
        print(f"ERROR: Assets directory not found: {ASSETS_DIR}")
        return 1

    client = get_s3_client()

    uploaded = 0
    skipped = 0

    for local_path in sorted(ASSETS_DIR.rglob("*")):
        if not local_path.is_file():
            continue

        relative = local_path.relative_to(ASSETS_DIR)
        key = str(relative).replace("\\", "/")

        # Skip source-control and temp files
        if local_path.name.startswith(".") or local_path.name.endswith("~"):
            skipped += 1
            continue

        upload_asset(client, local_path, key)
        uploaded += 1

    print(f"\nDone. Uploaded: {uploaded}, skipped: {skipped}")
    print(f"Public base URL: {PUBLIC_BASE}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
