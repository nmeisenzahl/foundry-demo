#!/usr/bin/env bash
set -euo pipefail

registry_name="${1:?usage: publish-hosted-image.sh <registry-name> [tag] [env-file]}"
unique_id="$(python3 -c 'import uuid; print(uuid.uuid4().hex[:12])')"
default_label="$(date -u +%Y%m%d%H%M%S)-$(git rev-parse --short HEAD 2>/dev/null || printf 'uncommitted')"
tag_label="${2:-$default_label}"
env_file="${3:-artifacts/images/architecture-advisor.env}"
repository="architecture-advisor"

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
docker buildx build --platform linux/amd64 --push -t "$image" src/agents/architecture_advisor >&2
digest="$(docker buildx imagetools inspect "$image" --format '{{.Manifest.Digest}}')"
if [[ ! "$digest" =~ ^sha256:[0-9a-fA-F]{64}$ ]]; then
  echo "Unable to resolve a sha256 digest for ${image}." >&2
  exit 3
fi

mkdir -p "$(dirname "$env_file")"
tmp="${env_file}.tmp.$$"
printf 'FOUNDRY_ARCHITECTURE_ADVISOR_IMAGE=%s\nFOUNDRY_ARCHITECTURE_ADVISOR_IMAGE_DIGEST=%s\n' "$image" "$digest" > "$tmp"
mv "$tmp" "$env_file"
echo "Published ${image}@${digest}" >&2
printf '%s\n' "$env_file"
