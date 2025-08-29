import logging
import os

# initialize the app logger
log = logging.getLogger("privateindexer")
LOG_LEVEL = os.getenv("LOG_LEVEL")
log.setLevel(logging.getLevelName(LOG_LEVEL))
