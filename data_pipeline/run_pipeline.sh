#!/bin/bash
# Executa o pipeline completo de coleta e consolidação de dados.
# Agendar via cron: 0 6 * * * /caminho/para/run_pipeline.sh

set -e

PROJECT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
VENV="$PROJECT_DIR/.venv/bin/python"
LOG_FILE="$PROJECT_DIR/logs/pipeline_$(date +%Y%m%d).log"

mkdir -p "$PROJECT_DIR/logs"

echo "[$(date '+%Y-%m-%d %H:%M:%S')] Iniciando pipeline" | tee -a "$LOG_FILE"

cd "$PROJECT_DIR/data_pipeline"
"$VENV" load_database.py 2>&1 | tee -a "$LOG_FILE"

echo "[$(date '+%Y-%m-%d %H:%M:%S')] Pipeline concluído" | tee -a "$LOG_FILE"
