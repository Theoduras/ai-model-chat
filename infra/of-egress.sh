#!/usr/bin/env bash
# Fixed-IP egress for OnlyFans sign-in + API calls, run entirely inside GCP.
#
# Why this exists: OnlyFans binds a session to the IP it was opened on. Cloud
# Run without VPC egress draws from a shared Google pool per request, so the
# session that just signed in gets dropped on the very next call. This script
# puts one small VM with one reserved static IP behind both Cloud Run services
# (app + browser) via Direct VPC egress, and points ONLYFANS_PROXY_TEMPLATE at
# it. See the plan / ENVIRONMENTS.md for the full reasoning.
#
# Usage: infra/of-egress.sh {create|wire|status|destroy}
#
# Nothing here touches application code or deploys an image — it only creates
# infrastructure and sets Cloud Run service env vars, which is the only place
# those can be set at all (see ENVIRONMENTS.md: the develop trigger only ever
# passes --image).

set -euo pipefail

REGION="${OF_EGRESS_REGION:-europe-west4}"
ZONE="${OF_EGRESS_ZONE:-${REGION}-a}"
PROJECT="${OF_EGRESS_PROJECT:-$(gcloud config get-value project 2>/dev/null)}"
NETWORK="of-egress"
SUBNET="of-egress-subnet"
SUBNET_RANGE="10.90.0.0/24"
VM_NAME="of-egress-proxy"
IP_NAME="of-egress-ip"
FW_NAME="of-egress-allow-proxy"
PROXY_PORT=3128
PROXY_USER="of"
SECRET_NAME="of-egress-proxy-password"
APP_SERVICE="ai-model-chat-dev"
BROWSER_SERVICE="ai-model-chat-dev-browser"

log() { echo "==> $*" >&2; }
run() { log "$*"; "$@"; }

require_project() {
  if [[ -z "$PROJECT" ]]; then
    echo "No GCP project set. Run: gcloud config set project <id>" >&2
    exit 1
  fi
}

cmd_create() {
  require_project

  if ! gcloud compute networks describe "$NETWORK" --project="$PROJECT" >/dev/null 2>&1; then
    run gcloud compute networks create "$NETWORK" --project="$PROJECT" \
      --subnet-mode=custom --bgp-routing-mode=regional
  else
    log "network $NETWORK already exists, skipping"
  fi

  if ! gcloud compute networks subnets describe "$SUBNET" --region="$REGION" --project="$PROJECT" >/dev/null 2>&1; then
    run gcloud compute networks subnets create "$SUBNET" --project="$PROJECT" \
      --network="$NETWORK" --region="$REGION" --range="$SUBNET_RANGE"
  else
    log "subnet $SUBNET already exists, skipping"
  fi

  if ! gcloud compute firewall-rules describe "$FW_NAME" --project="$PROJECT" >/dev/null 2>&1; then
    run gcloud compute firewall-rules create "$FW_NAME" --project="$PROJECT" \
      --network="$NETWORK" --direction=INGRESS --action=ALLOW \
      --rules="tcp:${PROXY_PORT}" --source-ranges="$SUBNET_RANGE"
  else
    log "firewall rule $FW_NAME already exists, skipping"
  fi

  if ! gcloud compute addresses describe "$IP_NAME" --region="$REGION" --project="$PROJECT" >/dev/null 2>&1; then
    run gcloud compute addresses create "$IP_NAME" --project="$PROJECT" --region="$REGION"
  else
    log "static IP $IP_NAME already exists, skipping"
  fi
  EXTERNAL_IP=$(gcloud compute addresses describe "$IP_NAME" --region="$REGION" --project="$PROJECT" --format='value(address)')
  log "reserved static IP: $EXTERNAL_IP"

  if gcloud secrets describe "$SECRET_NAME" --project="$PROJECT" >/dev/null 2>&1; then
    log "secret $SECRET_NAME already exists, reusing existing password"
    PROXY_PASS=$(gcloud secrets versions access latest --secret="$SECRET_NAME" --project="$PROJECT")
  else
    PROXY_PASS=$(python3 -c 'import secrets;print(secrets.token_urlsafe(24))')
    printf '%s' "$PROXY_PASS" | run gcloud secrets create "$SECRET_NAME" --project="$PROJECT" --data-file=-
  fi

  HTPASSWD_LINE=$(python3 - "$PROXY_USER" "$PROXY_PASS" <<'PY'
import crypt, sys
user, pw = sys.argv[1], sys.argv[2]
print(f"{user}:{crypt.crypt(pw, crypt.mksalt(crypt.METHOD_SHA256))}")
PY
)

  STARTUP=$(mktemp)
  trap 'rm -f "$STARTUP"' EXIT
  cat > "$STARTUP" <<EOF
#!/bin/bash
set -e
apt-get update -y
apt-get install -y squid apache2-utils
echo '${HTPASSWD_LINE}' > /etc/squid/passwd
cat > /etc/squid/squid.conf <<'SQUIDCONF'
http_port ${PROXY_PORT}
auth_param basic program /usr/lib/squid/basic_ncsa_auth /etc/squid/passwd
auth_param basic realm proxy
acl authenticated proxy_auth REQUIRED
http_access allow authenticated
http_access deny all
cache deny all
via off
forwarded_for delete
SQUIDCONF
systemctl restart squid
systemctl enable squid
EOF

  # A VM that exists but is not running is worse than one that does not exist:
  # wiring points both services at a proxy nothing answers on, which takes
  # OnlyFans offline entirely rather than leaving it as it was.
  VM_STATE=$(gcloud compute instances describe "$VM_NAME" --zone="$ZONE" \
    --project="$PROJECT" --format='value(status)' 2>/dev/null || true)
  case "$VM_STATE" in
    SUSPENDED)
      run gcloud compute instances resume "$VM_NAME" --zone="$ZONE" --project="$PROJECT"
      ;;
    TERMINATED|STOPPED)
      run gcloud compute instances start "$VM_NAME" --zone="$ZONE" --project="$PROJECT"
      ;;
  esac
  if ! gcloud compute instances describe "$VM_NAME" --zone="$ZONE" --project="$PROJECT" >/dev/null 2>&1; then
    run gcloud compute instances create "$VM_NAME" --project="$PROJECT" --zone="$ZONE" \
      --machine-type=e2-micro \
      --network="$NETWORK" --subnet="$SUBNET" \
      --address="$IP_NAME" \
      --image-family=debian-12 --image-project=debian-cloud \
      --metadata-from-file=startup-script="$STARTUP"
  else
    log "VM $VM_NAME already exists, skipping create (re-run startup by hand if config changed)"
  fi

  INTERNAL_IP=$(gcloud compute instances describe "$VM_NAME" --zone="$ZONE" --project="$PROJECT" \
    --format='value(networkInterfaces[0].networkIP)')

  echo
  echo "Created. Internal IP: $INTERNAL_IP   External IP: $EXTERNAL_IP"
  echo "Password stored in Secret Manager as: $SECRET_NAME"
  echo "Next: infra/of-egress.sh wire"
}

cmd_wire() {
  require_project

  INTERNAL_IP=$(gcloud compute instances describe "$VM_NAME" --zone="$ZONE" --project="$PROJECT" \
    --format='value(networkInterfaces[0].networkIP)')
  PROXY_PASS=$(gcloud secrets versions access latest --secret="$SECRET_NAME" --project="$PROJECT")
  TEMPLATE="http://${PROXY_USER}:${PROXY_PASS}@${INTERNAL_IP}:${PROXY_PORT}"

  for SVC in "$APP_SERVICE" "$BROWSER_SERVICE"; do
    log "wiring $SVC to $NETWORK/$SUBNET, private-ranges-only egress"
    run gcloud run services update "$SVC" --project="$PROJECT" --region="$REGION" \
      --network="$NETWORK" --subnet="$SUBNET" --vpc-egress=private-ranges-only \
      --update-env-vars="ONLYFANS_PROXY_TEMPLATE=${TEMPLATE}"
  done

  echo
  echo "Wired. Both services now route OnlyFans traffic through $INTERNAL_IP -> $VM_NAME."
  echo "Verify: GET /api/onlyfans/debug/store?persona=<slug> should show proxy_pool: true"
}

cmd_status() {
  VM_STATE=$(gcloud compute instances describe "$VM_NAME" --zone="$ZONE" \
    --project="$PROJECT" --format='value(status)' 2>/dev/null || true)
  if [[ -n "$VM_STATE" && "$VM_STATE" != "RUNNING" ]]; then
    echo "!! The proxy VM is $VM_STATE. Both services send OnlyFans traffic"
    echo "!! through it, so nothing will reach OnlyFans until it is running:"
    echo "!!   gcloud compute instances resume $VM_NAME --zone=$ZONE"
    echo
  fi
  require_project

  echo "-- VM --"
  gcloud compute instances describe "$VM_NAME" --zone="$ZONE" --project="$PROJECT" \
    --format='table(name,status,networkInterfaces[0].networkIP,networkInterfaces[0].accessConfigs[0].natIP)' 2>/dev/null \
    || echo "not created (run: infra/of-egress.sh create)"

  echo
  echo "-- Cloud Run egress --"
  for SVC in "$APP_SERVICE" "$BROWSER_SERVICE"; do
    echo "$SVC:"
    gcloud run services describe "$SVC" --project="$PROJECT" --region="$REGION" \
      --format='value(spec.template.metadata.annotations."run.googleapis.com/network-interfaces")' 2>/dev/null \
      || echo "  (not found)"
    gcloud run services describe "$SVC" --project="$PROJECT" --region="$REGION" \
      --format='value(spec.template.spec.containers[0].env.filter("name:ONLYFANS_PROXY_TEMPLATE").list())' 2>/dev/null \
      | sed -E 's/:[^@]+@/:***@/' || true
  done

  echo
  echo "-- Proxy reachability (from Cloud Shell / this machine, if on the VPC) --"
  echo "Run from a machine on the VPC: curl -x http://of:<password>@<internal-ip>:${PROXY_PORT} -sS https://onlyfans.com -o /dev/null -w '%{http_code}\\n'"
}

cmd_destroy() {
  require_project
  echo "This will delete the VM, static IP, firewall rule, subnet and network."
  read -r -p "Type the VM name ($VM_NAME) to confirm: " CONFIRM
  if [[ "$CONFIRM" != "$VM_NAME" ]]; then
    echo "Aborted."
    exit 1
  fi

  gcloud compute instances delete "$VM_NAME" --zone="$ZONE" --project="$PROJECT" --quiet 2>/dev/null || true
  gcloud compute firewall-rules delete "$FW_NAME" --project="$PROJECT" --quiet 2>/dev/null || true
  gcloud compute addresses delete "$IP_NAME" --region="$REGION" --project="$PROJECT" --quiet 2>/dev/null || true
  gcloud compute networks subnets delete "$SUBNET" --region="$REGION" --project="$PROJECT" --quiet 2>/dev/null || true
  gcloud compute networks delete "$NETWORK" --project="$PROJECT" --quiet 2>/dev/null || true

  echo "Destroyed. Cloud Run services still reference the network until you re-run wire against"
  echo "a new one, or clear it by hand: gcloud run services update <svc> --clear-network --clear-vpc-egress"
}

case "${1:-}" in
  create)  cmd_create ;;
  wire)    cmd_wire ;;
  status)  cmd_status ;;
  destroy) cmd_destroy ;;
  *)
    echo "Usage: $0 {create|wire|status|destroy}" >&2
    exit 1
    ;;
esac
