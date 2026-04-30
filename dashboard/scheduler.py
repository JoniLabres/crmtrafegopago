import sys
import os
import logging
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "data_pipeline"))

import schedule
import time
from dotenv import load_dotenv
from alerts import AlertSystem

load_dotenv()

LOG_DIR = Path(__file__).parent.parent / "logs"
LOG_DIR.mkdir(exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    handlers=[
        logging.FileHandler(LOG_DIR / "scheduler.log", encoding="utf-8"),
        logging.StreamHandler(),
    ],
)
logger = logging.getLogger(__name__)


def run_alerts():
    logger.info("Iniciando verificação de alertas")
    try:
        system = AlertSystem()
        alerts = system.check_all()
        logger.info("Verificação concluída: %d alertas", len(alerts))
    except Exception as e:
        logger.error("Erro na verificação de alertas: %s", e)


def run_pipeline():
    logger.info("Iniciando pipeline diário de dados")
    try:
        from load_database import run_pipeline as _run
        total = _run()
        logger.info("Pipeline concluído: %d linhas carregadas", total)
    except Exception as e:
        logger.error("Erro no pipeline: %s", e)


def main():
    logger.info("Scheduler iniciado")
    schedule.every().hour.do(run_alerts)
    schedule.every().day.at("06:00").do(run_pipeline)

    logger.info("Próxima execução de alertas: %s", schedule.next_run())
    while True:
        schedule.run_pending()
        time.sleep(60)


if __name__ == "__main__":
    main()
