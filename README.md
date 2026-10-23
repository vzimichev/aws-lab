# AWS EC2 Location API

Minimal Python/FastAPI project for the AWS lab Sub-task 4. The `/` endpoint reads the EC2 region and availability zone using IMDSv2. `/health` is a metadata-independent health check for the Application Load Balancer.

## Requirements

Python 3.9+; run the location endpoint on an EC2 instance with IMDS enabled.

## Local setup

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e .
uvicorn aws_lab.web.main:app --host 0.0.0.0 --port 8000
```

- `http://localhost:8000/health` returns `{"status":"ok"}`.
- `http://localhost:8000/` returns HTTP 503 when not running on EC2.

## Build a deployable artifact

```bash
./scripts/build.sh
```

This builds a Python wheel and packages it in `artifacts/aws-lab-deploy.zip`. The wheel contains application code, with dependencies declared in `pyproject.toml`. **Dependencies are installed during deployment**, so the ZIP is not a fully self-contained runtime bundle.

Upload the artifact to S3:

```bash
aws s3 cp artifacts/aws-lab-deploy.zip s3://YOUR_BUCKET/releases/aws-lab-deploy.zip --profile userA
```

## Deploy on Amazon Linux 2023 EC2

The instance needs an IAM role with `s3:GetObject` for the artifact and outbound network access to S3 and Python package repositories.

```bash
sudo dnf install -y python3 python3-pip unzip
aws s3 cp s3://YOUR_BUCKET/releases/aws-lab-deploy.zip /tmp/aws-lab-deploy.zip
mkdir -p /tmp/aws-lab-wheels
unzip -o /tmp/aws-lab-deploy.zip -d /tmp/aws-lab-wheels
sudo python3 -m pip install /tmp/aws-lab-wheels/*.whl
```

On some Amazon Linux environments, installing into the system Python may be restricted. For a more reliable deployment, create a dedicated virtual environment and install the wheel there:

```bash
sudo python3 -m venv /opt/aws-lab-venv
sudo /opt/aws-lab-venv/bin/pip install /tmp/aws-lab-wheels/*.whl
```

Run:

```bash
/opt/aws-lab-venv/bin/uvicorn aws_lab.web.main:app --host 0.0.0.0 --port 8000
```

For a persistent production-like deployment, configure a systemd service. Configure ALB target group health checks on `/health` and port `8000`, and permit port 8000 only from the ALB security group.

## Architecture

Shared metadata code lives in `src/aws_lab/metadata.py`, so a later second artifact can reuse it.
