import asyncio
import contextlib
import json
import logging
import os
import random
from datetime import datetime
from pathlib import PurePath
from typing import Annotated
from urllib.parse import quote

import boto3
from botocore.exceptions import ClientError
from fastapi import FastAPI, File, HTTPException, Request, UploadFile
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlalchemy.exc import SQLAlchemyError

from app.db import delete_metadata, initialize_database, save_metadata

logger = logging.getLogger(__name__)
BUCKET = os.getenv("S3_BUCKET", "")
PREFIX = os.getenv("S3_PREFIX", "images/").strip("/") + "/"
MAX_UPLOAD_BYTES = int(os.getenv("MAX_UPLOAD_BYTES", str(10 * 1024 * 1024)))
ALLOWED_TYPES = {"image/jpeg", "image/png", "image/gif", "image/webp"}
SQS_QUEUE_URL = os.getenv("SQS_QUEUE_URL", "")
APP_BASE_URL = os.getenv("APP_BASE_URL", "").rstrip("/")
SQS_POLL_INTERVAL_SECONDS = int(os.getenv("SQS_POLL_INTERVAL_SECONDS", "5"))
SNS_TOPIC_ARN = os.getenv(
    "SNS_TOPIC_ARN",
    "arn:aws:sns:eu-central-1:642155085753:aws-lab-UploadsNotificationTopic",
)
s3 = boto3.client("s3")  # EC2 instance profile via default AWS credential chain
sqs = boto3.client("sqs")
sns = boto3.client("sns")
app = FastAPI(title="AWS Image Lab", version="1.0.0")
worker_task: asyncio.Task | None = None


@app.on_event("startup")
def startup() -> None:
    initialize_database()


@app.on_event("startup")
async def start_sqs_worker() -> None:
    global worker_task
    if SQS_QUEUE_URL:
        worker_task = asyncio.create_task(sqs_worker())
    else:
        logger.warning("SQS_QUEUE_URL is not set; upload notifications are disabled")


@app.on_event("shutdown")
async def stop_sqs_worker() -> None:
    if worker_task is not None:
        worker_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await worker_task


class ImageMetadata(BaseModel):
    name: str
    size: int
    extension: str
    last_modified: datetime


class NotificationEmail(BaseModel):
    email: str


def check_queue() -> None:
    if not SQS_QUEUE_URL:
        raise HTTPException(503, "SQS_QUEUE_URL environment variable is not set")


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


def sns_error(exc: ClientError):
    code = exc.response.get("Error", {}).get("Code", "")
    if code in {"InvalidParameter", "InvalidParameterValue"}:
        raise HTTPException(400, "Invalid notification subscription request") from exc
    raise HTTPException(502, f"SNS request failed: {code}") from exc


def queue_error(exc: ClientError):
    code = exc.response.get("Error", {}).get("Code", "")
    raise HTTPException(502, f"SQS request failed: {code}") from exc


def download_url(request: Request, name: str) -> str:
    base_url = APP_BASE_URL or str(request.base_url).rstrip("/")
    return f"{base_url}/images/{quote(name, safe='')}"


def enqueue_upload_event(metadata: ImageMetadata, url: str) -> None:
    check_queue()
    message = {
        "event": "image_uploaded",
        "image": {
            "name": metadata.name,
            "size": metadata.size,
            "extension": metadata.extension,
            "download_url": url,
        },
    }
    try:
        sqs.send_message(QueueUrl=SQS_QUEUE_URL, MessageBody=json.dumps(message))
    except ClientError as exc:
        queue_error(exc)


def notification_text(message: dict) -> str:
    image = message["image"]
    return "\n".join(
        [
            "An image has been uploaded to the web application.",
            f"Name: {image['name']}",
            f"Size: {image['size']} bytes",
            f"Extension: {image['extension']}",
            f"Download: {image['download_url']}",
        ]
    )


def process_sqs_batch() -> None:
    response = sqs.receive_message(
        QueueUrl=SQS_QUEUE_URL,
        MaxNumberOfMessages=10,
        WaitTimeSeconds=20,
        VisibilityTimeout=60,
        MessageAttributeNames=["All"],
    )
    for message in response.get("Messages", []):
        try:
            event = json.loads(message["Body"])
            if event.get("event") != "image_uploaded":
                logger.warning("Ignoring unknown SQS event: %s", event.get("event"))
                delete_message(message["ReceiptHandle"])
                continue
            sns.publish(
                TopicArn=SNS_TOPIC_ARN,
                Subject="Image uploaded",
                Message=notification_text(event),
            )
            delete_message(message["ReceiptHandle"])
        except (KeyError, TypeError, ValueError) as exc:
            logger.error("Dropping malformed SQS message: %s", exc)
            delete_message(message["ReceiptHandle"])
        except ClientError:
            logger.exception("Could not publish SQS message to SNS; it will be retried")


def delete_message(receipt_handle: str) -> None:
    sqs.delete_message(QueueUrl=SQS_QUEUE_URL, ReceiptHandle=receipt_handle)


async def sqs_worker() -> None:
    while True:
        try:
            await asyncio.to_thread(process_sqs_batch)
        except asyncio.CancelledError:
            raise
        except ClientError:
            logger.exception("SQS polling failed; retrying")
        except Exception:
            logger.exception("Unexpected SQS worker error; retrying")
        await asyncio.sleep(SQS_POLL_INTERVAL_SECONDS)


def metadata_for(name: str) -> ImageMetadata:
    check_bucket()
    try:
        obj = s3.head_object(Bucket=BUCKET, Key=key_for(name))
    except ClientError as exc:
        s3_error(exc)
    metadata = ImageMetadata(
        name=name,
        size=obj["ContentLength"],
        extension=PurePath(name).suffix.lstrip(".").lower(),
        last_modified=obj["LastModified"],
    )
    try:
        save_metadata(
            name=metadata.name,
            size=metadata.size,
            extension=metadata.extension,
            last_modified=metadata.last_modified,
        )
    except (RuntimeError, SQLAlchemyError) as exc:
        raise HTTPException(503, "Image metadata database is unavailable") from exc
    return metadata


@app.get("/health")
def health():
    return {"status": "ok"}


@app.post("/notifications/subscribe", status_code=201)
def subscribe_email(notification: NotificationEmail):
    try:
        result = sns.subscribe(
            TopicArn=SNS_TOPIC_ARN,
            Protocol="email",
            Endpoint=notification.email,
            ReturnSubscriptionArn=True,
        )
    except ClientError as exc:
        sns_error(exc)
    return {
        "email": notification.email,
        "subscription_arn": result.get("SubscriptionArn"),
        "message": "Subscription request sent. Confirm it from the email inbox.",
    }


@app.post("/notifications/unsubscribe")
def unsubscribe_email(notification: NotificationEmail):
    try:
        pages = sns.get_paginator("list_subscriptions_by_topic").paginate(
            TopicArn=SNS_TOPIC_ARN
        )
        subscription_arn = next(
            (
                subscription["SubscriptionArn"]
                for page in pages
                for subscription in page.get("Subscriptions", [])
                if subscription.get("Protocol") == "email"
                and subscription.get("Endpoint", "").casefold()
                == notification.email.casefold()
            ),
            None,
        )
        if subscription_arn is None:
            raise HTTPException(404, "No SNS subscription found for this email")
        sns.unsubscribe(SubscriptionArn=subscription_arn)
    except ClientError as exc:
        sns_error(exc)
    return {"email": notification.email, "message": "Email unsubscribed"}


@app.post("/images", response_model=ImageMetadata, status_code=201)
def upload_image(request: Request, file: Annotated[UploadFile, File()]):
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
    metadata = metadata_for(name)
    enqueue_upload_event(metadata, download_url(request, name))
    return metadata


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
    try:
        delete_metadata(name)
    except (RuntimeError, SQLAlchemyError) as exc:
        raise HTTPException(503, "Image metadata database is unavailable") from exc
