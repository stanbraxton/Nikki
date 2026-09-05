#!/bin/bash
# Setup Cloud Build trigger for automated Nikki deployment
# Run this once to enable CI/CD on push to main branch

set -e

PROJECT=${PROJECT:-nikkiaia-prod}
REGION=${REGION:-us-east4}
REPO_OWNER="stanbraxton"
REPO_NAME="Nikki"
TRIGGER_NAME="nikki-deploy-main"

echo "Setting up Cloud Build trigger for $REPO_OWNER/$REPO_NAME"
echo "Project: $PROJECT"
echo "Region: $REGION"

# Ensure we're using the correct project
gcloud config set project "$PROJECT"

# Enable required APIs
echo "Enabling required APIs..."
gcloud services enable cloudbuild.googleapis.com
gcloud services enable run.googleapis.com
gcloud services enable containerregistry.googleapis.com

# Grant Cloud Build service account permissions
PROJECT_NUMBER=$(gcloud projects describe "$PROJECT" --format="value(projectNumber)")
CLOUD_BUILD_SA="${PROJECT_NUMBER}@cloudbuild.gserviceaccount.com"

echo "Granting permissions to Cloud Build service account: $CLOUD_BUILD_SA"

gcloud projects add-iam-policy-binding "$PROJECT" \
  --member="serviceAccount:$CLOUD_BUILD_SA" \
  --role="roles/run.admin" \
  --condition=None

gcloud projects add-iam-policy-binding "$PROJECT" \
  --member="serviceAccount:$CLOUD_BUILD_SA" \
  --role="roles/iam.serviceAccountUser" \
  --condition=None

# Check if GitHub connection exists, if not provide instructions
echo ""
echo "Checking for GitHub connection..."
CONNECTIONS=$(gcloud builds connections list --region="$REGION" --format="value(name)" 2>/dev/null | grep github || true)

if [ -z "$CONNECTIONS" ]; then
  echo ""
  echo "⚠️  No GitHub connection found. You need to connect GitHub first:"
  echo ""
  echo "1. Go to: https://console.cloud.google.com/cloud-build/triggers/connect?project=$PROJECT"
  echo "2. Select 'GitHub (Cloud Build GitHub App)'"
  echo "3. Authenticate with GitHub"
  echo "4. Select repository: $REPO_OWNER/$REPO_NAME"
  echo "5. Re-run this script"
  echo ""
  exit 1
fi

# Get the first connection name
CONNECTION_NAME=$(echo "$CONNECTIONS" | head -n1 | xargs basename)
echo "Using GitHub connection: $CONNECTION_NAME"

# Create the trigger
echo "Creating Cloud Build trigger: $TRIGGER_NAME"

gcloud builds triggers create github \
  --name="$TRIGGER_NAME" \
  --region="$REGION" \
  --repo-name="$REPO_NAME" \
  --repo-owner="$REPO_OWNER" \
  --branch-pattern="^main$" \
  --build-config="cloudbuild.yaml" \
  --description="Auto-deploy Nikki on push to main" \
  --substitutions="_REGION=$REGION" \
  2>/dev/null || echo "Trigger may already exist"

echo ""
echo "✅ Setup complete!"
echo ""
echo "Cloud Build will now automatically deploy Nikki whenever you push to main branch."
echo "Monitor builds at: https://console.cloud.google.com/cloud-build/builds?project=$PROJECT"
echo ""
