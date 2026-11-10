#!/usr/bin/env bash
# Start (or update) Vanish on an EC2 instance. Run from the project folder on the instance:
#
#   bash deploy/deploy.sh                      HTTPS on the instance's public IP (no domain needed)
#   bash deploy/deploy.sh notes.example.com    HTTPS on your own domain (point its DNS at the IP first)
#   bash deploy/deploy.sh --build              ignore dist/ and build the app image on the instance
#
# If dist/vanish-images.tar.gz exists (made by scripts/build-for-ec2.sh), the ready-built images are
# loaded from it and nothing is built or downloaded. Otherwise the image is built here.
# Safe to run again any time: after code changes, or after a stop/start gives the instance a new IP.
set -euo pipefail
cd "$(dirname "$0")/.."

IMAGES="dist/vanish-images.tar.gz"
APP_IMAGE="vanish-app:1.0"
SITE=""
BUILD_HERE=0
for arg in "$@"; do
  case "$arg" in
    --build) BUILD_HERE=1 ;;
    *) SITE="$arg" ;;
  esac
done

# Right after the first boot the "ubuntu" user may not be in the docker group yet; fall back to sudo.
DOCKER="docker"
docker info >/dev/null 2>&1 || DOCKER="sudo docker"
$DOCKER info >/dev/null 2>&1 || {
  echo "Docker isn't ready. Wait until 'cat ~/READY.txt' prints 'Docker is ready', then try again." >&2
  exit 1
}

if [ -z "$SITE" ]; then
  # Ask the EC2 instance metadata service (IMDSv2) for this instance's public IPv4 address.
  TOKEN=$(curl -fsS -X PUT "http://169.254.169.254/latest/api/token" \
    -H "X-aws-ec2-metadata-token-ttl-seconds: 60")
  SITE=$(curl -fsS -H "X-aws-ec2-metadata-token: $TOKEN" \
    "http://169.254.169.254/latest/meta-data/public-ipv4") || {
    echo "No public IPv4 address found. Launch the instance with 'Auto-assign public IP' enabled." >&2
    exit 1
  }
fi
echo "SITE_ADDRESS=$SITE" > .env

if [ "$BUILD_HERE" -eq 0 ] && [ -f "$IMAGES" ]; then
  echo "Loading ready-built images from $IMAGES ..."
  $DOCKER load -i "$IMAGES"

  # The images must match this instance's processor (t3.micro = amd64).
  case "$(uname -m)" in
    x86_64) WANT="amd64" ;;
    aarch64) WANT="arm64" ;;
    *) WANT="$(uname -m)" ;;
  esac
  HAVE=$($DOCKER image inspect --format '{{.Architecture}}' "$APP_IMAGE")
  if [ "$HAVE" != "$WANT" ]; then
    echo "The image is built for $HAVE but this instance is $WANT." >&2
    echo "Rebuild on the Mac with scripts/build-for-ec2.sh, or run: bash deploy/deploy.sh --build" >&2
    exit 1
  fi

  echo "Starting Vanish at https://$SITE ..."
  $DOCKER compose up -d --no-build
else
  echo "Building the app image on this instance (2-4 minutes) ..."
  $DOCKER compose up -d --build
fi

$DOCKER compose ps

cat <<EOF

Vanish is starting at:  https://$SITE

The first HTTPS certificate takes up to a minute. Watch it with:
  $DOCKER compose logs -f caddy      (look for "certificate obtained successfully", Ctrl+C to stop)
EOF
