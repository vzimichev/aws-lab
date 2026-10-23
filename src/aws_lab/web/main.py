"""Minimal HTTP API for the AWS load-balancing lab."""
from fastapi import FastAPI, HTTPException
from aws_lab.metadata import get_location

app = FastAPI(title="AWS EC2 Location")


@app.get("/")
def location() -> dict[str, str]:
    try:
        return get_location()
    except (OSError, ValueError, KeyError) as exc:
        raise HTTPException(status_code=503, detail="EC2 metadata unavailable") from exc


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}
