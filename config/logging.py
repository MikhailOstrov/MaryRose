import logging
import os
from logging.handlers import RotatingFileHandler
import sys

def setup_logging():
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(name)s - %(levelname)s - [%(threadName)s] - %(message)s',
        handlers=[
            logging.StreamHandler(sys.stdout),
        ]
    )
