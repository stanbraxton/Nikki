#!/usr/bin/env bash
# Build (Cloud Build) and deploy Nikki to Cloud Run; map the custom domain.
# Usage: PROJECT=nikki-prod REGION=us-east4 DOMAIN=nikkiaia.com bash scripts/deploy.sh
set -euo pipefail
: "${PROJECT:?}"
REGION="${REGION:-us-east4}"; DOMAIN="${DOMAIN:-nikkiaia.com}"
SERVICE="${SERVICE:-nikki}"; REPO="${REPO:-nikki}"; SQL_INSTANCE="${SQL_INSTANCE:-nikki-pg}"
BUCKET="${BUCKET:-${PROJECT}-nikki-data}"
RUNTIME_SA="nikki-runtime@${PROJECT}.iam.gserviceaccount.com"
IMAGE="${REGION}-docker.pkg.dev/${PROJECT}/${REPO}/${SERVICE}:$(date +%Y%m%d-%H%M%S)"
CONN=$(gcloud sql instances describe "$SQL_INSTANCE" --format='value(connectionName)')

echo "== build $IMAGE"
gcloud builds submit --tag "$IMAGE" -q .

echo "== deploy"
gcloud run deploy "$SERVICE" --image "$IMAGE" --region "$REGION" --platform managed \
  --service-account "$RUNTIME_SA" --allow-unauthenticated \
  --cpu 1 --memory 1Gi --min-instances "${MIN_INSTANCES:-1}" --max-instances 2 --concurrency 20 \
  --timeout 3600 --session-affinity \
  --add-cloudsql-instances "$CONN" \
  --add-volume name=data,type=cloud-storage,bucket="$BUCKET" \
  --add-volume-mount volume=data,mount-path=/mnt/data \
  --set-env-vars "NIKKI_ENV=prod,ADMIN_USERNAME=stan,NIKKI_WORKSPACE_DIR=/mnt/data/workspace,NIKKI_SKILLS_DIR=/mnt/data/skills,NIKKI_MODEL=${NIKKI_MODEL:-anthropic:claude-sonnet-4-5},SPACES_PROJECT=${PROJECT},SPACES_REGION=${REGION},SPACES_SERVICE_ACCOUNT=nikki-spaces@${PROJECT}.iam.gserviceaccount.com,SCHEDULER_SERVICE_ACCOUNT=nikki-scheduler@${PROJECT}.iam.gserviceaccount.com,PUBLIC_URL=https://${DOMAIN},SIGNUPS_ENABLED=${SIGNUPS_ENABLED:-true},SIGNUP_CODE=${SIGNUP_CODE:-}" \
  --set-secrets "ANTHROPIC_API_KEY=ANTHROPIC_API_KEY:latest,OPENAI_API_KEY=OPENAI_API_KEY:latest,ADMIN_PASSWORD_HASH=ADMIN_PASSWORD_HASH:latest,CHAINLIT_AUTH_SECRET=CHAINLIT_AUTH_SECRET:latest,ADMIN_API_TOKEN=ADMIN_API_TOKEN:latest,DATABASE_URL=DATABASE_URL:latest,GOOGLE_OAUTH_CLIENT_ID=GOOGLE_OAUTH_CLIENT_ID:latest,GOOGLE_OAUTH_CLIENT_SECRET=GOOGLE_OAUTH_CLIENT_SECRET:latest,TAVILY_API_KEY=TAVILY_API_KEY:latest" \
  -q
URL=$(gcloud run services describe "$SERVICE" --region "$REGION" --format='value(status.url)')
echo "service url: $URL"

echo "== domain mapping $DOMAIN"
gcloud beta run domain-mappings describe --domain "$DOMAIN" --region "$REGION" >/dev/null 2>&1 || \
  gcloud beta run domain-mappings create --service "$SERVICE" --domain "$DOMAIN" --region "$REGION" -q
gcloud beta run domain-mappings describe --domain "$DOMAIN" --region "$REGION" --format='yaml(status.resourceRecords)'
echo "deploy done"
