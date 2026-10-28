"""Forward SQS upload events to the configured SNS topic."""

import logging
import os

import boto3


logger = logging.getLogger()
logger.setLevel(logging.INFO)
sns = boto3.client("sns")
SNS_TOPIC_ARN = os.environ["SNS_TOPIC_ARN"]


def lambda_handler(event, context):
    for record in event["Records"]:
        response = sns.publish(TopicArn=SNS_TOPIC_ARN, Message=record["body"])
        logger.info(
            "Published SQS message %s to SNS message %s",
            record.get("messageId", "unknown"),
            response["MessageId"],
        )
