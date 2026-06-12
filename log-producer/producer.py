"""
Log Producer — Phase 2
Simulates realistic microservice logs and streams them to Kafka.
Injects anomalies (latency spikes, error bursts) so the ML model
in Phase 3 has something meaningful to detect.
"""

import json
import time
import random
import logging
import os
from datetime import datetime, timezone
from kafka import KafkaProducer
from kafka.errors import NoBrokersAvailable

# ── Logging setup (for the producer itself, not the fake app logs) ──
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [producer] %(levelname)s %(message)s"
)
logger = logging.getLogger(__name__)


# ── Configuration ───────────────────────────────────────────────────


KAFKA_BROKER = os.getenv("KAFKA_BROKER", "kafka-service:29092")  # internal listener (container-to-container)
KAFKA_TOPIC  = os.getenv("KAFKA_TOPIC", "app-logs")
PRODUCE_RATE   = 1.0             # seconds between each log message
ANOMALY_PROB   = 0.05            # 5% chance of injecting an anomaly burst


# ── Simulated microservices ─────────────────────────────────────────
SERVICES = [
    "auth-service",
    "payment-service",
    "user-service",
    "inventory-service",
    "notification-service",
]

# Weighted log levels: mostly INFO, occasionally WARN, rarely ERROR
LOG_LEVELS        = ["INFO",  "INFO",  "INFO",  "WARN",  "ERROR"]
LOG_LEVEL_WEIGHTS = [0.70,    0.10,    0.10,    0.07,    0.03]

# HTTP endpoints each service might hit
ENDPOINTS = {
    "auth-service":         ["/login", "/logout", "/refresh-token", "/validate"],
    "payment-service":      ["/charge", "/refund", "/balance", "/history"],
    "user-service":         ["/profile", "/update", "/delete", "/search"],
    "inventory-service":    ["/stock", "/reserve", "/release", "/audit"],
    "notification-service": ["/send-email", "/send-sms", "/subscribe", "/unsubscribe"],
}

HTTP_METHODS = ["GET", "POST", "PUT", "DELETE"]

# Normal status codes (weighted toward 200)
NORMAL_STATUS_CODES  = [200, 200, 200, 201, 204, 301, 304, 400, 404]
NORMAL_STATUS_WEIGHTS= [0.70, 0.08, 0.04, 0.04, 0.02, 0.02, 0.02, 0.04, 0.04]

# Anomalous status codes (errors)
ERROR_STATUS_CODES = [500, 502, 503, 504, 429]

# Normal latency range in ms
NORMAL_LATENCY_MIN = 10
NORMAL_LATENCY_MAX = 250

# Anomalous latency range in ms (spikes)
ANOMALY_LATENCY_MIN = 2000
ANOMALY_LATENCY_MAX = 8000

# Typical error messages per service
ERROR_MESSAGES = {
    "auth-service":         ["JWT validation failed", "Session expired", "Invalid credentials", "Token revoked"],
    "payment-service":      ["Payment gateway timeout", "Insufficient funds", "Card declined", "Fraud detected"],
    "user-service":         ["User not found", "Permission denied", "Rate limit exceeded", "DB connection lost"],
    "inventory-service":    ["Stock not available", "Reservation conflict", "Audit lock timeout", "Cache miss"],
    "notification-service": ["SMTP timeout", "SMS provider error", "Template not found", "Queue full"],
}

INFO_MESSAGES = {
    "auth-service":         ["User authenticated successfully", "Token refreshed", "Session created", "Logout successful"],
    "payment-service":      ["Payment processed", "Refund initiated", "Balance retrieved", "Transaction recorded"],
    "user-service":         ["Profile updated", "User created", "Search completed", "Preferences saved"],
    "inventory-service":    ["Stock checked", "Item reserved", "Reservation released", "Audit completed"],
    "notification-service": ["Email sent", "SMS delivered", "Subscription updated", "Notification queued"],
}


# ── Log generators ──────────────────────────────────────────────────

def build_normal_log(service: str) -> dict:
    """Generate a realistic, normal-looking log entry."""
    method    = random.choice(HTTP_METHODS)
    endpoint  = random.choice(ENDPOINTS[service])
    level     = random.choices(LOG_LEVELS, weights=LOG_LEVEL_WEIGHTS)[0]
    status    = random.choices(NORMAL_STATUS_CODES, weights=NORMAL_STATUS_WEIGHTS)[0]
    latency   = random.randint(NORMAL_LATENCY_MIN, NORMAL_LATENCY_MAX)

    if level == "ERROR" or status >= 500:
        message = random.choice(ERROR_MESSAGES[service])
    elif level == "WARN":
        message = f"Slow response detected on {endpoint}"
    else:
        message = random.choice(INFO_MESSAGES[service])

    return {
        "timestamp":   datetime.now(timezone.utc).isoformat(),
        "service":     service,
        "level":       level,
        "method":      method,
        "endpoint":    endpoint,
        "status_code": status,
        "latency_ms":  latency,
        "message":     message,
        "is_anomaly":  False,   # ground truth label (used later for ML evaluation)
    }


def build_anomaly_log(service: str) -> dict:
    """
    Inject an anomalous log entry.
    Anomalies are: high latency + 5xx errors together.
    This pattern is what the Isolation Forest will learn to flag.
    """
    method   = random.choice(HTTP_METHODS)
    endpoint = random.choice(ENDPOINTS[service])
    status   = random.choice(ERROR_STATUS_CODES)
    latency  = random.randint(ANOMALY_LATENCY_MIN, ANOMALY_LATENCY_MAX)
    message  = random.choice(ERROR_MESSAGES[service])

    return {
        "timestamp":   datetime.now(timezone.utc).isoformat(),
        "service":     service,
        "level":       "ERROR",
        "method":      method,
        "endpoint":    endpoint,
        "status_code": status,
        "latency_ms":  latency,
        "message":     f"[ANOMALY] {message}",
        "is_anomaly":  True,
    }


def generate_log() -> dict:
    """Pick a service and decide whether to inject an anomaly."""
    service = random.choice(SERVICES)

    if random.random() < ANOMALY_PROB:
        log = build_anomaly_log(service)
        logger.info(f"💥 Anomaly injected  — {service} | latency={log['latency_ms']}ms | status={log['status_code']}")
    else:
        log = build_normal_log(service)

    return log


# ── Kafka connection (with retry) ───────────────────────────────────

def create_producer(retries: int = 10, delay: int = 5) -> KafkaProducer:
    """
    Try to connect to Kafka with retries.
    Kafka can take a few seconds to be ready after Docker Compose starts.
    """
    for attempt in range(1, retries + 1):
        try:
            producer = KafkaProducer(
                bootstrap_servers=[KAFKA_BROKER],
                value_serializer=lambda v: json.dumps(v).encode("utf-8"),
                key_serializer=lambda k: k.encode("utf-8") if k else None,
                acks="all",               # wait for all replicas to acknowledge
                retries=3,                # retry failed sends
                linger_ms=10,             # small batch window for efficiency
            )
            logger.info(f"✅ Connected to Kafka at {KAFKA_BROKER}")
            return producer
        except NoBrokersAvailable:
            logger.warning(f"Kafka not ready. Attempt {attempt}/{retries}. Retrying in {delay}s...")
            time.sleep(delay)

    raise RuntimeError("Could not connect to Kafka after multiple attempts. Is it running?")


# ── Main loop ───────────────────────────────────────────────────────

def main():
    logger.info("🚀 Log producer starting...")
    producer = create_producer()

    sent = 0
    try:
        while True:
            log = generate_log()

            # Use service name as the Kafka partition key
            # This ensures all logs from the same service go to the same partition
            producer.send(
                topic=KAFKA_TOPIC,
                key=log["service"],
                value=log,
            )

            sent += 1
            print(
                f"[{sent:>6}] {log['timestamp']}  "
                f"{log['service']:<24}  "
                f"{log['level']:<5}  "
                f"{log['status_code']}  "
                f"{log['latency_ms']:>5}ms  "
                f"{log['message']}"
            )

            time.sleep(PRODUCE_RATE)

    except KeyboardInterrupt:
        logger.info("Stopping producer...")
    finally:
        producer.flush()   # send any buffered messages before exit
        producer.close()
        logger.info(f"Producer stopped. Total messages sent: {sent}")


if __name__ == "__main__":
    main()
