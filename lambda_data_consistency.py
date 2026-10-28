"""Validate image metadata in PostgreSQL against objects in S3."""

import json
import os
from typing import Any

import boto3
import psycopg
from botocore.exceptions import ClientError


BUCKET_NAME = os.environ["S3_BUCKET"]
S3_PREFIX = os.getenv("S3_PREFIX", "images/").strip("/") + "/"
DATABASE_URL_SECRET_ARN = os.environ["DATABASE_URL_SECRET_ARN"]

s3 = boto3.client("s3")
secretsmanager = boto3.client("secretsmanager")


def _database_url() -> str:
    response = secretsmanager.get_secret_value(SecretId=DATABASE_URL_SECRET_ARN)
    secret = response.get("SecretString", "")
    try:
        value = json.loads(secret)
    except json.JSONDecodeError:
        return secret.replace("postgresql+psycopg://", "postgresql://", 1)
    if isinstance(value, dict) and isinstance(value.get("database_url"), str):
        secret_url = value["database_url"]
    else:
        raise ValueError("Database secret must contain a database_url string")
    return secret_url.replace("postgresql+psycopg://", "postgresql://", 1)


# Keep the connection outside the handler so warm Lambda invocations can reuse it.
connection = psycopg.connect(_database_url(), connect_timeout=5)


def _check_image(image_name: str, expected_size: int | None) -> dict[str, Any]:
    key = S3_PREFIX + image_name
    try:
        response = s3.head_object(Bucket=BUCKET_NAME, Key=key)
    except ClientError as exc:
        error_code = exc.response.get("Error", {}).get("Code", "")
        if error_code in {"404", "NoSuchKey", "NotFound"}:
            return {
                "image_name": image_name,
                "exists_in_s3": False,
                "consistent": False,
            }
        raise

    actual_size = response["ContentLength"]
    return {
        "image_name": image_name,
        "exists_in_s3": True,
        "db_size_bytes": expected_size,
        "s3_size_bytes": actual_size,
        "consistent": expected_size is None or expected_size == actual_size,
    }


def lambda_handler(event: dict[str, Any], context: Any) -> dict[str, Any]:
    image_name = event.get("image_name")
    with connection.cursor() as cursor:
        if image_name:
            cursor.execute(
                "SELECT name, size_bytes FROM image_metadata WHERE name = %s",
                (image_name,),
            )
            rows = cursor.fetchall()
            checks = [_check_image(image_name, rows[0][1] if rows else None)]
        else:
            cursor.execute("SELECT name, size_bytes FROM image_metadata ORDER BY name")
            checks = [_check_image(name, size) for name, size in cursor.fetchall()]

    mismatches = [check for check in checks if not check["consistent"]]
    result = {
        "consistent": not mismatches,
        "checked": len(checks),
        "mismatches": mismatches,
    }
    if image_name:
        result["image_name"] = image_name

    return {"statusCode": 200, "body": json.dumps(result)}
