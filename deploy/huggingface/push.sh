#!/usr/bin/env bash
# Publish ChainLens to a Hugging Face Space.
#
#   1. Create a Space at https://huggingface.co/new-space
#        SDK: Docker   |   Visibility: Public
#   2. Create a WRITE token at https://huggingface.co/settings/tokens
#   3. Run:  ./deploy/huggingface/push.sh <username> <space-name>
#
# The Space README front matter sets app_port=8000, which is how the platform knows
# which port the container listens on.
set -euo pipefail

USER="${1:?usage: push.sh <hf-username> <space-name>}"
SPACE="${2:?usage: push.sh <hf-username> <space-name>}"
REMOTE="https://huggingface.co/spaces/${USER}/${SPACE}"

ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
STAGE="$(mktemp -d)"
trap 'rm -rf "$STAGE"' EXIT

echo "Staging deployable files in $STAGE"
cd "$ROOT"

# Only what the image needs. The supplied dataset and its answer key are deliberately
# excluded: they are never published to a public Space.
mkdir -p "$STAGE/backend" "$STAGE/frontend" "$STAGE/data/samples"
cp Dockerfile .dockerignore "$STAGE/"
cp deploy/huggingface/README.md "$STAGE/README.md"
cp -r backend/chainlens backend/requirements.txt backend/model_artifacts "$STAGE/backend/"
cp -r frontend/src frontend/package.json frontend/package-lock.json \
      frontend/index.html frontend/tsconfig.json frontend/vite.config.ts "$STAGE/frontend/"
cp data/samples/demo_mixed_patterns.csv "$STAGE/data/samples/"

cd "$STAGE"
git init -q
git add -A
git commit -qm "Deploy ChainLens"
git branch -M main
git remote add origin "$REMOTE"

echo
echo "Pushing to $REMOTE"
echo "When prompted: username = your HF username, password = your WRITE token."
git push -f origin main

cat <<NOTE

Done. Next:

  1. Open ${REMOTE}/settings
  2. Under "Variables and secrets", add a secret:
        CHAINLENS_AUTH_PASSWORD = <a long password, 12+ characters>
  3. Restart the Space.

The first build takes about five minutes.

Then check, in order:
  - ${REMOTE#https://huggingface.co/spaces/} URL returns a PASSWORD SCREEN, not the app.
  - If you see the application instead, the secret is not set. Fix that before sharing.
NOTE
