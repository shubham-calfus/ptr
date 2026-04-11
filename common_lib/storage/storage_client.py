from __future__ import annotations

import os
from enum import Enum
from typing import Any

import boto3
from botocore.exceptions import BotoCoreError, ClientError


class RetrievalMode(str, Enum):
    FULL_OBJECT = "full_object"


def _normalize_endpoint(endpoint: str | None) -> str | None:
    if not endpoint:
        return None
    if "://" in endpoint:
        return endpoint
    return f"http://{endpoint}"


class StorageService:
    def __init__(self) -> None:
        self.client: Any | None = None

    def init_client(self) -> Any:
        if self.client is not None:
            return self.client

        endpoint = (
            os.environ.get("STORAGE_ENDPOINT")
            or os.environ.get("MINIO_ENDPOINT")
            or os.environ.get("AWS_ENDPOINT_URL")
        )
        access_key = (
            os.environ.get("STORAGE_ACCESS_KEY")
            or os.environ.get("MINIO_ACCESS_KEY")
            or os.environ.get("AWS_ACCESS_KEY_ID")
        )
        secret_key = (
            os.environ.get("STORAGE_SECRET_KEY")
            or os.environ.get("MINIO_SECRET_KEY")
            or os.environ.get("AWS_SECRET_ACCESS_KEY")
        )
        region = os.environ.get("AWS_DEFAULT_REGION", "us-east-1")

        kwargs: dict[str, Any] = {"region_name": region}
        normalized_endpoint = _normalize_endpoint(endpoint)
        if normalized_endpoint:
            kwargs["endpoint_url"] = normalized_endpoint
        if access_key and secret_key:
            kwargs["aws_access_key_id"] = access_key
            kwargs["aws_secret_access_key"] = secret_key

        try:
            self.client = boto3.client("s3", **kwargs)
        except (BotoCoreError, Exception) as exc:  # pragma: no cover - runtime path
            raise RuntimeError(
                "Failed to initialize S3/MinIO client. "
                f"endpoint={normalized_endpoint!r}, region={region!r}, "
                f"has_access_key={bool(access_key)}, has_secret_key={bool(secret_key)}"
            ) from exc

        if self.client is None:
            raise RuntimeError(
                "S3/MinIO client initialization returned None unexpectedly. "
                f"endpoint={normalized_endpoint!r}, region={region!r}"
            )

        return self.client

    def ensure_bucket_exists(self, bucket_name: str) -> None:
        client = self.init_client()
        try:
            client.head_bucket(Bucket=bucket_name)
            return
        except ClientError as exc:
            error_code = exc.response.get("Error", {}).get("Code", "")
            if error_code not in {"404", "NoSuchBucket", "NotFound"}:
                raise

        create_kwargs: dict[str, Any] = {"Bucket": bucket_name}
        region = os.environ.get("AWS_DEFAULT_REGION", "us-east-1")
        endpoint = (
            os.environ.get("STORAGE_ENDPOINT")
            or os.environ.get("MINIO_ENDPOINT")
            or os.environ.get("AWS_ENDPOINT_URL")
        )
        if region != "us-east-1" and not endpoint:
            create_kwargs["CreateBucketConfiguration"] = {"LocationConstraint": region}
        client.create_bucket(**create_kwargs)

    def store_object(
        self,
        bucket_name: str,
        object_key: str,
        data: bytes,
        content_type: str = "application/octet-stream",
    ) -> tuple[str, str]:
        client = self.init_client()
        client.put_object(Bucket=bucket_name, Key=object_key, Body=data, ContentType=content_type)
        return bucket_name, object_key


storage = StorageService()
