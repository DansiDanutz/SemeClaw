#!/bin/sh
mkdir -p /app/data/audio/meetings/saved \
         /app/data/audio/scripts \
         /app/data/research/saved \
         /app/data/logs \
         /app/data/memory \
         /app/data/builds

ln -sf /app/data/audio /app/war_room/audio 2>/dev/null || true
ln -sf /app/data/research /app/war_room/research 2>/dev/null || true
ln -sf /app/data/logs /app/war_room/logs 2>/dev/null || true
ln -sf /app/data/memory /app/war_room/memory 2>/dev/null || true
ln -sf /app/data/builds /app/war_room/builds 2>/dev/null || true

exec "$@"
