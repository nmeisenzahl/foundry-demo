#!/usr/bin/env bash
set -euo pipefail

registry_name="${1:?usage: publish-hosted-image.sh <registry-name> <agent-name> [tag] [env-file]}"
agent_name="${2:?usage: publish-hosted-image.sh <registry-name> <agent-name> [tag] [env-file]}"
unique_id="$(python3 -c 'import uuid; print(uuid.uuid4().hex[:12])')"
default_label="$(date -u +%Y%m%d%H%M%S)-$(git rev-parse --short HEAD 2>/dev/null || printf 'uncommitted')"
tag_label="${3:-$default_label}"
env_file="${4:-artifacts/images/${agent_name}.env}"

if [[ ! "$agent_name" =~ ^[a-z0-9]+(-[a-z0-9]+)*$ ]]; then
  echo "Agent name must be lowercase kebab-case: ${agent_name}" >&2
  exit 2
fi
repository="$agent_name"
# Package directories are the snake_case form of the registered agent name.
context="src/agents/${agent_name//-/_}"
env_prefix="FOUNDRY_$(printf '%s' "$agent_name" | tr 'a-z-' 'A-Z_')"

if [[ ! -f "${context}/Dockerfile" ]]; then
  echo "No Dockerfile found for agent ${agent_name} at ${context}." >&2
  exit 2
fi
if [[ "$tag_label" == "latest" ]]; then
  echo "Refusing mutable image tag label 'latest'." >&2
  exit 2
fi
tag="${tag_label}-${unique_id}"

docker buildx version >/dev/null
az acr login --name "$registry_name" >/dev/null
login_server="$(az acr show --name "$registry_name" --query loginServer -o tsv)"
image="${login_server}/${repository}:${tag}"

echo "Building and pushing ${image}" >&2
docker buildx build --platform linux/amd64 --push -t "$image" "$context" >&2
digest="$(docker buildx imagetools inspect "$image" --format '{{.Manifest.Digest}}')"
if [[ ! "$digest" =~ ^sha256:[0-9a-fA-F]{64}$ ]]; then
  echo "Unable to resolve a sha256 digest for ${image}." >&2
  exit 3
fi

mkdir -p "$(dirname "$env_file")"
tmp="${env_file}.tmp.$$"
printf '%s_IMAGE=%s\n%s_IMAGE_DIGEST=%s\n' \
  "$env_prefix" "$image" "$env_prefix" "$digest" > "$tmp"
mv "$tmp" "$env_file"
echo "Published ${image}@${digest}" >&2
printf '%s\n' "$env_file"
