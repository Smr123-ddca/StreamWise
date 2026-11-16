"""Tests for application health check endpoint."""

import unittest
from fastapi.testclient import TestClient

from backend.app.main import app


class HealthCheckTestCase(unittest.TestCase):
    """Verify GET /api/health endpoint returns expected status and schema."""

    def setUp(self):
        self.client = TestClient(app)

    def test_health_check_returns_200_and_valid_json(self):
        response = self.client.get("/api/health")
        self.assertEqual(response.status_code, 200)

        data = response.json()
        self.assertEqual(data.get("application"), "StreamWise")
        self.assertEqual(data.get("status"), "healthy")
        self.assertEqual(data.get("version"), "0.1.0")


if __name__ == "__main__":
    unittest.main()
