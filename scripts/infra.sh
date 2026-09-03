#!/usr/bin/env bash
# Nikki infra bootstrap on GCP. Idempotent; safe to re-run.
# Usage: PROJECT=nikki-prod REGION=us-east4 DOMAIN=nikkiaia.com KEY=/path/sa.json bash scripts/infra.sh
set -euo pipefail
export PATH=/work/tools/google-cloud-sdk/bin:$PATH
: "${PROJECT:?}"
REGION="${REGION:-us-east4}"
DOMAIN="${DOMAIN:-nikkiaia.com}"
SQL_INSTANCE="${SQL_INSTANCE:-nikki-pg}"
BUCKET="${BUCKET:-${PROJECT}-nikki-data}"
REPO="${REPO:-nikki}"
SERVICE="${SERVICE:-nikki}"
RUNTIME_SA="nikki-runtime@${PROJECT}.iam.gserviceaccount.com"

if [ -n "${KEY:-}" ]; then gcloud auth activate-service-account --key-file="$KEY" -q; fi
gcloud config set project "$PROJECT" -q
gcloud config set run/region "$REGION" -q

echo "== APIs"
gcloud services enable run.googleapis.com artifactregistry.googleapis.com secretmanager.googleapis.com \
  cloudbuild.googleapis.com sqladmin.googleapis.com storage.googleapis.com dns.googleapis.com \
  domains.googleapis.com iam.googleapis.com cloudresourcemanager.googleapis.com -q

echo "== Runtime service account"
gcloud iam service-accounts describe "$RUNTIME_SA" >/dev/null 2>&1 || \
  gcloud iam service-accounts create nikki-runtime --display-name="Nikki Cloud Run runtime" -q
for role in roles/secretmanager.secretAccessor roles/cloudsql.client roles/storage.objectAdmin; do
  gcloud projects add-iam-policy-binding "$PROJECT" --member="serviceAccount:$RUNTIME_SA" --role="$role" -q --condition=None >/dev/null
done

echo "== Artifact Registry"
gcloud artifacts repositories describe "$REPO" --location="$REGION" >/dev/null 2>&1 || \
  gcloud artifacts repositories create "$REPO" --repository-format=docker --location="$REGION" -q

echo "== Storage bucket (workspace + skills volume)"
gcloud storage buckets describe "gs://$BUCKET" >/dev/null 2>&1 || \
  gcloud storage buckets create "gs://$BUCKET" --location="$REGION" --uniform-bucket-level-access -q

echo "== Secrets (empty shells; values are added by the owner or the deploy step)"
for s in ANTHROPIC_API_KEY OPENAI_API_KEY ADMIN_PASSWORD_HASH CHAINLIT_AUTH_SECRET ADMIN_API_TOKEN DATABASE_URL; do
  gcloud secrets describe "$s" >/dev/null 2>&1 || gcloud secrets create "$s" --replication-policy=automatic -q
done

echo "== Spaces engine (Nikki deploys micro-apps to Cloud Run)"
SPACES_SA="nikki-spaces@${PROJECT}.iam.gserviceaccount.com"
PROJECT_NUMBER=$(gcloud projects describe "$PROJECT" --format='value(projectNumber)')
BUILD_SA="${PROJECT_NUMBER}-compute@developer.gserviceaccount.com"
gcloud services enable cloudbuild.googleapis.com -q
# Identity that every deployed Space runs as: zero roles (blast radius = the app itself).
gcloud iam service-accounts describe "$SPACES_SA" >/dev/null 2>&1 || \
  gcloud iam service-accounts create nikki-spaces --display-name="Nikki Spaces runtime (no roles)" -q
# Pre-create the repo gcloud uses for --source deploys so Nikki never needs artifactregistry.admin.
gcloud artifacts repositories describe cloud-run-source-deploy --location="$REGION" >/dev/null 2>&1 || \
  gcloud artifacts repositories create cloud-run-source-deploy --repository-format=docker --location="$REGION" -q
# Nikki's runtime SA: deploy Cloud Run services, submit builds, upload sources.
for role in roles/run.admin roles/cloudbuild.builds.editor roles/serviceusage.serviceUsageConsumer roles/storage.objectAdmin; do
  gcloud projects add-iam-policy-binding "$PROJECT" --member="serviceAccount:$RUNTIME_SA" --role="$role" -q --condition=None >/dev/null
done
# `gcloud run deploy --source` needs storage.buckets.get on the source-upload bucket (objectAdmin is not enough),
# so pre-create it and grant bucket-scoped storage.admin.
SRC_BUCKET="gs://run-sources-${PROJECT}-${REGION}"
gcloud storage buckets describe "$SRC_BUCKET" >/dev/null 2>&1 || gcloud storage buckets create "$SRC_BUCKET" --location="$REGION" --uniform-bucket-level-access -q
gcloud storage buckets add-iam-policy-binding "$SRC_BUCKET" --member="serviceAccount:$RUNTIME_SA" --role=roles/storage.admin -q >/dev/null
# gcloud also lists buckets by prefix before uploading -> project-level storage.buckets.list via a minimal custom role.
gcloud iam roles describe nikkiBucketLister --project "$PROJECT" >/dev/null 2>&1 || \
  gcloud iam roles create nikkiBucketLister --project "$PROJECT" --title "Nikki bucket lister" --permissions storage.buckets.list --stage GA -q >/dev/null
gcloud projects add-iam-policy-binding "$PROJECT" --member="serviceAccount:$RUNTIME_SA" --role="projects/$PROJECT/roles/nikkiBucketLister" -q --condition=None >/dev/null
gcloud artifacts repositories add-iam-policy-binding cloud-run-source-deploy --location="$REGION" \
  --member="serviceAccount:$RUNTIME_SA" --role=roles/artifactregistry.writer -q >/dev/null
# actAs scoped to two SAs only (never project-wide serviceAccountUser): the spaces SA (new services run as it)
# and the build SA (Cloud Run's builds:submit acts as it on Nikki's behalf).
for sa in "$SPACES_SA" "$BUILD_SA"; do
  gcloud iam service-accounts add-iam-policy-binding "$sa" \
    --member="serviceAccount:$RUNTIME_SA" --role=roles/iam.serviceAccountUser -q >/dev/null
done
# Cloud Build runs as the compute default SA in this project; it needs the builder bundle.
gcloud projects add-iam-policy-binding "$PROJECT" --member="serviceAccount:$BUILD_SA" --role=roles/cloudbuild.builds.builder -q --condition=None >/dev/null
gcloud artifacts repositories add-iam-policy-binding cloud-run-source-deploy --location="$REGION" \
  --member="serviceAccount:$BUILD_SA" --role=roles/artifactregistry.writer -q >/dev/null
echo "spaces IAM applied (runtime=$RUNTIME_SA, spaces=$SPACES_SA, build=$BUILD_SA)"

echo "== Cloud SQL Postgres (db-f1-micro, ~\$9/mo)"
if [ "${SKIP_SQL:-0}" = "1" ]; then echo "skipped (SKIP_SQL=1)"; echo "infra done (no sql)"; exit 0; fi
if ! gcloud sql instances describe "$SQL_INSTANCE" >/dev/null 2>&1; then
  gcloud sql instances create "$SQL_INSTANCE" --database-version=POSTGRES_16 --edition=enterprise --tier=db-f1-micro \
    --region="$REGION" --storage-size=10GB --storage-auto-increase --availability-type=zonal \
    --backup-start-time=07:00 -q
fi
gcloud sql databases describe nikki --instance="$SQL_INSTANCE" >/dev/null 2>&1 || \
  gcloud sql databases create nikki --instance="$SQL_INSTANCE" -q
CONN=$(gcloud sql instances describe "$SQL_INSTANCE" --format='value(connectionName)')
echo "SQL connection name: $CONN"

echo "infra done"
