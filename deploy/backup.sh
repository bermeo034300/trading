#!/usr/bin/env bash
# Copia de seguridad diaria de la base SQLite de QuantFlow.
set -euo pipefail

DB="${QUANTFLOW_DB:-/opt/quantflow/data/quantflow.db}"
DEST="${QUANTFLOW_BACKUPS:-/opt/quantflow/backups}"
KEEP_DAYS="${KEEP_DAYS:-14}"

mkdir -p "$DEST"
stamp="$(date -u +%Y%m%dT%H%M%SZ)"
sqlite3 "$DB" ".backup '$DEST/quantflow-$stamp.db'"
find "$DEST" -name 'quantflow-*.db' -type f -mtime +"$KEEP_DAYS" -delete
