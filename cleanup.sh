#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

# ── Colours ──────────────────────────────────────────────────────────────────
RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; CYAN='\033[0;36m'; NC='\033[0m'
info()    { echo -e "${CYAN}[kuboco]${NC} $*"; }
success() { echo -e "${GREEN}[kuboco]${NC} $*"; }
warn()    { echo -e "${YELLOW}[kuboco]${NC} $*"; }

# ── Parse flags ───────────────────────────────────────────────────────────────
DELETE_DATA=false
for arg in "$@"; do
    case "$arg" in
        --delete-data) DELETE_DATA=true ;;
        --help|-h)
            echo "Usage: $0 [--delete-data]"
            echo ""
            echo "  Removes all Kuboco Kubernetes resources and local Docker images."
            echo "  --delete-data   Also delete the local SQLite database (data/kuboco.db)"
            exit 0
            ;;
    esac
done

# ── Confirm ───────────────────────────────────────────────────────────────────
echo ""
warn "This will delete all Kuboco namespaces, pods, services, and Docker images."
if [[ "$DELETE_DATA" == "true" ]]; then
    warn "data/kuboco.db will also be deleted (--delete-data)."
fi
echo ""
read -r -p "$(echo -e "${YELLOW}[kuboco]${NC} Continue? [y/N] ")" CONFIRM
[[ "$CONFIRM" =~ ^[Yy]$ ]] || { echo "Aborted."; exit 0; }
echo ""

# ── Kubernetes cleanup ────────────────────────────────────────────────────────
if command -v kubectl &>/dev/null && kubectl cluster-info --request-timeout=5s &>/dev/null 2>&1; then

    # Delete all per-user namespaces (kuboco-user-*)
    info "Deleting per-user namespaces (kuboco-user-*)…"
    kubectl get namespaces -o name 2>/dev/null \
        | grep '^namespace/kuboco-user-' \
        | xargs -r kubectl delete --ignore-not-found 2>/dev/null || true

    # Legacy: kuboco-containers namespace (pre-per-user-namespace builds)
    kubectl delete namespace kuboco-containers --ignore-not-found 2>/dev/null || true

    # Delete backend resources (deployment, service, secret, configmap, pvc)
    info "Deleting backend deployment and resources…"
    kubectl delete -f k8s/backend.yaml       --ignore-not-found 2>/dev/null || true
    kubectl delete -f k8s/rbac.yaml          --ignore-not-found 2>/dev/null || true

    # Delete kuboco namespace last
    info "Deleting namespace kuboco…"
    kubectl delete namespace kuboco --ignore-not-found 2>/dev/null || true

    success "Kubernetes resources deleted."
else
    warn "kubectl not available or cluster unreachable — skipping Kubernetes cleanup."
fi

# ── Docker image cleanup ──────────────────────────────────────────────────────
CONTEXT=$(kubectl config current-context 2>/dev/null || echo "")
IS_MINIKUBE=false
command -v minikube &>/dev/null && minikube status &>/dev/null 2>&1 && IS_MINIKUBE=true

remove_image() {
    local img="$1"
    if [[ "$IS_MINIKUBE" == "true" ]]; then
        info "Removing ${img} from minikube…"
        minikube image rm "$img" 2>/dev/null || true
    fi
    if docker image inspect "$img" &>/dev/null 2>&1; then
        info "Removing local Docker image ${img}…"
        docker rmi "$img" 2>/dev/null || true
    fi
}

remove_image kuboco/ubuntu-ttyd:latest
remove_image kuboco/backend:latest
success "Docker images removed."

# ── Optional: delete local database ──────────────────────────────────────────
if [[ "$DELETE_DATA" == "true" ]]; then
    if [[ -f data/kuboco.db ]]; then
        info "Deleting data/kuboco.db…"
        rm -f data/kuboco.db
        success "Database deleted."
    else
        info "data/kuboco.db not found, skipping."
    fi
else
    warn "Skipping data/kuboco.db (pass --delete-data to remove it)."
fi

echo ""
success "Cleanup complete."
