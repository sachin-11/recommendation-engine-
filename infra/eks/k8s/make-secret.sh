#!/usr/bin/env bash
# Creates the Secret `recoengine-secrets` in the cluster: the API keys copied from the
# repo's .env, and a fresh SECRET_KEY and Postgres password made here. Nothing is printed
# or written to disk; the values live only in the cluster and go away with it.
set -euo pipefail

ENV_FILE="$(git rev-parse --show-toplevel)/.env"
get() { grep -E "^$1=" "$ENV_FILE" | head -1 | cut -d= -f2- | tr -d '\r'; }
random() { python -c "import secrets; print(secrets.token_urlsafe($1))"; }

PG_USER=reco
PG_DB=recoengine
PG_PASSWORD=$(random 24)

kubectl create namespace recoengine --dry-run=client -o yaml | kubectl apply -f - >/dev/null
kubectl -n recoengine create secret generic recoengine-secrets \
  --from-literal=SECRET_KEY="$(random 64)" \
  --from-literal=POSTGRES_USER="$PG_USER" \
  --from-literal=POSTGRES_PASSWORD="$PG_PASSWORD" \
  --from-literal=POSTGRES_DB="$PG_DB" \
  --from-literal=DATABASE_URL="postgresql+asyncpg://$PG_USER:$PG_PASSWORD@postgres:5432/$PG_DB" \
  --from-literal=OPENAI_API_KEY="$(get OPENAI_API_KEY)" \
  --from-literal=PINECONE_API_KEY="$(get PINECONE_API_KEY)" \
  --from-literal=PINECONE_INDEX_NAME="$(get PINECONE_INDEX_NAME)" \
  --from-literal=PINECONE_ENVIRONMENT="$(get PINECONE_ENVIRONMENT)" \
  --from-literal=PINECONE_CLOUD="$(get PINECONE_CLOUD)" \
  --from-literal=EMBEDDING_MODEL="$(get EMBEDDING_MODEL)" \
  --from-literal=EMBEDDING_DIMENSION="$(get EMBEDDING_DIMENSION)" \
  --dry-run=client -o yaml | kubectl apply -f - >/dev/null
echo "Secret recoengine-secrets is in place ($(kubectl -n recoengine get secret recoengine-secrets -o jsonpath='{.data}' | python -c 'import json,sys; print(len(json.load(sys.stdin)))') keys)."
