# ---------------------------------------------------------------------------
# Database credentials — pass on the command line or set in your environment:
#   make migrations-upgrade DATABASE_USERNAME=alice DATABASE_PASSWORD=secret DATABASE_NAME=ridges
# HOST defaults to localhost, PORT defaults to 5432.
# ---------------------------------------------------------------------------

guard-%:
	@if [ -z '${$*}' ]; then echo 'ERROR: $* is required' >&2; exit 1; fi

_DB_GUARDS = guard-DATABASE_USERNAME guard-DATABASE_PASSWORD guard-DATABASE_NAME
_DB_ENV    = DATABASE_USERNAME=$(DATABASE_USERNAME) \
             DATABASE_PASSWORD=$(DATABASE_PASSWORD) \
             DATABASE_HOST=$(or $(DATABASE_HOST),localhost) \
             DATABASE_PORT=$(or $(DATABASE_PORT),5432) \
             DATABASE_NAME=$(DATABASE_NAME)

.PHONY: migrations-generate migrations-upgrade migrations-downgrade migrations-history migrations-current

## Generate a new migration from model changes.
## Usage: make migrations-generate MESSAGE="add foo column"
migrations-generate: $(_DB_GUARDS)
	$(_DB_ENV) uv run alembic revision --autogenerate -m "$(MESSAGE)"

## Upgrade to head (default) or a specific revision.
## Usage: make migrations-upgrade
##        make migrations-upgrade REVISION=abc123
migrations-upgrade: $(_DB_GUARDS)
	$(_DB_ENV) uv run alembic upgrade $(or $(REVISION),head)

## Downgrade one step (default) or to a specific revision.
## Usage: make migrations-downgrade
##        make migrations-downgrade REVISION=-2
migrations-downgrade: $(_DB_GUARDS)
	$(_DB_ENV) uv run alembic downgrade $(or $(REVISION),-1)

## Show the full migration history.
migrations-history: $(_DB_GUARDS)
	$(_DB_ENV) uv run alembic history --verbose

## Show the current applied revision.
migrations-current: $(_DB_GUARDS)
	$(_DB_ENV) uv run alembic current

# ---------------------------------------------------------------------------
# Kubernetes local dev (kind)
#
# Prerequisites: kind, kubectl, docker, kustomize
# Usage:
#   make k8s-setup     — full cluster from scratch (run once)
#   make k8s-images    — rebuild + reload validator image after code changes
#   make k8s-network   — re-apply manifests after editing k8s/ files
#   make k8s-screener  — run screener locally against the kind cluster
#   make k8s-down      — destroy the kind cluster
# ---------------------------------------------------------------------------

K8S_CLUSTER := ridges
K8S_NS      := ridges
K8S_CTX     := kind-$(K8S_CLUSTER)
KUBECTL     := kubectl --context=$(K8S_CTX)

.PHONY: k8s-up k8s-down k8s-images k8s-network k8s-setup k8s-screener

## Create kind cluster with Calico CNI and ridges namespace
k8s-up:
	kind create cluster --name $(K8S_CLUSTER) --config k8s/kind-config.yaml
	$(KUBECTL) apply -f https://raw.githubusercontent.com/projectcalico/calico/v3.27.0/manifests/calico.yaml
	$(KUBECTL) wait --for=condition=ready pod -l k8s-app=calico-node -n kube-system --timeout=180s
	$(KUBECTL) create namespace $(K8S_NS) --dry-run=client -o yaml | $(KUBECTL) apply -f -

## Destroy the kind cluster
k8s-down:
	kind delete cluster --name $(K8S_CLUSTER)

## Build and load the validator/screener image into every kind node.
## Re-run this after any code change to pick up the new image.
k8s-images:
	docker build -t ridges-validator:latest -f Dockerfile.validator .
	kind load docker-image ridges-validator:latest --name $(K8S_CLUSTER)

## Apply all k8s/ manifests (NetworkPolicies, RBAC, registry, dockerhost).
## Re-run this after editing any file under k8s/.
k8s-network:
	$(KUBECTL) apply -f k8s/registry.yaml
	$(KUBECTL) wait --for=condition=ready pod -l app=registry -n $(K8S_NS) --timeout=120s
	kustomize build k8s/local | $(KUBECTL) apply -f -

## Full setup: cluster → registry creds → images → manifests → secrets.
## Run once after k8s-up (or after k8s-down to start fresh).
k8s-setup: k8s-up
	@# --- Registry credentials (idempotent, auto-generated on first run) ---
	@if ! $(KUBECTL) get secret registry-htpasswd -n $(K8S_NS) >/dev/null 2>&1; then \
	    echo "Generating registry credentials..."; \
	    PASS=$$(python3 -c "import secrets; print(secrets.token_urlsafe(32))"); \
	    HTPASSWD=$$(docker run --rm httpd:2-alpine htpasswd -bBn kaniko "$$PASS"); \
	    $(KUBECTL) create secret generic registry-htpasswd \
	        --from-literal=htpasswd="$$HTPASSWD" --namespace=$(K8S_NS); \
	    $(KUBECTL) create secret docker-registry registry-creds \
	        --docker-server=registry.$(K8S_NS).svc:5000 \
	        --docker-username=kaniko --docker-password="$$PASS" \
	        --namespace=$(K8S_NS); \
	    $(KUBECTL) create secret generic registry-password \
	        --from-literal=password="$$PASS" --namespace=$(K8S_NS); \
	else \
	    echo "Registry credentials already exist — skipping"; \
	fi
	@# --- Configure Kind nodes for plain-HTTP pulls from the in-cluster registry ---
	@REGISTRY_IP=$$($(KUBECTL) get svc registry -n $(K8S_NS) -o jsonpath='{.spec.clusterIP}' 2>/dev/null || echo ""); \
	for node in $$(kind get nodes --name $(K8S_CLUSTER)); do \
	    docker exec $$node mkdir -p /etc/containerd/certs.d/registry.$(K8S_NS).svc:5000; \
	    printf '[host."http://registry.$(K8S_NS).svc:5000"]\n' | \
	        docker exec -i $$node tee /etc/containerd/certs.d/registry.$(K8S_NS).svc:5000/hosts.toml > /dev/null; \
	    docker exec $$node sh -c \
	        "grep -q 'registry.$(K8S_NS).svc' /etc/hosts || echo '$$REGISTRY_IP registry.$(K8S_NS).svc' >> /etc/hosts"; \
	done
	$(MAKE) k8s-images
	$(MAKE) k8s-network
	@# --- Screener secrets from validator/.env ---
	@if [ ! -f validator/.env ]; then \
	    echo "ERROR: validator/.env not found. Copy validator/.env.example and fill in values."; \
	    exit 1; \
	fi
	$(KUBECTL) create secret generic ridges-screener-secrets \
	    --from-env-file=validator/.env --namespace=$(K8S_NS) \
	    --dry-run=client -o yaml | $(KUBECTL) apply -f -
	@echo ""
	@echo "Kind cluster ready — context: $(K8S_CTX)"
	@echo "  Run:   make k8s-screener"
	@echo "  Scale: $(KUBECTL) scale sts ridges-screener-1 --replicas=1 -n $(K8S_NS)"

## Run screener locally against the kind cluster
k8s-screener:
	RIDGES_ENVIRONMENT_TYPE=kubernetes \
	K8S_CONTEXT=$(K8S_CTX) \
	K8S_NAMESPACE=$(K8S_NS) \
	K8S_REGISTRY=registry.$(K8S_NS).svc:5000 \
	uv run python -m validator.main

