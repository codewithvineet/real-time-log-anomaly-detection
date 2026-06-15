#!/bin/bash

# ─────────────────────────────────────────────
#  Real-Time Anomaly Detection Pipeline — Demo
# ─────────────────────────────────────────────

set -e

GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
RED='\033[0;31m'
NC='\033[0m' # No Color

print_step() { echo -e "\n${CYAN}▶ $1${NC}"; }
print_ok()   { echo -e "${GREEN}✔ $1${NC}"; }
print_warn() { echo -e "${YELLOW}⚠ $1${NC}"; }
print_err()  { echo -e "${RED}✘ $1${NC}"; exit 1; }

echo ""
echo -e "${CYAN}╔══════════════════════════════════════════════╗${NC}"
echo -e "${CYAN}║   Real-Time Anomaly Detection Pipeline Demo  ║${NC}"
echo -e "${CYAN}╚══════════════════════════════════════════════╝${NC}"
echo ""

# ── 0. Check dependencies ──────────────────────────────────────
print_step "Checking dependencies..."

command -v docker    &>/dev/null || print_err "Docker not found. Install: https://docs.docker.com/get-docker/"
command -v minikube  &>/dev/null || print_err "Minikube not found. Install: https://minikube.sigs.k8s.io/docs/start/"
command -v kubectl   &>/dev/null || print_err "kubectl not found. Install: https://kubernetes.io/docs/tasks/tools/"

print_ok "All dependencies found."

# ── 1. Start Minikube ──────────────────────────────────────────
print_step "Starting Minikube..."

if minikube status | grep -q "Running"; then
  print_ok "Minikube is already running."
else
  minikube start --memory=4096 --cpus=2
  print_ok "Minikube started."
fi

# ── 2. Set Docker env to use Minikube's daemon ─────────────────
print_step "Pointing Docker to Minikube daemon..."
eval $(minikube docker-env)
print_ok "Docker env set."

# ── 3. Apply Kubernetes manifests ─────────────────────────────
print_step "Deploying all services to Kubernetes..."

if [ -d "./k8s" ]; then
  kubectl apply -f k8s/
  print_ok "Manifests applied."
else
  print_warn "No k8s/ directory found. Skipping manifest apply."
fi

# ── 4. Wait for pods to be ready ──────────────────────────────
print_step "Waiting for all pods to be Ready (timeout: 3 minutes)..."

kubectl wait --for=condition=ready pod --all --timeout=180s 2>/dev/null \
  && print_ok "All pods are running." \
  || print_warn "Some pods may still be starting. Run: kubectl get pods"

echo ""
echo -e "${CYAN}Current pod status:${NC}"
kubectl get pods -o wide

# ── 5. Port-forward services ───────────────────────────────────
print_step "Setting up port-forwarding..."

# Kill any existing port-forwards
pkill -f "kubectl port-forward" 2>/dev/null || true
sleep 1

kubectl port-forward svc/kibana        5601:5601 &>/dev/null &
kubectl port-forward svc/grafana       3000:3000 &>/dev/null &
kubectl port-forward svc/elasticsearch 9200:9200 &>/dev/null &

sleep 3
print_ok "Port-forwarding active:"
echo "   → Kibana:         http://localhost:5601"
echo "   → Grafana:        http://localhost:3000  (admin / admin)"
echo "   → Elasticsearch:  http://localhost:9200"

# ── 6. Send sample Kafka messages ─────────────────────────────
print_step "Sending 20 sample messages to Kafka (including injected anomalies)..."

KAFKA_POD=$(kubectl get pod -l app=kafka -o jsonpath="{.items[0].metadata.name}" 2>/dev/null)

if [ -z "$KAFKA_POD" ]; then
  print_warn "Kafka pod not found. Skipping message injection."
else
  for i in $(seq 1 20); do
    # Every 5th message is an anomaly (high value)
    if [ $((i % 5)) -eq 0 ]; then
      VALUE=$(awk "BEGIN{printf \"%.2f\", 900 + $RANDOM % 100}")
      LABEL="ANOMALY"
    else
      VALUE=$(awk "BEGIN{printf \"%.2f\", 10 + $RANDOM % 50}")
      LABEL="normal"
    fi

    MSG="{\"id\": $i, \"value\": $VALUE, \"label\": \"$LABEL\", \"timestamp\": \"$(date -u +%Y-%m-%dT%H:%M:%SZ)\"}"

    kubectl exec "$KAFKA_POD" -- \
      kafka-console-producer.sh \
      --bootstrap-server localhost:9092 \
      --topic anomaly-input <<< "$MSG" 2>/dev/null

    echo "  [msg $i] $MSG"
    sleep 0.4
  done

  print_ok "20 messages sent. The ML service will flag the anomalies."
fi

# ── 7. Show live logs from ML service ────────────────────────
print_step "Tailing ML service logs for 15 seconds (watch for ANOMALY flags)..."

ML_POD=$(kubectl get pod -l app=ml-service -o jsonpath="{.items[0].metadata.name}" 2>/dev/null)

if [ -z "$ML_POD" ]; then
  print_warn "ML service pod not found. Check: kubectl get pods"
else
  timeout 15s kubectl logs -f "$ML_POD" 2>/dev/null || true
fi

# ── 8. Open dashboards in browser ────────────────────────────
print_step "Opening dashboards..."

if command -v xdg-open &>/dev/null; then
  xdg-open "http://localhost:5601" &
  sleep 1
  xdg-open "http://localhost:3000" &
elif command -v open &>/dev/null; then
  open "http://localhost:5601"
  sleep 1
  open "http://localhost:3000"
else
  echo "  Open manually:"
  echo "  → Kibana:  http://localhost:5601"
  echo "  → Grafana: http://localhost:3000"
fi

# ── 9. Final summary ──────────────────────────────────────────
echo ""
echo -e "${GREEN}╔══════════════════════════════════════════════╗${NC}"
echo -e "${GREEN}║              Demo is LIVE!                   ║${NC}"
echo -e "${GREEN}╚══════════════════════════════════════════════╝${NC}"
echo ""
echo "  Kibana:         http://localhost:5601"
echo "  Grafana:        http://localhost:3000  (admin / admin)"
echo "  Elasticsearch:  http://localhost:9200"
echo ""
echo "  Useful commands:"
echo "    kubectl get pods              → check all pods"
echo "    kubectl logs -f <pod-name>    → live logs"
echo "    kubectl get svc               → list services"
echo ""
echo -e "${YELLOW}To stop everything:${NC}"
echo "    pkill -f 'kubectl port-forward'"
echo "    minikube stop"
echo ""
