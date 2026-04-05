# Kuboco

Kuboco is a self-hosted web application that gives users on-demand Linux terminals in their browser. Each user gets isolated containers running on Kubernetes, accessible through a clean web interface with no client software required.

---

## What it does

A user registers, clicks **New Container**, and within seconds has a fully functional Linux shell in their browser. They can run commands, install packages, start servers, and connect to those servers through the built-in port proxy — all without leaving the browser tab.

Containers are isolated from one another at the network level. Each one is a standard Ubuntu environment with common tools pre-installed.

---

## Prerequisites

- Docker
- kubectl connected to a running cluster (minikube, kind, k3d, or any remote cluster)

That is all. The quickstart script handles everything else.

---

## Getting started

```bash
git clone <repository-url>
cd kuboco
./quickstart.sh
```

The script will:

1. Generate a secret key and write it to `.env`
2. Build the container images
3. Apply the Kubernetes manifests
4. Wait for the backend to be ready
5. Print the URL and attempt to open it in your browser

On minikube the URL will be `http://<minikube-ip>:30080`. On kind or k3d a port-forward is started and the URL is `http://localhost:8000`.

---

## Using Kuboco

**Create a container**

Click **New Container**, give it a name (lowercase letters, numbers, and hyphens), and click **Launch**. The container starts in the background. Once it is ready the terminal opens automatically.

**Use the terminal**

The terminal is a full interactive shell. You can run any command, use the arrow keys, tab-complete, and scroll through history exactly as you would in a local terminal.

**Access a port**

If a process inside the container is listening on a port, enter that port number in the **Port Proxy** panel and click **Connect**. The service appears in an inline frame. Click the external-link button to open it in a new tab.

**Delete a container**

Click the trash icon on the container card. The pod, service, and all associated resources are removed immediately.

---

## Development mode

For local development with hot reload:

```bash
cp .env.example .env   # set SECRET_KEY
docker-compose up --build
```

The backend reloads on every file save. The frontend is served as static files — refresh the browser to pick up changes. Your local `~/.kube/config` is mounted read-only so the backend can reach the cluster.

---

## Cleanup

To remove all Kuboco resources from the cluster and delete the Docker images:

```bash
./cleanup.sh
```

To also delete the local SQLite database:

```bash
./cleanup.sh --delete-data
```

The script asks for confirmation before taking any action.

---

## Configuration

All settings are read from environment variables. The defaults are set in `k8s/backend.yaml` (ConfigMap) for cluster deployments and in `docker-compose.yml` for local development.

| Variable | Default | Description |
|---|---|---|
| `SECRET_KEY` | — | Required. Used to sign authentication tokens. Generate with `openssl rand -hex 32`. |
| `DATABASE_URL` | `sqlite+aiosqlite:////app/data/kuboco.db` | SQLite connection string. Replace with a PostgreSQL URL for production. |
| `CONTAINER_IMAGE` | `kuboco/ubuntu-ttyd:latest` | The image used for user containers. |
| `MAX_CONTAINERS_PER_USER` | `5` | Maximum number of containers a single user may have at one time. |
| `CONTAINER_NAMESPACE` | `kuboco-containers` | Kubernetes namespace where user containers run. |

---

## Supported cluster types

| Cluster | Notes |
|---|---|
| minikube | Images are built directly in the minikube daemon. No registry needed. |
| kind | Images are loaded with `kind load docker-image` after building locally. |
| k3d | Images are imported with `k3d image import` after building locally. |
| Remote cluster | Build and push images to a registry your cluster can pull from, then run `kubectl apply -f k8s/`. |

---

## Security model

Each user's containers run in a dedicated Kubernetes namespace (`kuboco-containers`). A NetworkPolicy denies all ingress to that namespace by default and opens only the traffic from the backend pod. Users cannot reach each other's containers directly.

The backend service account holds the minimum permissions required: it can create, read, and delete pods and services in `kuboco-containers` and nothing else.

---

## Running the QA test

A Playwright-based end-to-end test verifies that the terminal renders output correctly without protocol artifacts:

```bash
pip install playwright
playwright install chromium
python3 qa/test_terminal.py
```

The test registers a temporary user, launches a container, types a command, reads the terminal buffer, and cleans up after itself. It prints PASS or FAIL with a diagnostic summary and saves a screenshot to `qa/screenshot_after_command.png`.
