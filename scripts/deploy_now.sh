#!/bin/bash
# Immediate deployment script - triggers Cloud Build manually
# Use this to deploy the current committed code without setting up automated triggers

set -e

PROJECT=${PROJECT:-nikkiaia-prod}
REGION=${REGION:-us-east4}

echo "Deploying Nikki to Cloud Run..."
echo "Project: $PROJECT"
echo "Region: $REGION"
echo ""

# Ensure we're using the correct project
gcloud config set project "$PROJECT"

# Enable required APIs if not already enabled
gcloud services enable cloudbuild.googleapis.com --quiet
gcloud services enable run.googleapis.com --quiet
gcloud services enable containerregistry.googleapis.com --quiet

# Get the Cloud Build service account and grant permissions
PROJECT_NUMBER=$(gcloud projects describe "$PROJECT" --format="value(projectNumber)")
CLOUD_BUILD_SA="${PROJECT_NUMBER}@cloudbuild.gserviceaccount.com"

echo "Granting permissions to Cloud Build service account..."
gcloud projects add-iam-policy-binding "$PROJECT" \
  --member="serviceAccount:$CLOUD_BUILD_SA" \
  --role="roles/run.admin" \
  --condition=None \
  --quiet 2>/dev/null || true

gcloud projects add-iam-policy-binding "$PROJECT" \
  --member="serviceAccount:$CLOUD_BUILD_SA" \
  --role="roles/iam.serviceAccountUser" \
  --condition=None \
  --quiet 2>/dev/null || true

# Submit the build
echo ""
echo "Submitting build to Cloud Build..."
echo "(This will take 3-5 minutes)"
echo ""

gcloud builds submit \
  --config=cloudbuild.yaml \
  --region="$REGION" \
  --substitutions="_REGION=$REGION" \
  .

echo ""
echo "✅ Deployment complete!"
echo ""
echo "Your Nikki instance should now be running with voice capabilities."
echo "Visit: https://nikkiaia.com"
echo ""
