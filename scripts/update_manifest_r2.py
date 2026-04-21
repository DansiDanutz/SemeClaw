"""
Update the SemeClaw release manifest in Cloudflare R2.

Reads the existing manifest.json from R2 (if any), appends a new update entry,
writes it back, and optionally invalidates cache.

Env vars:
    R2_ACCESS_KEY_ID       S3-compatible access key
    R2_SECRET_ACCESS_KEY   S3-compatible secret key
    R2_ENDPOINT_URL        e.g. https://<id>.r2.cloudflarestorage.com
    R2_BUCKET_NAME         e.g. semeclaw-assets
    MANIFEST_KEY           S3 object key (default: manifest.json)
"""

from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timezone


def get_boto3_client():
    try:
        import boto3
    except ImportError:
        print("boto3 is required. Install: pip install boto3", file=sys.stderr)
        raise SystemExit(1)

    return boto3.client(
        "s3",
        endpoint_url=os.environ["R2_ENDPOINT_URL"],
        aws_access_key_id=os.environ["R2_ACCESS_KEY_ID"],
        aws_secret_access_key=os.environ["R2_SECRET_ACCESS_KEY"],
    )


def read_manifest(s3, bucket: str, key: str) -> dict:
    try:
        obj = s3.get_object(Bucket=bucket, Key=key)
        return json.loads(obj["Body"].read().decode("utf-8"))
    except s3.exceptions.NoSuchKey:
        return {"updates": [], "latest": None}


def write_manifest(s3, bucket: str, key: str, manifest: dict) -> None:
    body = json.dumps(manifest, indent=2, ensure_ascii=False).encode("utf-8")
    s3.put_object(
        Bucket=bucket,
        Key=key,
        Body=body,
        ContentType="application/json",
        CacheControl="max-age=60",
    )


def build_entry(version: str, image_tag: str, release_url: str, changelog: str = "") -> dict:
    return {
        "version": version,
        "date": datetime.now(timezone.utc).isoformat(),
        "image": image_tag,
        "release_url": release_url,
        "changelog": changelog or f"Daily release {version}",
    }


def main() -> int:
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("--version", required=True, help="SemeClaw version (e.g. 0.7.1)")
    parser.add_argument("--image", required=True, help="Full Docker image URI")
    parser.add_argument("--release-url", required=True, help="GitHub release page URL")
    parser.add_argument("--changelog", default="", help="Release notes / changelog snippet")
    parser.add_argument("--manifest-key", default=os.environ.get("MANIFEST_KEY", "manifest.json"))
    args = parser.parse_args()

    bucket = os.environ["R2_BUCKET_NAME"]
    s3 = get_boto3_client()

    manifest = read_manifest(s3, bucket, args.manifest_key)

    entry = build_entry(args.version, args.image, args.release_url, args.changelog)
    manifest["updates"].insert(0, entry)
    manifest["latest"] = args.version

    # Keep last 90 entries to avoid unbounded growth
    manifest["updates"] = manifest["updates"][:90]

    write_manifest(s3, bucket, args.manifest_key, manifest)
    print(f"Manifest updated: {args.version} -> s3://{bucket}/{args.manifest_key}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
