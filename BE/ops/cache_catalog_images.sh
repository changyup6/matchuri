#!/usr/bin/env bash
# Incremental prefetch, separate from API startup and request handling.
# Use the currently running application's image so encoder and URL version match.
set -euo pipefail
umask 077
CACHE_ROOT="${MATCHURI_CATALOG_REPORT_DIR:-/home/ubuntu/matchuri-image-cache}"
DATA_DIR="${MATCHURI_DATA:-/home/ubuntu/matchuri-data/kakao}"
APP_CONTAINER="${MATCHURI_APP_CONTAINER:-backmock-app-1}"
MEDIA_VOLUME="${MATCHURI_MEDIA_VOLUME:-backmock_media}"
mkdir -p "$CACHE_ROOT"
exec 9>"$CACHE_ROOT/.lock"
flock -n 9 || exit 0
exec >"$CACHE_ROOT/latest.log" 2>&1
IMAGE_ID=$(docker inspect --format '{{.Image}}' "$APP_CONTAINER")
# Keep reports outside public /media. The last report is replaced each run so
# successful cached rows do not grow an unbounded hourly log.
if test -f "$CACHE_ROOT/latest.jsonl"; then
    mv -f "$CACHE_ROOT/latest.jsonl" "$CACHE_ROOT/previous.jsonl"
fi
docker run --rm --cpus=2 --memory=2g \
    -v "$DATA_DIR:/app/data/kakao:ro" \
    -v "$MEDIA_VOLUME:/app/media" \
    -v "$CACHE_ROOT:/reports" \
    "$IMAGE_ID" python cache_catalog_images.py \
    --data-dir /app/data/kakao --media-dir /app/media \
    --workers 8 --report /reports/latest.jsonl
