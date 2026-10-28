import json
import os
import unittest
from unittest.mock import Mock, call, patch

os.environ.setdefault("AWS_EC2_METADATA_DISABLED", "true")
os.environ.setdefault("SNS_TOPIC_ARN", "arn:aws:sns:eu-central-1:123456789012:topic")

import lambda_function


class LambdaNotificationTests(unittest.TestCase):
    def test_publishes_each_sqs_record_body(self):
        publish = Mock(
            side_effect=[
                {"MessageId": "sns-message-1"},
                {"MessageId": "sns-message-2"},
            ]
        )
        event = {
            "Records": [
                {"messageId": "sqs-message-1", "body": json.dumps({"image": "one"})},
                {"messageId": "sqs-message-2", "body": json.dumps({"image": "two"})},
            ]
        }

        with patch.object(lambda_function.sns, "publish", publish):
            lambda_function.lambda_handler(event, None)

        publish.assert_has_calls(
            [
                call(TopicArn=lambda_function.SNS_TOPIC_ARN, Message=event["Records"][0]["body"]),
                call(TopicArn=lambda_function.SNS_TOPIC_ARN, Message=event["Records"][1]["body"]),
            ]
        )
        self.assertEqual(2, publish.call_count)

    def test_publish_failure_propagates_for_sqs_retry(self):
        publish = Mock(side_effect=RuntimeError("SNS unavailable"))
        event = {"Records": [{"messageId": "sqs-message-1", "body": "payload"}]}

        with patch.object(lambda_function.sns, "publish", publish):
            with self.assertRaisesRegex(RuntimeError, "SNS unavailable"):
                lambda_function.lambda_handler(event, None)


if __name__ == "__main__":
    unittest.main()
