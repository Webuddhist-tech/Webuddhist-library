from typing import Any, BinaryIO

import aioboto3
from botocore.exceptions import ClientError

from exceptions import DataNotFoundError


class Storage:
    def __init__(self, bucket_name: str, region: str) -> None:
        self.bucket_name = bucket_name
        self.region = region
        self._session = aioboto3.Session()
        self._client: Any = None
        self._client_context: Any = None

    async def connect(self) -> None:
        """Initialize the S3 client connection."""
        self._client_context = self._session.client("s3", region_name=self.region)
        self._client = await self._client_context.__aenter__()

    async def close(self) -> None:
        """Close the S3 client connection."""
        if self._client_context is not None:
            await self._client_context.__aexit__(None, None, None)
            self._client = None
            self._client_context = None

    async def put_immutable(self, key: str, body: bytes | BinaryIO, content_type: str) -> None:
        """Write a fresh object; never replace published bytes. Failures may leave an unused object."""
        await self._client.put_object(
            Bucket=self.bucket_name, Key=key, Body=body, ContentType=content_type, IfNoneMatch="*"
        )

    async def read_text(self, key: str) -> str:
        try:
            response = await self._client.get_object(Bucket=self.bucket_name, Key=key)
            async with response["Body"] as body:
                return (await body.read()).decode("utf-8")
        except ClientError as exc:
            if exc.response.get("Error", {}).get("Code") == "NoSuchKey":
                raise DataNotFoundError(f"Content object '{key}' not found") from exc
            raise

    async def generate_url(self, key: str, expires_in: int = 3600) -> str:
        return await self._client.generate_presigned_url(
            "get_object",
            Params={"Bucket": self.bucket_name, "Key": key},
            ExpiresIn=expires_in,
        )
