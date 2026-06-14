"""
tests/test_producer.py

Tests for the log producer's log generation logic.
We test the shape and validity of generated logs — not the Kafka
connection itself (that's an integration concern).
"""

import sys
import os
import pytest
from datetime import datetime

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../log-producer"))

from producer import (
    build_normal_log,
    build_anomaly_log,
    generate_log,
    SERVICES,
    ENDPOINTS,
)


REQUIRED_FIELDS = {
    "timestamp", "service", "level", "method",
    "endpoint", "status_code", "latency_ms", "message", "is_anomaly"
}


class TestNormalLog:

    def test_has_all_required_fields(self):
        log = build_normal_log("auth-service")
        assert REQUIRED_FIELDS.issubset(log.keys())

    def test_valid_service(self):
        log = build_normal_log("payment-service")
        assert log["service"] == "payment-service"

    def test_valid_level(self):
        log = build_normal_log("auth-service")
        assert log["level"] in {"INFO", "WARN", "ERROR", "DEBUG"}

    def test_latency_in_normal_range(self):
        # Run many times to cover random variation
        for _ in range(50):
            log = build_normal_log("auth-service")
            assert 10 <= log["latency_ms"] <= 250, \
                f"Normal latency out of range: {log['latency_ms']}"

    def test_timestamp_is_iso_format(self):
        log = build_normal_log("auth-service")
        # Should not raise
        datetime.fromisoformat(log["timestamp"].replace("Z", "+00:00"))

    def test_is_anomaly_false(self):
        log = build_normal_log("auth-service")
        assert log["is_anomaly"] is False

    def test_endpoint_belongs_to_service(self):
        service = "payment-service"
        log = build_normal_log(service)
        assert log["endpoint"] in ENDPOINTS[service]


class TestAnomalyLog:

    def test_has_all_required_fields(self):
        log = build_anomaly_log("auth-service")
        assert REQUIRED_FIELDS.issubset(log.keys())

    def test_is_anomaly_true(self):
        log = build_anomaly_log("auth-service")
        assert log["is_anomaly"] is True

    def test_level_is_error(self):
        log = build_anomaly_log("auth-service")
        assert log["level"] == "ERROR"

    def test_latency_in_anomaly_range(self):
        for _ in range(20):
            log = build_anomaly_log("auth-service")
            assert 2000 <= log["latency_ms"] <= 8000, \
                f"Anomaly latency out of range: {log['latency_ms']}"

    def test_status_code_is_error(self):
        """
        Anomaly logs should have error-range status codes.
        This includes 429 (rate limit) which is a valid anomaly signal,
        plus all 5xx server errors.
        """
        valid_error_codes = {500, 502, 503, 504, 429}
    
        for _ in range(20):
            log = build_anomaly_log("payment-service")
            assert log["status_code"] in valid_error_codes, \
                f"Unexpected anomaly status code: {log['status_code']}"

    def test_message_contains_anomaly_marker(self):
        log = build_anomaly_log("auth-service")
        assert "[ANOMALY]" in log["message"]


class TestGenerateLog:

    def test_always_returns_valid_log(self):
        for _ in range(100):
            log = generate_log()
            assert REQUIRED_FIELDS.issubset(log.keys())
            assert log["service"] in SERVICES

    def test_anomaly_rate_roughly_correct(self):
        """
        With ANOMALY_PROB=0.05, over 1000 samples we expect ~5% anomalies.
        Allow wide margin (2%–15%) since it's random.
        """
        anomalies = sum(1 for _ in range(1000) if generate_log()["is_anomaly"])
        rate = anomalies / 1000
        assert 0.02 <= rate <= 0.15, \
            f"Anomaly rate {rate:.2%} is outside expected range 2–15%"
