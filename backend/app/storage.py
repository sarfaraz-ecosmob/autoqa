"""Pluggable artifact storage (spec: S3-compatible; local FS driver for now).

Artifacts (screenshots, traces, videos, reports) are addressed by a stable
storage key; the driver maps keys to bytes. Switch drivers via
AUTOQA_STORAGE_DRIVER=fs|s3 without touching call sites.
"""
import hashlib
import io
import os
from typing import BinaryIO

from app.config import get_settings


class StorageError(RuntimeError):
    pass


def _driver() -> str:
    return get_settings().storage_driver.lower()


def _ensure_dir(path: str) -> None:
    os.makedirs(path, exist_ok=True)


def _fs_path(key: str) -> str:
    settings = get_settings()
    safe = key.lstrip("/").replace("..", "_")
    return os.path.join(settings.storage_dir, safe)


def put_bytes(key: str, data: bytes, content_type: str = "application/octet-stream") -> str:
    """Store bytes under key; returns the storage key."""
    if _driver() == "s3":
        import boto3

        settings = get_settings()
        client = boto3.client(
            "s3",
            endpoint_url=settings.s3_endpoint_url,
            aws_access_key_id=settings.s3_access_key,
            aws_secret_access_key=settings.s3_secret_key,
            region_name=settings.s3_region,
        )
        client.put_object(Bucket=settings.s3_bucket, Key=key, Body=data, ContentType=content_type)
        return key

    path = _fs_path(key)
    _ensure_dir(os.path.dirname(path))
    with open(path, "wb") as f:
        f.write(data)
    return key


def put_file(key: str, path: str, content_type: str = "application/octet-stream") -> str:
    with open(path, "rb") as f:
        return put_bytes(key, f.read(), content_type)


def get_bytes(key: str) -> bytes:
    if _driver() == "s3":
        import boto3

        settings = get_settings()
        client = boto3.client(
            "s3",
            endpoint_url=settings.s3_endpoint_url,
            aws_access_key_id=settings.s3_access_key,
            aws_secret_access_key=settings.s3_secret_key,
            region_name=settings.s3_region,
        )
        resp = client.get_object(Bucket=settings.s3_bucket, Key=key)
        return resp["Body"].read()

    path = _fs_path(key)
    if not os.path.exists(path):
        raise StorageError(f"artifact not found: {key}")
    with open(path, "rb") as f:
        return f.read()


def delete(key: str) -> None:
    if _driver() == "s3":
        import boto3

        settings = get_settings()
        client = boto3.client(
            "s3",
            endpoint_url=settings.s3_endpoint_url,
            aws_access_key_id=settings.s3_access_key,
            aws_secret_access_key=settings.s3_secret_key,
            region_name=settings.s3_region,
        )
        client.delete_object(Bucket=settings.s3_bucket, Key=key)
        return

    path = _fs_path(key)
    if os.path.exists(path):
        os.remove(path)


def exists(key: str) -> bool:
    if _driver() == "s3":
        import boto3

        settings = get_settings()
        client = boto3.client(
            "s3",
            endpoint_url=settings.s3_endpoint_url,
            aws_access_key_id=settings.s3_access_key,
            aws_secret_access_key=settings.s3_secret_key,
            region_name=settings.s3_region,
        )
        try:
            client.head_object(Bucket=settings.s3_bucket, Key=key)
            return True
        except client.exceptions.ClientError:
            return False

    return os.path.exists(_fs_path(key))


def checksum(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()
