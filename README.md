# AWS Image Lab — Sub-task 1

Standalone FastAPI image API for your existing AWS lab. Stores image bytes in S3 and currently derives metadata using S3 `HeadObject`. **RDS is not included yet**; Module 8 Sub-task 2 will introduce PostgreSQL metadata storage.

## Run locally

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
export S3_BUCKET=YOUR_BUCKET
export AWS_DEFAULT_REGION=eu-central-1
export SNS_TOPIC_ARN='arn:aws:sns:eu-central-1:642155085753:aws-lab-UploadsNotificationTopic'
# Optional PostgreSQL/RDS metadata storage:
export DATABASE_URL='postgresql+psycopg://USERNAME:PASSWORD@RDS_ENDPOINT:5432/images'
# Local development: use an AWS CLI profile or other supported credential provider
uvicorn app.main:app --reload
```

Swagger: http://localhost:8000/docs

## Docker

```bash
docker build -t aws-image-lab .
docker run --rm -p 8000:8000 -e S3_BUCKET=YOUR_BUCKET -e AWS_DEFAULT_REGION=eu-central-1 aws-image-lab
```

On EC2, **attach an instance profile** with `s3:PutObject`, `s3:GetObject`, `s3:DeleteObject` on `arn:aws:s3:::YOUR_BUCKET/images/*`, `s3:ListBucket` on `arn:aws:s3:::YOUR_BUCKET` (optionally constrained to the `images/` prefix), and `sns:Subscribe`, `sns:ListSubscriptionsByTopic`, and `sns:Unsubscribe` on the configured topic. boto3 uses the default credential provider chain; do not put access keys in source or environment variables. The EC2 instance needs network connectivity to S3/SNS (NAT or VPC endpoints).

## API

| Method | Path | Purpose |
|---|---|---|
| POST | `/images` | multipart form field `file`, JPEG/PNG/GIF/WebP, max 10 MiB |
| GET | `/images/{name}` | download |
| GET | `/images/{name}/metadata` | metadata |
| GET | `/images/random/metadata` | random metadata |
| DELETE | `/images/{name}` | delete |
| POST | `/notifications/subscribe` | subscribe an email to SNS notifications |
| POST | `/notifications/unsubscribe` | unsubscribe an email from SNS notifications |
| GET | `/health` | health check |

Example:

```bash
curl -F 'file=@sample.png' http://localhost:8000/images
curl http://localhost:8000/images/sample.png/metadata
curl http://localhost:8000/images/random/metadata
curl -OJ http://localhost:8000/images/sample.png
curl -X DELETE http://localhost:8000/images/sample.png
curl -X POST http://localhost:8000/notifications/subscribe \\
  -H 'Content-Type: application/json' -d '{"email":"user@example.com"}'
curl -X POST http://localhost:8000/notifications/unsubscribe \\
  -H 'Content-Type: application/json' -d '{"email":"user@example.com"}'
```

SNS sends a confirmation email after subscribing. The email address must be confirmed before SNS delivers notifications. The unsubscribe endpoint looks up the confirmed or pending email subscription in the configured topic.

Names are flat (no folders); uploading the same name overwrites the object. `last_modified` comes from S3 and is in UTC. This lab implementation validates declared MIME type and file size, but does not deeply inspect image bytes. The `/images/random/metadata` endpoint lists all keys under the prefix and is intended for lab-scale use.

When `DATABASE_URL` is configured, the application creates the `image_metadata` table
on startup and upserts metadata after an S3 upload or metadata lookup. Deleting an
image removes its metadata row as well. The application continues to support S3-only
mode when `DATABASE_URL` is not set.
