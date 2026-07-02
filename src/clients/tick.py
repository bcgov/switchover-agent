import time

TICK_INTERVAL_SECONDS = 30


def tick_producer(logic_q):
    """Enqueues a tick event every 30 seconds so Logic.handler can evaluate
    scheduled retries and the total-duration cap without relying on incidental
    events from other watchers."""
    while True:
        time.sleep(TICK_INTERVAL_SECONDS)
        logic_q.put({"event": "tick"})
