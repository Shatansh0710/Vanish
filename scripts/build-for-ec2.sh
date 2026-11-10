#!/usr/bin/env bash
# Build the Vanish Docker images for EC2 and pack the whole project into one file to carry.
#
# Run on the Mac (or any computer with Docker Desktop), from anywhere:
#   bash scripts/build-for-ec2.sh
#
# Output:
#   dist/vanish-images.tar.gz      both images (app + caddy) for linux/amd64, ready for "docker load"
#   ../vanish-transfer.zip         the whole project folder including dist/, to copy to the other laptop
#
# Why linux/amd64: a t3.micro EC2 instance has an Intel/AMD (x86_64) processor. Apple Silicon Macs
# build arm64 images by default, which would fail on t3.micro with "exec format error".
set -euo pipefail
cd "$(dirname "$0")/.."
PROJECT_DIR="$(pwd)"
PROJECT_NAME="$(basename "$PROJECT_DIR")"

PLATFORM="linux/amd64"
APP_IMAGE="vanish-app:1.0"
CADDY_IMAGE="caddy:2.11"
OUT="dist/vanish-images.tar.gz"
ZIP="$(dirname "$PROJECT_DIR")/vanish-transfer.zip"

fail() { echo "ERROR: $*" >&2; exit 1; }

command -v docker >/dev/null 2>&1 || fail "Docker is not installed. Install Docker Desktop, open it, then run this again."
docker info >/dev/null 2>&1 || fail "Docker is not running. Open Docker Desktop, wait for 'Engine running', then run this again."
docker buildx version >/dev/null 2>&1 || fail "docker buildx is missing. Update Docker Desktop to the latest version."
command -v zip >/dev/null 2>&1 || fail "The 'zip' command is missing."

echo "This computer: $(uname -s) $(uname -m). Building for EC2: $PLATFORM"
echo

echo "[1/5] Building $APP_IMAGE ..."
docker buildx build --platform "$PLATFORM" --tag "$APP_IMAGE" --load .

echo
echo "[2/5] Downloading $CADDY_IMAGE ..."
docker pull --platform "$PLATFORM" "$CADDY_IMAGE"

echo
echo "[3/5] Checking both images are $PLATFORM ..."
for img in "$APP_IMAGE" "$CADDY_IMAGE"; do
  arch=$(docker image inspect --platform "$PLATFORM" --format '{{.Os}}/{{.Architecture}}' "$img" 2>/dev/null \
         || docker image inspect --format '{{.Os}}/{{.Architecture}}' "$img")
  [ "$arch" = "$PLATFORM" ] || fail "$img is $arch, expected $PLATFORM."
  echo "  $img  ->  $arch"
done

echo
echo "[4/5] Saving both images to $OUT ..."
mkdir -p dist
rm -f "$OUT"
if ! docker save --platform "$PLATFORM" "$APP_IMAGE" "$CADDY_IMAGE" 2>/dev/null | gzip > "$OUT"; then
  docker save "$APP_IMAGE" "$CADDY_IMAGE" | gzip > "$OUT"   # older Docker without --platform on save
fi
gzip -t "$OUT" || fail "$OUT is damaged; run the script again."
echo "  $(du -h "$OUT" | cut -f1)  $OUT"

echo
echo "[5/5] Packing the project into $ZIP ..."
rm -f "$ZIP"
( cd .. && zip -qr "$ZIP" "$PROJECT_NAME" \
    -x "$PROJECT_NAME/.venv/*" "$PROJECT_NAME/*__pycache__*" "$PROJECT_NAME/.pytest_cache/*" \
       "$PROJECT_NAME/.env" "$PROJECT_NAME/*.db" "$PROJECT_NAME/*.db-*" "*.DS_Store" "*.pem" )
echo "  $(du -h "$ZIP" | cut -f1)  $ZIP"

cat <<EOF

Done. Copy this one file to the other laptop (USB drive, Google Drive, etc.):
  $ZIP

It contains the code, the slides, the guide, and dist/vanish-images.tar.gz (the ready-built images).
EOF
