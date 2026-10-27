import os
import random
from datetime import datetime
from pathlib import PurePath
from typing import Annotated

import boto3
from botocore.exceptions import ClientError
from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

BUCKET = os.getenv("S3_BUCKET", "")
PREFIX = os.getenv("S3_PREFIX", "images/").strip("/") + "/"
MAX_UPLOAD_BYTES = int(os.getenv("MAX_UPLOAD_BYTES", str(10 * 1024 * 1024)))
ALLOWED_TYPES = {"image/jpeg", "image/png", "image/gif", "image/webp"}
s3 = boto3.client("s3")  # EC2 instance profile via default AWS credential chain
app = FastAPI(title="AWS Image Lab", version="1.0.0")


class ImageMetadata(BaseModel):
    name: str
    size: int
    extension: str
    last_modified: datetime


def key_for(name: str) -> str:
    if not name or name in {".", ".."} or name != PurePath(name).name or "/" in name or "\\" in name:
        raise HTTPException(400, "Invalid image name")
    return PREFIX + name


def check_bucket() -> None:
    if not BUCKET:
        raise HTTPException(503, "S3_BUCKET environment variable is not set")


def s3_error(exc: ClientError):
    code = exc.response.get("Error", {}).get("Code", "")
    if code in {"404", "NoSuchKey", "NotFound"}:
        raise HTTPException(404, "Image not found") from exc
    raise HTTPException(502, f"S3 request failed: {code}") from exc


def metadata_for(name: str) -> ImageMetadata:
    check_bucket()
    try:
        obj = s3.head_object(Bucket=BUCKET, Key=key_for(name))
    except ClientError as exc:
        s3_error(exc)
    return ImageMetadata(name=name, size=obj["ContentLength"],
                         extension=PurePath(name).suffix.lstrip(".").lower(),
                         last_modified=obj["LastModified"])


@app.get("/health")
def health():
    return {"status": "ok"}


@app.post("/images", response_model=ImageMetadata, status_code=201)
def upload_image(file: Annotated[UploadFile, File()]):
    check_bucket()
    name = file.filename or ""
    key = key_for(name)
    if file.content_type not in ALLOWED_TYPES:
        raise HTTPException(415, "Supported types: JPEG, PNG, GIF, WebP")
    # Read one byte beyond limit to reject oversized uploads before S3 upload.
    content = file.file.read(MAX_UPLOAD_BYTES + 1)
    if len(content) > MAX_UPLOAD_BYTES:
        raise HTTPException(413, "Image exceeds upload size limit")
    if not content:
        raise HTTPException(400, "Empty file")
    try:
        s3.put_object(Bucket=BUCKET, Key=key, Body=content, ContentType=file.content_type)
    except ClientError as exc:
        s3_error(exc)
    return metadata_for(name)


@app.get("/images/random/metadata", response_model=ImageMetadata)
def random_metadata():
    check_bucket()
    # Reservoir sampling across all pages avoids fetching/storing the entire key list.
    chosen = None
    count = 0
    try:
        pages = s3.get_paginator("list_objects_v2").paginate(Bucket=BUCKET, Prefix=PREFIX)
        for page in pages:
            for item in page.get("Contents", []):
                name = item["Key"][len(PREFIX):]
                if not name or "/" in name:
                    continue
                count += 1
                if random.randrange(count) == 0:
                    chosen = name
    except ClientError as exc:
        s3_error(exc)
    if chosen is None:
        raise HTTPException(404, "No images found")
    return metadata_for(chosen)


@app.get("/images/{name}/metadata", response_model=ImageMetadata)
def get_metadata(name: str):
    return metadata_for(name)


@app.get("/images/{name}")
def download_image(name: str):
    check_bucket()
    try:
        obj = s3.get_object(Bucket=BUCKET, Key=key_for(name))
    except ClientError as exc:
        s3_error(exc)
    body = obj["Body"]

    def stream():
        try:
            for chunk in body.iter_chunks(chunk_size=64 * 1024):
                if chunk:
                    yield chunk
        finally:
            body.close()

    return StreamingResponse(stream(), media_type=obj.get("ContentType", "application/octet-stream"),
                             headers={"Content-Disposition": f'attachment; filename="{name}"'})


@app.delete("/images/{name}", status_code=204)
def delete_image(name: str):
    check_bucket()
    metadata_for(name)  # 404 if absent
    try:
        s3.delete_object(Bucket=BUCKET, Key=key_for(name))
    except ClientError as exc:
        s3_error(exc)
