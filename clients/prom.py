from prometheus_client import start_http_server, Summary, Gauge
import random
import time
import logging

logger = logging.getLogger(__name__)

REQUEST_TIME = Summary('switchover_request_processing_seconds',
                       'Time spent processing request')


@REQUEST_TIME.time()
def process_request(t):
    """A dummy function that takes some time."""
    logger.info("Dummy Call ")
    time.sleep(t)


def prom_server():
    # start_http_server(4000)
    while True:
        process_request(random.random())
