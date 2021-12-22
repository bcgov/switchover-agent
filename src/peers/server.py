import sys
import traceback
import json
import time
import asyncio
import logging
import pprint
import ssl
import pathlib
import functools

import websockets

import threading

import random

logger = logging.getLogger(__name__)


async def hello(websocket, path, q):
    while True:
        try:
            message_str = await websocket.recv()
        except websockets.ConnectionClosed:
            logger.warn(f"Terminated")
            break
        logger.debug("(SERVER) < {}".format(message_str))

        message = json.loads(message_str)
        if message['event'] == 'from_peer':
            q.put(message)
        greeting = {"event": "pong"}
        await websocket.send(json.dumps(greeting))
        logger.debug("(SERVER) > {}".format(greeting))


def peer_server(bundle_pem, self_crt, self_key, listen_host, listen_port, q):
    logger.info("Starting WS Server on %s:%s" % (listen_host, listen_port))
    ssl_context = ssl.SSLContext(
        ssl.PROTOCOL_TLS_SERVER, verify_mode=ssl.CERT_REQUIRED)
    ssl_context.load_cert_chain(self_crt, self_key)
    ssl_context.load_verify_locations(bundle_pem)

    new_loop = asyncio.new_event_loop()
    asyncio.set_event_loop(new_loop)
    start_server = websockets.serve(
        functools.partial(hello, q=q), listen_host, listen_port, ssl=ssl_context)

    new_loop.run_until_complete(start_server)
    new_loop.run_forever()
