import sys
import traceback
import json
import time
import asyncio
import logging
import pprint
import ssl
import pathlib
from prometheus_client import Gauge

import websockets

import threading

import random

logger = logging.getLogger(__name__)

count = {"n": 1}


async def hello_client(ssl_context, peer_host, peer_port, logic_q, GAUGE):
    last_state = "unknown"
    while True:
        try:
            async with websockets.connect('wss://%s:%s' % (peer_host, peer_port), ssl=ssl_context) as websocket:
                logger.info("Connected!")
                GAUGE.set(1)
                while True:
                    count["n"] = count["n"] + 1
                    name = "Sequence %d" % count["n"]
                    message = {"event": "ping", "name": name}
                    await websocket.send(json.dumps(message))
                    logger.debug("(CLIENT) > {}".format(message))

                    greeting = await websocket.recv()
                    logger.debug("(CLIENT) < {}".format(greeting))

                    if last_state != 'connected':
                        logic_q.put({"event": "peer", "state": "ok"})
                        last_state = 'connected'

                    time.sleep(5)

        except websockets.exceptions.ConnectionClosedOK:
            logger.info("WS Closed")
        except websockets.exceptions.ConnectionClosedError:
            logger.error("Connection closing error")
            GAUGE.set(0)
            if last_state != 'error':
                logic_q.put({"event": "peer", "state": "error"})
                last_state = 'error'
            time.sleep(2)
        except ConnectionRefusedError:
            logger.error("Connection refused")
            GAUGE.set(0)
            if last_state != 'error':
                logic_q.put({"event": "peer", "state": "error"})
                last_state = 'error'
            time.sleep(2)
        except:
            traceback.print_exc(file=sys.stdout)
            logger.error("Unknown failure")
            GAUGE.set(0)
            if last_state != 'error':
                logic_q.put({"event": "peer", "state": "error"})
                last_state = 'error'
            time.sleep(2)


def peer_client(bundle_pem, self_crt, self_key, peer_host, peer_port, logic_q):

    GAUGE = Gauge('switchover_peer', 'Switchover Peer Status',
                  ['state']).labels(state="active")

    ssl_context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    ssl_context.check_hostname = True
    ssl_context.verify_mode = ssl.CERT_REQUIRED

    ssl_context.load_cert_chain(self_crt, self_key)
    ssl_context.load_verify_locations(bundle_pem)

    logger.info("Starting WS Client to %s:%s" % (peer_host, peer_port))
    new_loop = asyncio.new_event_loop()
    asyncio.set_event_loop(new_loop)
    new_loop.run_until_complete(hello_client(
        ssl_context, peer_host, peer_port, logic_q, GAUGE))
    new_loop.run_forever()
