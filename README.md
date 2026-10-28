# AWS Image Lab — Module 10, Sub-task 1

FastAPI image API for the AWS lab. Image bytes are stored in S3, metadata is stored in RDS when configured, and upload notifications use this flow:

```text
FastAPI -> SQS -> Lambda -> SNS -> email
```

The FastAPI application only sends the existing JSON upload event to SQS. Lambda publishes that message body to SNS. SQS/Lambda delivery is at-least-once, so downstream processing must tolerate duplicate notifications.

## Run locally

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
export S3_BUCKET=YOUR_BUCKET
export AWS_DEFAULT_REGION=eu-central-1
export SNS_TOPIC_ARN='arn:aws:sns:eu-central-1:ACCOUNT_ID:aws-lab-UploadsNotificationTopic'
export SQS_QUEUE_URL='https://sqs.eu-central-1.amazonaws.com/ACCOUNT_ID/YOUR_QUEUE_NAME'
export APP_BASE_URL='http://YOUR_PUBLIC_HOST:8000'
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

On EC2, **attach an instance profile** with `s3:PutObject`, `s3:GetObject`, `s3:DeleteObject` on `arn:aws:s3:::YOUR_BUCKET/images/*`, `s3:ListBucket` on `arn:aws:s3:::YOUR_BUCKET` (optionally constrained to the `images/` prefix), and `sqs:SendMessage` on the configured queue. SNS subscription management uses `sns:Subscribe`, `sns:ListSubscriptionsByTopic`, and `sns:Unsubscribe`. Lambda uses the managed basic-logging and SQS execution policies plus a narrowly scoped `sns:Publish` permission for the existing topic. boto3 uses the default credential provider chain; do not put access keys in source or environment variables.

## CloudFormation

`cloudformation/module9-sns-sqs.yaml` remains the source of the existing SQS queue and SNS topic. Its queue visibility timeout is 180 seconds, which is at least six times the Lambda timeout. The EC2 policy only sends messages and manages subscriptions; it no longer consumes SQS messages or publishes to SNS.

`cloudformation/module10-serverless.yaml` creates only the Module 10 resources and accepts the existing queue and topic ARNs as parameters:

- `AWS::IAM::Role` with basic Lambda logging, SQS receive/delete/get-attributes, and topic-scoped SNS publish permissions.
- `AWS::Lambda::Function` using Python 3.13, x86_64, a 30-second timeout, and handler `lambda_function.lambda_handler`.
- `AWS::Lambda::EventSourceMapping` for the existing queue with batch size 1.

### Data consistency Lambda

The data consistency workflow is managed by these CloudFormation templates:

- `cloudformation/module10-database-secret.yaml` stores the existing PostgreSQL `DATABASE_URL` in Secrets Manager.
- `cloudformation/module10-lambda-vpc-ep.yaml` creates the S3 Gateway Endpoint, Secrets Manager Interface Endpoint, Lambda security group, and RDS ingress.
- `cloudformation/module10-data-consistency.yaml` manages the imported `aws-lab-DataConsistencyFunction` and `aws-lab-DataConsistencyLambdaRole`.

The Lambda package is built from `lambda_data_consistency.py` with `lambda_requirements.txt` for Python 3.13 x86_64, uploaded to S3, and referenced by `LambdaCodeBucket`/`LambdaCodeKey`. The function reads its database URL from Secrets Manager, initializes the PostgreSQL connection outside the handler, and compares `image_metadata` rows with objects under the configured S3 prefix.

The existing manual Lambda and IAM role were imported into the `aws-lab-module10-data-consistency` stack. Do not create another function with the same name; update the stack and change `DeploymentVersion` whenever the package changes.

The Lambda, role, and mapping have `Retain` deletion policies because a manually-created Lambda already exists in the account. Do not run a normal create/update with the default function name until the existing function has been imported or a replacement has been explicitly approved.

### Package and validate Lambda

Run these commands from the repository root in `eu-central-1`. They upload only the Lambda deployment package; they do not deploy CloudFormation.

```bash
export AWS_REGION=eu-central-1
export ARTIFACT_BUCKET=YOUR_LAMBDA_ARTIFACT_BUCKET
export ARTIFACT_KEY=aws-lab/module10/lambda_function.zip

rm -f lambda_function.zip
zip -j lambda_function.zip lambda_function.py
aws s3 cp lambda_function.zip "s3://${ARTIFACT_BUCKET}/${ARTIFACT_KEY}" --region "$AWS_REGION"
aws cloudformation validate-template \
  --template-body file://cloudformation/module10-serverless.yaml \
  --region "$AWS_REGION"
```

### Controlled deployment

First inspect the existing manually-created resources. Do not delete or replace them automatically:

```bash
export LAMBDA_FUNCTION_NAME=aws-lab-UploadsNotificationFunction
export LAMBDA_ROLE_NAME=YOUR_EXISTING_LAMBDA_ROLE_NAME

aws lambda get-function-configuration \
  --function-name "$LAMBDA_FUNCTION_NAME" --region "$AWS_REGION"
aws lambda list-event-source-mappings \
  --function-name "$LAMBDA_FUNCTION_NAME" --region "$AWS_REGION"
aws iam get-role --role-name "$LAMBDA_ROLE_NAME"
```

For an import, create an import change set in the CloudFormation console or with `create-change-set --change-set-type IMPORT`, using the existing function name, role name, and event source mapping identifier. Confirm that the imported resource properties match the template before executing the change set. The role's existing managed and inline policies must also match the role resource in the template.

If import is not suitable, use a controlled replacement name and cut over only after approval. The following command is intentionally parameterized with a `-v2` name so it cannot collide with the manually-created function:

```bash
export MODULE9_STACK=YOUR_EXISTING_MODULE9_STACK
export MODULE10_STACK=aws-lab-module10
export QUEUE_ARN=$(aws cloudformation describe-stacks --stack-name "$MODULE9_STACK" --query 'Stacks[0].Outputs[?OutputKey==`QueueArn`].OutputValue' --output text --region "$AWS_REGION")
export TOPIC_ARN=$(aws cloudformation describe-stacks --stack-name "$MODULE9_STACK" --query 'Stacks[0].Outputs[?OutputKey==`TopicArn`].OutputValue' --output text --region "$AWS_REGION")

aws cloudformation deploy \
  --stack-name "$MODULE10_STACK" \
  --template-file cloudformation/module10-serverless.yaml \
  --parameter-overrides \
    CodeS3Bucket="$ARTIFACT_BUCKET" \
    CodeS3Key="$ARTIFACT_KEY" \
    ExistingQueueArn="$QUEUE_ARN" \
    ExistingTopicArn="$TOPIC_ARN" \
    LambdaFunctionName="${LAMBDA_FUNCTION_NAME}-v2" \
    LambdaRoleName=aws-lab-UploadsNotificationRole-v2 \
  --capabilities CAPABILITY_NAMED_IAM \
  --region "$AWS_REGION"
```

Do not disable the old event source mapping or delete the old function until the replacement has been tested and a cutover has been approved.

### Verification

1. Deploy the Module 9 visibility-timeout update and the Module 10 Lambda stack using the controlled procedure above.
2. Upload two images through `POST /images`.
3. Check the Lambda CloudWatch log group for two successful publication messages containing SQS and SNS message IDs.
4. Confirm the SNS topic publishes the notifications and the subscribed mailbox receives them.
5. Confirm successfully processed messages leave the SQS queue. Allow for visibility timeout and Lambda polling delay.
6. Repeat checks with a failed SNS publish in a test environment and confirm the SQS message becomes visible for retry.

Run the local tests with:

```bash
python3 -m unittest discover -s tests -v
```

### Cleanup

After the lab, remove the Module 10 stack and the Lambda artifact from S3 when they are no longer needed. Review the manually-created Lambda, event source mapping, SNS subscriptions, SQS queue, RDS instance, and EC2 instance separately before deleting them; retained resources are not removed automatically by this template.

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

SNS sends a confirmation email after subscribing. The email address must be confirmed before SNS delivers notifications. The unsubscribe endpoint looks up the confirmed or pending email subscription in the configured topic. After an upload, the application puts the existing event payload on SQS. An AWS Lambda function consumes the queue and publishes the notification to SNS.

Names are flat (no folders); uploading the same name overwrites the object. `last_modified` comes from S3 and is in UTC. This lab implementation validates declared MIME type and file size, but does not deeply inspect image bytes. The `/images/random/metadata` endpoint lists all keys under the prefix and is intended for lab-scale use.

When `DATABASE_URL` is configured, the application creates the `image_metadata` table
on startup and upserts metadata after an S3 upload or metadata lookup. Deleting an
image removes its metadata row as well. The application continues to support S3-only
mode when `DATABASE_URL` is not set.
