# -*- coding: utf-8 -*-
import logging
import logging.handlers
from pathlib import Path

_LOG_DIR = Path.home() / '.memsi' / 'logs'
_LOG_DIR.mkdir(parents=True, exist_ok=True)

_handler = logging.handlers.TimedRotatingFileHandler(
    _LOG_DIR / 'memsi.log',
    when='midnight',
    backupCount=30,
    encoding='utf-8',
)
_handler.setFormatter(logging.Formatter(
    '%(asctime)s %(levelname)-8s %(name)s — %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S',
))

logger = logging.getLogger('memsi')
logger.setLevel(logging.DEBUG)
if not logger.handlers:
    logger.addHandler(_handler)
