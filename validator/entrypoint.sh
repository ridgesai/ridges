#!/bin/sh
export SCREENER_NAME="${SCREENER_NAME:-$HOSTNAME}"
mkdir -p /workspace/tmp

# Resolve the gateway hostname to a ClusterIP so the
# DinD-internal proxy container can reach it (no K8s DNS inside DinD).
if [ "$DIND_RESOLVE_GATEWAY" = "true" ] && [ -n "$RIDGES_INFERENCE_GATEWAY_URL" ]; then
  GW_HOST=$(echo "$RIDGES_INFERENCE_GATEWAY_URL" | sed -E 's|^https?://||;s|[:/].*||')
  GW_IP=$(getent hosts "$GW_HOST" | awk '{print $1}')
  if [ -n "$GW_IP" ]; then
    export RIDGES_INFERENCE_GATEWAY_URL=$(echo "$RIDGES_INFERENCE_GATEWAY_URL" | sed "s|$GW_HOST|$GW_IP|")
    export RIDGES_INFERENCE_GATEWAY_HOST="$GW_HOST"
    echo "Resolved gateway $GW_HOST -> $GW_IP"
  fi
fi

trap 'kill -TERM $PID; wait $PID' TERM
python -m validator.main &
PID=$!
wait $PID
