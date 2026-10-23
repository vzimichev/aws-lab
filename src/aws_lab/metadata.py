"""Retrieve AWS EC2 location from IMDSv2."""
import json
from urllib.request import Request, urlopen

IMDS = "http://169.254.169.254/latest"


def get_location() -> dict[str, str]:
    token_request = Request(
        f"{IMDS}/api/token",
        method="PUT",
        headers={"X-aws-ec2-metadata-token-ttl-seconds": "60"},
    )
    with urlopen(token_request, timeout=2) as response:
        token = response.read().decode("utf-8")

    document_request = Request(
        f"{IMDS}/dynamic/instance-identity/document",
        headers={"X-aws-ec2-metadata-token": token},
    )
    with urlopen(document_request, timeout=2) as response:
        document = json.load(response)

    return {
        "region": document["region"],
        "availability_zone": document["availabilityZone"],
    }
