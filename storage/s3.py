import logging
from typing import Any

import aioboto3
from botocore.exceptions import ClientError

from exceptions import DataNotFoundError

logger = logging.getLogger(__name__)


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

    @staticmethod
    def _base_text_path(text_id: str, edition_id: str) -> str:
        return f"base_texts/{text_id}/{edition_id}.txt"

    async def store_base_text(self, text_id: str, edition_id: str, base_text: str) -> str:
        """Store base text to S3."""
        key = self._base_text_path(text_id, edition_id)
        s3 = self._client

        await s3.put_object(
            Bucket=self.bucket_name,
            Key=key,
            Body=base_text.encode("utf-8"),
            ContentType="text/plain; charset=utf-8",
            CacheControl="public, max-age=0, must-revalidate",
        )

        url = f"https://{self.bucket_name}.s3.{self.region}.amazonaws.com/{key}"
        logger.info("Uploaded base text to S3: %s", url)
        return url

    async def retrieve_base_text(self, text_id: str, edition_id: str) -> str:
        """Retrieve base text from S3."""
        key = self._base_text_path(text_id, edition_id)
        s3 = self._client

        try:
            response = await s3.get_object(Bucket=self.bucket_name, Key=key)
            data = await response["Body"].read()
            logger.info("Retrieved from S3: %s, size: %d", key, len(data))
            return data.decode("utf-8")
        except ClientError as e:
            error = e.response.get("Error", {})
            if error.get("Code") == "NoSuchKey":
                raise DataNotFoundError(f"File not found in S3: {key}") from e
            raise

    async def generate_base_text_url(self, text_id: str, edition_id: str, expires_in: int = 3600) -> str:
        """Generate a presigned URL for downloading base text directly from S3."""
        key = self._base_text_path(text_id, edition_id)
        s3 = self._client

        return await s3.generate_presigned_url(
            "get_object",
            Params={
                "Bucket": self.bucket_name,
                "Key": key,
                "ResponseContentType": "text/plain; charset=utf-8",
            },
            ExpiresIn=expires_in,
        )

    async def delete_base_text(self, text_id: str, edition_id: str) -> None:
        """Delete base text from S3."""
        key = self._base_text_path(text_id, edition_id)
        s3 = self._client

        await s3.delete_object(Bucket=self.bucket_name, Key=key)
        logger.info("Deleted from S3: %s", key)

    @staticmethod
    def _recording_path(edition_id: str, recording_id: str, extension: str) -> str:
        return f"recordings/{edition_id}/{recording_id}.{extension}"

    async def store_recording(
        self,
        edition_id: str,
        recording_id: str,
        extension: str,
        audio: bytes,
        content_type: str,
    ) -> str:
        """Store a recording's audio to S3."""
        key = self._recording_path(edition_id, recording_id, extension)
        s3 = self._client

        await s3.put_object(
            Bucket=self.bucket_name,
            Key=key,
            Body=audio,
            ContentType=content_type,
        )

        url = f"https://{self.bucket_name}.s3.{self.region}.amazonaws.com/{key}"
        logger.info("Uploaded recording to S3: %s, size: %d", url, len(audio))
        return url

    async def delete_recording(self, edition_id: str, recording_id: str, extension: str) -> None:
        """Delete a recording's audio from S3."""
        key = self._recording_path(edition_id, recording_id, extension)
        s3 = self._client

        await s3.delete_object(Bucket=self.bucket_name, Key=key)
        logger.info("Deleted from S3: %s", key)

    async def generate_recording_url(
        self,
        edition_id: str,
        recording_id: str,
        extension: str,
        expires_in: int = 3600,
    ) -> str:
        """Generate a presigned URL for downloading a recording's audio."""
        key = self._recording_path(edition_id, recording_id, extension)
        s3 = self._client

        return await s3.generate_presigned_url(
            "get_object",
            Params={"Bucket": self.bucket_name, "Key": key},
            ExpiresIn=expires_in,
        )

    async def apply_insert(self, text_id: str, edition_id: str, position: int, text: str) -> str:
        """Insert text at the specified position."""
        current_text = await self.retrieve_base_text(text_id, edition_id)
        updated_text = current_text[:position] + text + current_text[position:]
        return await self.store_base_text(text_id, edition_id, updated_text)

    async def apply_delete(self, text_id: str, edition_id: str, start: int, end: int) -> str:
        """Delete text in the specified range [start, end)."""
        current_text = await self.retrieve_base_text(text_id, edition_id)
        updated_text = current_text[:start] + current_text[end:]
        return await self.store_base_text(text_id, edition_id, updated_text)

    async def apply_replace(self, text_id: str, edition_id: str, start: int, end: int, text: str) -> str:
        """Replace text in the specified range [start, end) with new text."""
        current_text = await self.retrieve_base_text(text_id, edition_id)
        updated_text = current_text[:start] + text + current_text[end:]
        return await self.store_base_text(text_id, edition_id, updated_text)

    async def rollback_base_text(self, text_id: str, edition_id: str) -> None:
        """Rollback to previous version (requires S3 versioning enabled)."""
        key = self._base_text_path(text_id, edition_id)
        s3 = self._client

        versions = await s3.list_object_versions(Bucket=self.bucket_name, Prefix=key)
        version_list = versions.get("Versions", [])

        if len(version_list) < 2:
            logger.warning("No previous version available to rollback for: %s", key)
            return

        version_list.sort(key=lambda v: v.get("LastModified", 0), reverse=True)
        previous_version = version_list[1]

        await s3.copy_object(
            Bucket=self.bucket_name,
            CopySource={"Bucket": self.bucket_name, "Key": key, "VersionId": previous_version.get("VersionId", "")},
            Key=key,
        )
        logger.info("Rolled back %s to version %s", key, previous_version.get("VersionId", ""))
