# Real-Time Log Monitoring & Anomaly Detection System 🚀

## 📌 Project Overview
This project demonstrates a **real-time log monitoring platform** with **ML-based anomaly detection**.  
It integrates modern DevOps tools and showcases **streaming, monitoring, and AI-driven automation**.

### 🔧 Tech Stack
- **Apache Kafka** – log streaming backbone
- **Kafka Connect** – for data sinks (ElasticSearch)
- **Kubernetes + Docker** – container orchestration
- **ElasticSearch + Grafana** – storage & visualization
- **Jenkins** – CI/CD pipeline
- **GitHub** – version control
- **Python ML Service (FastAPI + Scikit-learn)** – anomaly detection

---

## 📊 Architecture
```
[ App Logs ] --> [ Kafka ] --> [ Kafka Connect ] --> [ ElasticSearch ] --> [ Grafana ]
                                |
                                v
                        [ ML Anomaly Detection Service ]
                                |
                                v
                           [ Alerts / Dashboard ]
```

---

## 🚀 Features
- Real-time log ingestion via Kafka.
- ElasticSearch indexing for log searchability.
- Grafana dashboards for visualization.
- Anomaly detection microservice (ML model).
- Jenkins pipeline for CI/CD automation.
- Kubernetes manifests for cloud-native deployment.

---

## 📂 Repository Structure
- `ml-service/` – ML anomaly detection API (Python + FastAPI).
- `log-producer/` – Simple log generator pushing logs to Kafka.
- `k8s/` – Kubernetes manifests for all components.
- `dashboards/` – Preconfigured Grafana dashboards.
- `Jenkinsfile` – CI/CD pipeline definition.
- `docker-compose.yml` – Local setup for Kafka + ElasticSearch + Grafana.

---

## ⚡ Quick Start (Local - Docker Compose)
```bash
docker-compose up -d
```

---

## 📈 Next Steps
- Train an ML model for anomaly detection.
- Deploy to Kubernetes using provided manifests.
- Add Grafana alerting integrations (Slack/Email).
