import boto3
from botocore.exceptions import ClientError
from app.core.config import settings
from app.core.logging import get_logger

log = get_logger(__name__)


def _make_client():
    kwargs = {"region_name": settings.AWS_REGION}
    if settings.AWS_ENDPOINT_URL:
        # MinIO or other S3-compatible store
        kwargs["aws_access_key_id"] = settings.AWS_ACCESS_KEY_ID
        kwargs["aws_secret_access_key"] = settings.AWS_SECRET_ACCESS_KEY
        kwargs["endpoint_url"] = settings.AWS_ENDPOINT_URL
        kwargs["config"] = boto3.session.Config(
            signature_version="s3v4",
            s3={"addressing_style": "path"},
        )
    elif settings.AWS_ACCESS_KEY_ID and settings.AWS_SECRET_ACCESS_KEY:
        if settings.AWS_ACCESS_KEY_ID != "minioadmin":
            kwargs["aws_access_key_id"] = settings.AWS_ACCESS_KEY_ID
            kwargs["aws_secret_access_key"] = settings.AWS_SECRET_ACCESS_KEY
    return boto3.client("s3", **kwargs)


_s3 = _make_client()


def ensure_bucket_exists() -> None:
    """
    Create the S3 bucket if it does not exist.
    Called at application startup.
    For real AWS S3, the bucket should be pre-created via Console or CLI.
    For local MinIO, this creates it automatically.
    """
    try:
        _s3.head_bucket(Bucket=settings.S3_BUCKET_NAME)
        log.info("s3_bucket_exists", bucket=settings.S3_BUCKET_NAME)
    except ClientError as exc:
        code = exc.response["Error"]["Code"]
        if code in ("404", "NoSuchBucket"):
            try:
                if settings.AWS_REGION == "us-east-1" or settings.AWS_ENDPOINT_URL:
                    _s3.create_bucket(Bucket=settings.S3_BUCKET_NAME)
                else:
                    _s3.create_bucket(
                        Bucket=settings.S3_BUCKET_NAME,
                        CreateBucketConfiguration={"LocationConstraint": settings.AWS_REGION},
                    )
                log.info("s3_bucket_created", bucket=settings.S3_BUCKET_NAME)
            except ClientError as create_exc:
                log.error("s3_bucket_create_failed", error=str(create_exc))
                raise
        else:
            log.error("s3_bucket_check_failed", error=str(exc))
            raise


def upload_to_s3(content: bytes, key: str, content_type: str) -> str:
    """
    Upload bytes to S3/MinIO.

    Args:
        content:      Raw file bytes.
        key:          Object key, format: "uploads/{job_id}/{filename}"
        content_type: MIME type string.

    Returns:
        S3 URI string: "s3://{bucket}/{key}"
    """
    try:
        _s3.put_object(
            Bucket=settings.S3_BUCKET_NAME,
            Key=key,
            Body=content,
            ContentType=content_type,
        )
        uri = f"s3://{settings.S3_BUCKET_NAME}/{key}"
        log.info("s3_upload_ok", key=key, size_bytes=len(content))
        return uri
    except ClientError as exc:
        log.error("s3_upload_failed", key=key, error=str(exc))
        raise


def download_from_s3(key: str) -> bytes:
    """
    Download an object from S3/MinIO and return its raw bytes.

    Args:
        key: Object key, format: "uploads/{job_id}/{filename}"
    """
    try:
        obj = _s3.get_object(Bucket=settings.S3_BUCKET_NAME, Key=key)
        content = obj["Body"].read()
        log.info("s3_download_ok", key=key, size_bytes=len(content))
        return content
    except ClientError as exc:
        log.error("s3_download_failed", key=key, error=str(exc))
        raise


def delete_from_s3(key: str) -> None:
    """Delete an object from S3/MinIO."""
    try:
        _s3.delete_object(Bucket=settings.S3_BUCKET_NAME, Key=key)
        log.info("s3_delete_ok", key=key)
    except ClientError as exc:
        log.error("s3_delete_failed", key=key, error=str(exc))
        raise
