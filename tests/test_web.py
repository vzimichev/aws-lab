from fastapi.testclient import TestClient
from aws_lab.web import main


def test_health():
    response = TestClient(main.app).get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_location(monkeypatch):
    monkeypatch.setattr(main, "get_location", lambda: {"region": "eu-central-1", "availability_zone": "eu-central-1a"})
    response = TestClient(main.app).get("/")
    assert response.status_code == 200
    assert response.json()["region"] == "eu-central-1"
