#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.." || exit 1

IMAGE_NAME="theos-test"

echo "=== Building Docker image ==="
docker build --target gateway -t "$IMAGE_NAME" .

echo ""
echo "=== Running 'theos --help' ==="
docker run --rm "$IMAGE_NAME" --help

echo ""
echo "=== Running 'theos status' ==="
STATUS_OUTPUT=$(docker run --rm "$IMAGE_NAME" status 2>&1) || true

echo "$STATUS_OUTPUT"

echo ""
echo "=== Validating output ==="
PASS=true

check() {
    if echo "$STATUS_OUTPUT" | grep -q "$1"; then
        echo "  PASS: found '$1'"
    else
        echo "  FAIL: missing '$1'"
        PASS=false
    fi
}

check "theos Status"
check "Config:"
check "Workspace:"
check "Gateway:"

echo ""
if $PASS; then
    echo "=== All checks passed ==="
else
    echo "=== Some checks FAILED ==="
    exit 1
fi

# Cleanup
echo ""
echo "=== Cleanup ==="
docker rmi -f "$IMAGE_NAME" 2>/dev/null || true
echo "Done."
