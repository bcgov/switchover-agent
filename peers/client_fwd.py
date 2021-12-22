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


async def client_fwd_messages(ssl_context, peer_host, peer_port, fwd_to_peer_q):
    while True:
        msg = fwd_to_peer_q.get()
        tries = 0
        while tries < 5 and msg is not None:
            tries = tries + 1
            try:
                async with websockets.connect('wss://%s:%s' % (peer_host, peer_port), ssl=ssl_context) as websocket:
                    logger.info("Connected!")
                    await websocket.send(json.dumps(msg))
                    logger.debug("(CLIENT) > {}".format(msg))

                    rsp = await websocket.recv()
                    logger.debug("(CLIENT) < {}".format(rsp))

                    await websocket.close()
                    msg = None

            except websockets.exceptions.ConnectionClosedOK:
                logger.info("WS Closed")
            except ConnectionRefusedError:
                logger.error("Connection refused")
                time.sleep(2)
            except:
                traceback.print_exc(file=sys.stdout)
                logger.error("Unknown failure")
                time.sleep(2)


def peer_client_fwd(bundle_pem, self_crt, self_key, peer_host, peer_port, fwd_to_peer_q):

    ssl_context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    ssl_context.check_hostname = True
    ssl_context.verify_mode = ssl.CERT_REQUIRED

    ssl_context.load_cert_chain(self_crt, self_key)
    ssl_context.load_verify_locations(bundle_pem)

    logger.info("Starting WS Client to %s:%s" % (peer_host, peer_port))
    new_loop = asyncio.new_event_loop()
    asyncio.set_event_loop(new_loop)
    new_loop.run_until_complete(client_fwd_messages(
        ssl_context, peer_host, peer_port, fwd_to_peer_q))
    new_loop.run_forever()
