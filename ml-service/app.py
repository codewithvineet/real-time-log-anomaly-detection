"""
ML Anomaly Detection Service — Phase 3

Two responsibilities:
  1. FastAPI HTTP server  — /predict, /health, /stats endpoints
  2. Background Kafka consumer — reads app-logs, scores each message,
     writes results to Elasticsearch

Model: Isolation Forest (unsupervised)
  - Trained on a batch of synthetic "normal" logs at startup
  - Re-trains every RETRAIN_INTERVAL messages with the logs it has seen
    so it adapts to the real traffic pattern over time
"""

import json
import time
import logging
import threading
import os
from datetime import datetime, timezone
from collections import defaultdict

import numpy as np
import uvicorn
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from sklearn.ensemble import IsolationForest
from sklearn.preprocessing import StandardScaler
from kafka import KafkaConsumer
from kafka.errors import NoBrokersAvailable
from elasticsearch import Elasticsearch

# ── Logging ─────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [ml-service] %(levelname)s %(message)s"
)
logger = logging.getLogger(__name__)


# ── Configuration ────────────────────────────────────────────────────
KAFKA_BROKER = os.getenv(
    "KAFKA_BROKER",
    "kafka-service:29092"
)
KAFKA_TOPIC        = "app-logs"
KAFKA_GROUP_ID     = "ml-anomaly-detector"

ES_HOST = os.getenv(

    "ES_HOST",

    "http://elasticsearch-service:9200"

)
ES_INDEX           = "log-anomalies"

RETRAIN_INTERVAL   = 200    # retrain model every N messages
CONTAMINATION      = 0.05   # expected fraction of anomalies (matches producer's 5%)
ANOMALY_THRESHOLD  = -0.1   # score below this = anomaly (Isolation Forest scores: -1 to 1)


# ── Feature engineering ──────────────────────────────────────────────
# Map categorical fields to numbers the model can work with.

LEVEL_MAP = {
    "INFO":  0,
    "WARN":  1,
    "ERROR": 2,
    "DEBUG": 0,
}

METHOD_MAP = {
    "GET":    0,
    "POST":   1,
    "PUT":    2,
    "DELETE": 3,
    "PATCH":  2,
}

SERVICE_MAP = {
    "auth-service":         0,
    "payment-service":      1,
    "user-service":         2,
    "inventory-service":    3,
    "notification-service": 4,
}

def extract_features(log: dict) -> list[float]:
    """
    Convert a raw log dict into a numeric feature vector.

    Features:
      [0] latency_ms      — raw ms, key anomaly signal
      [1] status_code     — 200 normal, 5xx anomalous
      [2] level_encoded   — INFO=0, WARN=1, ERROR=2
      [3] method_encoded  — GET=0, POST=1, PUT=2, DELETE=3
      [4] service_encoded — service identity
      [5] is_5xx          — binary: 1 if status >= 500
      [6] is_error_level  — binary: 1 if level == ERROR
    """
    latency     = float(log.get("latency_ms", 100))
    status      = int(log.get("status_code", 200))
    level       = LEVEL_MAP.get(log.get("level", "INFO"), 0)
    method      = METHOD_MAP.get(log.get("method", "GET"), 0)
    service     = SERVICE_MAP.get(log.get("service", "auth-service"), 0)
    is_5xx      = 1.0 if status >= 500 else 0.0
    is_err_lvl  = 1.0 if log.get("level") == "ERROR" else 0.0

    return [latency, status, level, method, service, is_5xx, is_err_lvl]


# ── Synthetic training data ───────────────────────────────────────────
# We generate a batch of "normal" logs to pre-train the model at startup.
# Without this, the model has nothing to compare against until real logs flow in.

def generate_synthetic_normal_data(n: int = 1000) -> np.ndarray:
    """
    Create synthetic normal log feature vectors.
    Normal = low latency, 2xx status, INFO level.
    """
    import random
    rows = []
    for _ in range(n):
        latency = random.gauss(120, 60)         # normal ~120ms, std 60ms
        latency = max(10, latency)              # clamp to positive
        status  = random.choices(
            [200, 201, 204, 301, 304, 400, 404],
            weights=[0.70, 0.08, 0.04, 0.04, 0.04, 0.05, 0.05]
        )[0]
        level   = random.choices([0, 1, 2], weights=[0.85, 0.10, 0.05])[0]
        method  = random.choice([0, 1, 2, 3])
        service = random.randint(0, 4)
        is_5xx  = 0.0
        is_err  = 1.0 if level == 2 else 0.0

        rows.append([latency, status, level, method, service, is_5xx, is_err])

    return np.array(rows)


# ── Model class ──────────────────────────────────────────────────────

class AnomalyDetector:
    """
    Wraps IsolationForest + StandardScaler.
    Holds a rolling buffer of seen logs and retrains periodically.
    Thread-safe via a lock (Kafka thread writes, HTTP thread reads).
    """

    def __init__(self):
        self.scaler  = StandardScaler()
        self.model   = IsolationForest(
            n_estimators=100,
            contamination=CONTAMINATION,
            random_state=42,
        )
        self.lock    = threading.Lock()
        self.buffer  = []          # rolling window of feature vectors from live logs
        self.trained = False
        self.stats   = defaultdict(int)  # for /stats endpoint

        # Pre-train on synthetic data so the model is ready immediately
        self._initial_train()

    def _initial_train(self):
        logger.info("Training model on synthetic baseline data...")
        X = generate_synthetic_normal_data(1000)
        X_scaled = self.scaler.fit_transform(X)
        self.model.fit(X_scaled)
        self.trained = True
        logger.info("✅ Initial training complete.")

    def retrain(self):
        """Retrain on everything in the buffer (synthetic + real logs seen so far)."""
        with self.lock:
            if len(self.buffer) < 50:
                return   # not enough data yet

            X_real    = np.array(self.buffer)
            X_synth   = generate_synthetic_normal_data(500)
            X_combined = np.vstack([X_synth, X_real])

            self.scaler.fit(X_combined)
            X_scaled = self.scaler.transform(X_combined)
            self.model.fit(X_scaled)

        logger.info(f"🔄 Model retrained on {len(self.buffer)} real + 500 synthetic samples.")

    def predict(self, log: dict) -> dict:
        """
        Score a single log entry.
        Returns the original log enriched with anomaly info.
        """
        features = extract_features(log)

        with self.lock:
            # Buffer this log for future retraining
            self.buffer.append(features)
            if len(self.buffer) > 5000:
                self.buffer = self.buffer[-5000:]  # keep last 5000

            X = np.array([features])
            X_scaled = self.scaler.transform(X)

            # score_samples returns negative values; more negative = more anomalous
            raw_score    = float(self.model.score_samples(X_scaled)[0])
            # decision_function: -1 = anomaly, 1 = normal
            decision     = int(self.model.predict(X_scaled)[0])

        is_anomaly   = decision == -1
        # Normalize score to 0–1 range for readability (0 = normal, 1 = anomalous)
        anomaly_score = max(0.0, min(1.0, (-raw_score + 0.5) / 1.0))

        # Update stats
        self.stats["total"] += 1
        if is_anomaly:
            self.stats["anomalies"] += 1
        self.stats[f"service_{log.get('service', 'unknown')}"] += 1

        return {
            **log,
            "anomaly_score":  round(anomaly_score, 4),
            "is_anomaly":     is_anomaly,
            "raw_if_score":   round(raw_score, 4),
            "processed_at":   datetime.now(timezone.utc).isoformat(),
        }


# ── Elasticsearch setup ──────────────────────────────────────────────

def get_es_client() -> Elasticsearch | None:
    """Connect to ES. Returns None if unavailable (so the service still works)."""
    try:
        es = Elasticsearch(ES_HOST)
        if es.ping():
            logger.info(f"✅ Connected to Elasticsearch at {ES_HOST}")
            ensure_index(es)
            return es
        else:
            logger.warning("Elasticsearch ping failed. Results won't be indexed.")
    except Exception as e:
        logger.warning(f"Could not connect to Elasticsearch: {e}")
    return None


def ensure_index(es: Elasticsearch):
    """Create the ES index with correct mappings if it doesn't exist."""
    if es.indices.exists(index=ES_INDEX):
        return

    mapping = {
        "mappings": {
            "properties": {
                "timestamp":     {"type": "date"},
                "processed_at":  {"type": "date"},
                "service":       {"type": "keyword"},
                "level":         {"type": "keyword"},
                "method":        {"type": "keyword"},
                "endpoint":      {"type": "keyword"},
                "status_code":   {"type": "integer"},
                "latency_ms":    {"type": "integer"},
                "message":       {"type": "text"},
                "is_anomaly":    {"type": "boolean"},
                "anomaly_score": {"type": "float"},
                "raw_if_score":  {"type": "float"},
            }
        }
    }

    es.indices.create(index=ES_INDEX, body=mapping)
    logger.info(f"Created Elasticsearch index: {ES_INDEX}")


def index_result(es: Elasticsearch, result: dict):
    """Write a scored log to Elasticsearch."""
    try:
        es.index(index=ES_INDEX, body=result)
    except Exception as e:
        logger.error(f"Failed to index log to ES: {e}")


# ── Kafka consumer loop ──────────────────────────────────────────────

def kafka_consumer_loop(detector: AnomalyDetector, es_client):
    """
    Runs in a background thread.
    Continuously reads from Kafka, scores each log, writes to ES.
    """
    consumer = None
    retries  = 0

    while consumer is None and retries < 10:
        try:
            consumer = KafkaConsumer(
                KAFKA_TOPIC,
                bootstrap_servers=[KAFKA_BROKER],
                group_id=KAFKA_GROUP_ID,
                auto_offset_reset="latest",       # only process new logs (not history)
                value_deserializer=lambda m: json.loads(m.decode("utf-8")),
                consumer_timeout_ms=1000,         # don't block forever if topic is empty
            )
            logger.info("✅ Kafka consumer connected.")
        except NoBrokersAvailable:
            retries += 1
            logger.warning(f"Kafka not ready. Retry {retries}/10 in 5s...")
            time.sleep(5)

    if consumer is None:
        logger.error("Could not connect to Kafka. Consumer thread exiting.")
        return

    messages_processed = 0

    logger.info("🎧 Listening for logs on Kafka topic: app-logs")
    while True:
        try:
            for message in consumer:
                log = message.value
                result = detector.predict(log)

                # Write to Elasticsearch
                if es_client:
                    index_result(es_client, result)

                messages_processed += 1

                # Log anomalies to console so you can see them
                if result["is_anomaly"]:
                    logger.warning(
                        f"🚨 ANOMALY | {result['service']} | "
                        f"latency={result['latency_ms']}ms | "
                        f"status={result['status_code']} | "
                        f"score={result['anomaly_score']:.3f} | "
                        f"{result['message']}"
                    )

                # Retrain periodically
                if messages_processed % RETRAIN_INTERVAL == 0:
                    threading.Thread(target=detector.retrain, daemon=True).start()
                    logger.info(f"Processed {messages_processed} messages total.")

        except Exception as e:
            logger.error(f"Consumer error: {e}. Retrying in 3s...")
            time.sleep(3)


# ── FastAPI app ──────────────────────────────────────────────────────

app = FastAPI(
    title="Log Anomaly Detection Service",
    description="Real-time ML anomaly detection for microservice logs",
    version="1.0.0",
)

# Shared state (initialized on startup)
detector:  AnomalyDetector = None
es_client: Elasticsearch   = None


class LogEntry(BaseModel):
    """Schema for the /predict HTTP endpoint."""
    service:     str   = "auth-service"
    level:       str   = "INFO"
    method:      str   = "GET"
    endpoint:    str   = "/health"
    status_code: int   = 200
    latency_ms:  int   = 120
    message:     str   = "OK"
    timestamp:   str   = ""


@app.on_event("startup")
def startup():
    global detector, es_client

    logger.info("🚀 ML service starting up...")

    # Initialize model
    detector = AnomalyDetector()

    # Connect to Elasticsearch
    es_client = get_es_client()

    # Start Kafka consumer in background thread
    t = threading.Thread(
        target=kafka_consumer_loop,
        args=(detector, es_client),
        daemon=True,
        name="kafka-consumer",
    )
    t.start()
    logger.info("✅ Kafka consumer thread started.")


@app.get("/health")
def health():
    """Health check — used by Docker and Kubernetes."""
    return {
        "status":       "healthy",
        "model_trained": detector.trained if detector else False,
        "es_connected":  es_client is not None,
    }


@app.post("/predict")
def predict(log: LogEntry):
    """
    Score a single log entry via HTTP.
    Useful for manual testing or one-off checks.

    Example:
      curl -X POST http://localhost:8000/predict \
        -H "Content-Type: application/json" \
        -d '{"service":"payment-service","level":"ERROR","status_code":503,"latency_ms":5000,"method":"POST","endpoint":"/charge","message":"Gateway timeout"}'
    """
    if not detector:
        raise HTTPException(status_code=503, detail="Model not initialized yet")

    result = detector.predict(log.dict())
    return result


@app.get("/stats")
def stats():
    """Return running statistics about processed logs."""
    if not detector:
        raise HTTPException(status_code=503, detail="Model not initialized yet")

    total     = detector.stats.get("total", 0)
    anomalies = detector.stats.get("anomaly", 0)

    return {
        "total_processed": total,
        "total_anomalies": anomalies,
        "anomaly_rate":    round(anomalies / total, 4) if total > 0 else 0,
        "buffer_size":     len(detector.buffer),
        "by_service": {
            k.replace("service_", ""): v
            for k, v in detector.stats.items()
            if k.startswith("service_")
        },
    }


@app.get("/predict")
def predict_get(log: str):
    """
    Simple GET version for quick browser/curl testing.
    Pass the log message as a query param.

    Example: GET /predict?log=ERROR+Database+timeout
    """
    if not detector:
        raise HTTPException(status_code=503, detail="Model not initialized yet")

    # Build a minimal log entry from the string
    is_error = any(w in log.upper() for w in ["ERROR", "FAIL", "TIMEOUT", "CRASH"])
    entry = {
        "service":     "manual-test",
        "level":       "ERROR" if is_error else "INFO",
        "method":      "GET",
        "endpoint":    "/test",
        "status_code": 500 if is_error else 200,
        "latency_ms":  5000 if is_error else 100,
        "message":     log,
        "timestamp":   datetime.now(timezone.utc).isoformat(),
    }
    return detector.predict(entry)


if __name__ == "__main__":
    uvicorn.run("app:app", host="0.0.0.0", port=8000, reload=False)