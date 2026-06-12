"""
tests/test_ml_service.py

Unit tests for the anomaly detection service.
These run in Jenkins Stage 2 before any Docker build happens.
If any test fails, the pipeline stops — no broken image gets built.
"""

import sys
import os
import pytest
import numpy as np

# Add ml-service to path so we can import app.py directly
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../ml-service"))

from app import (
    extract_features,
    AnomalyDetector,
    generate_synthetic_normal_data,
    LEVEL_MAP,
    METHOD_MAP,
    SERVICE_MAP,
)


# ── Feature extraction tests ─────────────────────────────────────────

class TestExtractFeatures:

    def test_returns_correct_length(self):
        log = {
            "service": "auth-service", "level": "INFO",
            "method": "GET", "endpoint": "/login",
            "status_code": 200, "latency_ms": 120,
        }
        features = extract_features(log)
        assert len(features) == 7, "Feature vector must have exactly 7 elements"

    def test_normal_log_features(self):
        log = {
            "service": "auth-service", "level": "INFO",
            "method": "GET", "status_code": 200, "latency_ms": 100,
        }
        features = extract_features(log)
        latency, status, level, method, service, is_5xx, is_err = features

        assert latency == 100.0
        assert status == 200
        assert level == LEVEL_MAP["INFO"]     # 0
        assert method == METHOD_MAP["GET"]    # 0
        assert is_5xx == 0.0
        assert is_err == 0.0

    def test_anomalous_log_features(self):
        log = {
            "service": "payment-service", "level": "ERROR",
            "method": "POST", "status_code": 503, "latency_ms": 5000,
        }
        features = extract_features(log)
        latency, status, level, method, service, is_5xx, is_err = features

        assert latency == 5000.0
        assert status == 503
        assert level == LEVEL_MAP["ERROR"]    # 2
        assert is_5xx == 1.0                  # 503 >= 500
        assert is_err == 1.0

    def test_unknown_service_defaults(self):
        log = {
            "service": "unknown-service", "level": "WARN",
            "method": "DELETE", "status_code": 404, "latency_ms": 80,
        }
        features = extract_features(log)
        # Should not raise — unknown keys default gracefully
        assert len(features) == 7

    def test_missing_fields_use_defaults(self):
        # Simulate a partial log (some fields missing)
        features = extract_features({})
        assert len(features) == 7
        # Defaults: latency=100, status=200, INFO, GET, auth-service, no anomaly flags
        assert features[0] == 100.0   # default latency
        assert features[1] == 200     # default status


# ── Synthetic data tests ──────────────────────────────────────────────

class TestSyntheticData:

    def test_shape(self):
        data = generate_synthetic_normal_data(100)
        assert data.shape == (100, 7)

    def test_latency_positive(self):
        data = generate_synthetic_normal_data(200)
        assert (data[:, 0] > 0).all(), "All latencies must be positive"

    def test_no_5xx_in_normal_data(self):
        data = generate_synthetic_normal_data(500)
        # Column 5 = is_5xx flag — should be 0 for all synthetic normal data
        assert (data[:, 5] == 0.0).all(), "Synthetic normal data should have no 5xx errors"


# ── Model tests ───────────────────────────────────────────────────────

class TestAnomalyDetector:

    @pytest.fixture
    def detector(self):
        """Fresh detector for each test — pre-trained on synthetic data."""
        return AnomalyDetector()

    def test_model_trained_on_init(self, detector):
        assert detector.trained is True

    def test_predict_returns_enriched_log(self, detector):
        log = {
            "service": "auth-service", "level": "INFO",
            "method": "GET", "endpoint": "/login",
            "status_code": 200, "latency_ms": 100,
            "message": "Login OK", "timestamp": "2025-01-01T00:00:00Z",
        }
        result = detector.predict(log)

        assert "anomaly_score" in result
        assert "is_anomaly" in result
        assert "raw_if_score" in result
        assert "processed_at" in result

    def test_anomaly_score_range(self, detector):
        log = {
            "service": "auth-service", "level": "INFO",
            "method": "GET", "status_code": 200, "latency_ms": 100,
        }
        result = detector.predict(log)
        assert 0.0 <= result["anomaly_score"] <= 1.0

    def test_normal_log_not_flagged(self, detector):
        """A clearly normal log should not be flagged as an anomaly."""
        log = {
            "service": "auth-service", "level": "INFO",
            "method": "GET", "status_code": 200, "latency_ms": 80,
        }
        result = detector.predict(log)
        # Not guaranteed (model is probabilistic) but very likely for a clean normal log
        assert result["anomaly_score"] < 0.8, "Normal log should have low anomaly score"

    def test_anomalous_log_flagged(self, detector):
        """A clearly anomalous log (high latency + 5xx) should score highly."""
        log = {
            "service": "payment-service", "level": "ERROR",
            "method": "POST", "status_code": 503, "latency_ms": 7000,
        }
        result = detector.predict(log)
        assert result["anomaly_score"] > 0.3, "Anomalous log should have elevated score"

    def test_buffer_grows_with_predictions(self, detector):
        initial_size = len(detector.buffer)
        for _ in range(5):
            detector.predict({
                "service": "auth-service", "level": "INFO",
                "method": "GET", "status_code": 200, "latency_ms": 100,
            })
        assert len(detector.buffer) == initial_size + 5

    def test_stats_tracked(self, detector):
        detector.predict({
            "service": "auth-service", "level": "INFO",
            "method": "GET", "status_code": 200, "latency_ms": 100,
        })
        assert detector.stats["total"] >= 1
        assert detector.stats["service_auth-service"] >= 1

    def test_thread_safety(self, detector):
        """Multiple threads predicting simultaneously should not crash."""
        import threading
        errors = []

        def predict_many():
            try:
                for _ in range(20):
                    detector.predict({
                        "service": "auth-service", "level": "INFO",
                        "method": "GET", "status_code": 200, "latency_ms": 100,
                    })
            except Exception as e:
                errors.append(str(e))

        threads = [threading.Thread(target=predict_many) for _ in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert len(errors) == 0, f"Thread safety errors: {errors}"
