import json
import os
import unittest
from datetime import datetime, timezone
from unittest.mock import Mock, patch

os.environ.setdefault("AWS_EC2_METADATA_DISABLED", "true")
os.environ.setdefault("SQS_QUEUE_URL", "https://sqs.example.test/queue")

from app import main


class NotificationTests(unittest.TestCase):
    def test_data_consistency_endpoint_invokes_lambda_asynchronously(self):
        lambda_client = Mock()

        with patch.object(main, "lambda_client", lambda_client):
            response = main.trigger_data_consistency()

        lambda_client.invoke.assert_called_once_with(
            FunctionName="aws-lab-DataConsistencyFunction",
            InvocationType="Event",
            Payload=b"{}",
        )
        self.assertEqual(
            response,
            {
                "status": "accepted",
                "function_name": "aws-lab-DataConsistencyFunction",
            },
        )

    def test_upload_event_keeps_existing_payload_and_only_sends_to_sqs(self):
        sqs = Mock()
        metadata = main.ImageMetadata(
            name="example.png",
            size=123,
            extension="png",
            last_modified=datetime(2026, 1, 2, tzinfo=timezone.utc),
        )

        with patch.object(main, "sqs", sqs):
            main.enqueue_upload_event(metadata, "https://example.test/images/example.png")

        sqs.send_message.assert_called_once_with(
            QueueUrl="https://sqs.example.test/queue",
            MessageBody=json.dumps(
                {
                    "event": "image_uploaded",
                    "image": {
                        "name": "example.png",
                        "size": 123,
                        "extension": "png",
                        "download_url": "https://example.test/images/example.png",
                    },
                }
            ),
        )
        sqs.receive_message.assert_not_called()
        sqs.delete_message.assert_not_called()

    def test_sqs_forwarding_worker_and_hooks_are_removed(self):
        self.assertFalse(hasattr(main, "sqs_worker"))
        self.assertFalse(hasattr(main, "process_sqs_batch"))
        self.assertFalse(hasattr(main, "worker_task"))
        self.assertEqual([], main.app.router.on_shutdown)


if __name__ == "__main__":
    unittest.main()
