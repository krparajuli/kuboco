#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

# ── Colours ──────────────────────────────────────────────────────────────────
RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; CYAN='\033[0;36m'; NC='\033[0m'
info()    { echo -e "${CYAN}[kuboco]${NC} $*"; }
success() { echo -e "${GREEN}[kuboco]${NC} $*"; }
warn()    { echo -e "${YELLOW}[kuboco]${NC} $*"; }
die()     { echo -e "${RED}[kuboco] ERROR:${NC} $*" >&2; exit 1; }

# ── Preflight checks ─────────────────────────────────────────────────────────
check_cmd() { command -v "$1" &>/dev/null || die "'$1' not found. Please install it first."; }
check_cmd docker
check_cmd kubectl

info "Checking kubectl cluster connection…"
kubectl cluster-info --request-timeout=5s &>/dev/null || die "kubectl cannot reach a cluster. Start one (kind/k3d/minikube) or set KUBECONFIG."
success "Cluster reachable."

# ── .env setup ───────────────────────────────────────────────────────────────
if [[ ! -f .env ]]; then
    info "Creating .env from .env.example…"
    cp .env.example .env
    # Generate a random SECRET_KEY
    if command -v openssl &>/dev/null; then
        SECRET=$(openssl rand -hex 32)
        sed -i "s|change-me-use-openssl-rand-hex-32|${SECRET}|" .env
        success "Generated SECRET_KEY."
    else
        warn "openssl not found — please set SECRET_KEY manually in .env before running in production."
    fi
else
    info ".env already exists, skipping."
fi

# ── Detect cluster type ───────────────────────────────────────────────────────
CONTEXT=$(kubectl config current-context 2>/dev/null || echo "")

IS_MINIKUBE=false
command -v minikube &>/dev/null && minikube status &>/dev/null && IS_MINIKUBE=true

# ── Build + load container images ────────────────────────────────────────────
# For minikube: build directly into its daemon (avoids load step entirely).
# For kind/k3d: build locally then load. For others: build locally only.

if [[ "$IS_MINIKUBE" == "true" ]]; then
    info "Building ttyd container image directly in minikube (kuboco/ubuntu-ttyd:latest)…"
    minikube image build -t kuboco/ubuntu-ttyd:latest ./container/
    success "ttyd image built."

    info "Building backend image directly in minikube (kuboco/backend:latest)…"
    minikube image build -t kuboco/backend:latest .
    success "Backend image built."
else
    info "Building ttyd container image (kuboco/ubuntu-ttyd:latest)…"
    docker build -t kuboco/ubuntu-ttyd:latest ./container/
    success "ttyd image built."

    info "Building backend image (kuboco/backend:latest)…"
    docker build -t kuboco/backend:latest .
    success "Backend image built."

    if [[ "$CONTEXT" == kind-* ]]; then
        CLUSTER="${CONTEXT#kind-}"
        info "Loading images into kind cluster '${CLUSTER}'…"
        kind load docker-image kuboco/ubuntu-ttyd:latest --name "$CLUSTER"
        kind load docker-image kuboco/backend:latest --name "$CLUSTER"
    elif [[ "$CONTEXT" == k3d-* ]]; then
        CLUSTER="${CONTEXT#k3d-}"
        info "Loading images into k3d cluster '${CLUSTER}'…"
        k3d image import kuboco/ubuntu-ttyd:latest kuboco/backend:latest --cluster "$CLUSTER"
    else
        warn "Context '${CONTEXT}' is not kind/k3d/minikube — skipping image load."
        warn "Push the images to a registry your cluster can pull from:"
        warn "  docker push kuboco/ubuntu-ttyd:latest"
        warn "  docker push kuboco/backend:latest"
    fi
fi

# ── Apply Kubernetes manifests ───────────────────────────────────────────────
info "Applying Kubernetes manifests…"

kubectl apply -f k8s/namespace.yaml
kubectl apply -f k8s/rbac.yaml
kubectl apply -f k8s/network-policy.yaml

# Patch the SECRET_KEY into the k8s Secret before applying
SECRET_KEY_VAL=$(grep '^SECRET_KEY=' .env | cut -d= -f2-)
if [[ -n "$SECRET_KEY_VAL" ]]; then
    info "Patching SECRET_KEY into k8s Secret…"
    kubectl create secret generic kuboco-secrets \
        --namespace=kuboco \
        --from-literal=SECRET_KEY="$SECRET_KEY_VAL" \
        --dry-run=client -o yaml | kubectl apply -f -
fi

kubectl apply -f k8s/backend.yaml

# ── Force restart so pods pick up the freshly-built image ────────────────────
info "Restarting backend deployment…"
kubectl rollout restart deployment/kuboco-backend -n kuboco

# ── Wait for backend to be ready ─────────────────────────────────────────────
info "Waiting for backend deployment to roll out…"
kubectl rollout status deployment/kuboco-backend -n kuboco --timeout=120s
success "Backend is running."

# ── Expose the UI ─────────────────────────────────────────────────────────────
echo ""
success "Kuboco is ready!"
echo ""

# Resolve the best URL to reach the UI
NODE_URL=""
if [[ "$CONTEXT" == kind-* ]]; then
    # kind exposes NodePort via localhost (port-forward is easier)
    info "Starting port-forward for kind cluster (background)…"
    kubectl port-forward -n kuboco svc/kuboco-backend-svc 8000:80 &>/dev/null &
    PF_PID=$!
    echo -e "  Port-forward PID: ${PF_PID} (kill to stop)"
    NODE_URL="http://localhost:8000"
elif [[ "$CONTEXT" == k3d-* ]]; then
    info "Starting port-forward for k3d cluster (background)…"
    kubectl port-forward -n kuboco svc/kuboco-backend-svc 8000:80 &>/dev/null &
    PF_PID=$!
    echo -e "  Port-forward PID: ${PF_PID} (kill to stop)"
    NODE_URL="http://localhost:8000"
elif command -v minikube &>/dev/null && [[ "$CONTEXT" == minikube* ]]; then
    MINIKUBE_IP=$(minikube ip 2>/dev/null || true)
    if [[ -n "$MINIKUBE_IP" ]]; then
        NODE_URL="http://${MINIKUBE_IP}:30080"
    fi
fi

if [[ -z "$NODE_URL" ]]; then
    # Generic fallback: use node InternalIP
    NODE_IP=$(kubectl get nodes -o jsonpath='{.items[0].status.addresses[?(@.type=="InternalIP")].address}' 2>/dev/null || true)
    if [[ -n "$NODE_IP" ]]; then
        NODE_URL="http://${NODE_IP}:30080"
    else
        NODE_URL="http://<NODE_IP>:30080"
    fi
fi

echo -e "  ${CYAN}Open the UI:${NC} ${GREEN}${NODE_URL}${NC}"
# Try to open the browser automatically
if command -v xdg-open &>/dev/null; then
    xdg-open "$NODE_URL" &>/dev/null &
elif command -v open &>/dev/null; then
    open "$NODE_URL" &>/dev/null &
fi

echo ""
echo -e "  ${CYAN}Dev mode (hot-reload via docker-compose):${NC}"
echo -e "    docker-compose up"
echo ""
echo -e "  ${CYAN}Useful commands:${NC}"
echo -e "    kubectl get pods -n kuboco"
echo -e "    kubectl get pods -n kuboco-containers"
echo -e "    kubectl logs -n kuboco -l app=kuboco-backend -f"
