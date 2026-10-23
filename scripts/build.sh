#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
python3 -m pip install --quiet build
python3 -m build --wheel
mkdir -p artifacts
cp dist/*.whl artifacts/
(cd artifacts && zip -q -j aws-lab-deploy.zip ./*.whl)
echo "Artifact: artifacts/aws-lab-deploy.zip"
