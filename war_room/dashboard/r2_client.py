"""
Minimal R2 client for serving assets from the semeclaw-assets bucket.
"""

from __future__ import annotations

import os


def get_r2_client():
    import boto3

    return boto3.client(
        "s3",
        endpoint_url=os.environ["R2_ENDPOINT_URL"],
        aws_access_key_id=os.environ["R2_ACCESS_KEY_ID"],
        aws_secret_access_key=os.environ["R2_SECRET_ACCESS_KEY"],
    )


def generate_presigned_url(key: str, expiration: int = 3600) -> str | None:
    """Generate a presigned GET URL for an R2 object."""
    try:
        client = get_r2_client()
        bucket = os.environ["R2_BUCKET_NAME"]
        return client.generate_presigned_url(
            "get_object",
            Params={"Bucket": bucket, "Key": key},
            ExpiresIn=expiration,
        )
    except Exception:
        return None
