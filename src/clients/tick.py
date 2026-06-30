import time
import logging

logger = logging.getLogger(__name__)

TICK_INTERVAL_SECONDS = 30


def tick_producer(logic_q):
    """Enqueues a tick event every 30 seconds so Logic.handler can evaluate
    scheduled retries and the total-duration cap without relying on incidental
    events from other watchers."""
    while True:
        time.sleep(TICK_INTERVAL_SECONDS)
        logger.debug("tick")
        logic_q.put({"event": "tick"})
