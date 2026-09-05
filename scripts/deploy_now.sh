#!/usr/bin/env bash
# Manual deploy of the committed HEAD via Cloud Build (same path as Nikki's `deploy_self` tool).
# Usage: PROJECT=nikkiaia-prod REGION=us-east4 bash scripts/deploy_now.sh
set -euo pipefail
PROJECT=${PROJECT:-nikkiaia-prod}; REGION=${REGION:-us-east4}
TAG="$(git rev-parse --short HEAD)-$(date +%Y%m%d-%H%M%S)"
STAGE=$(mktemp -d)
git archive --format=tar HEAD | tar -x -C "$STAGE"
cp cloudbuild.yaml "$STAGE/cloudbuild.yaml"
echo "== Cloud Build: tag $TAG"
gcloud builds submit "$STAGE" --config "$STAGE/cloudbuild.yaml" --project "$PROJECT" --region "$REGION" \
  --substitutions "_REGION=$REGION,_TAG=$TAG" -q
rm -rf "$STAGE"
echo "deploy done: $(gcloud run services describe nikki --region "$REGION" --project "$PROJECT" --format='value(status.latestReadyRevisionName)')"
