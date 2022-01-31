from fastapi import FastAPI, Response
import os
import sys
import traceback
import json
import time
import asyncio
import logging
import pprint
import ssl
import pathlib
import uvicorn

from multiprocessing import Process, Queue

import random

from config import config
from is_enabled import is_enabled
from logic import Logic
from clients.dns import dns_watch
from clients.kube import patch_secret
from clients.kube_stream import kube_stream_watch
from clients.kube import kube_watch, restart_deployment
from clients.kube import scale, scale_and_wait, delete_pvc, delete_configmap
from clients.keycloak import keycloak_service_block, keycloak_service_flow
from clients.prom import prom_server
from clients.maintenance import set_maintenance
from clients.patroni import patroni_worker, set_readonly_cluster, set_primary_cluster, set_standby_cluster
from transitions.initiate_down import rollback_active_down
from peers.server import peer_server
from peers.client import peer_client
from peers.client_fwd import peer_client_fwd

from dotenv import dotenv_values

from prometheus_client import CollectorRegistry, generate_latest, multiprocess

registry = CollectorRegistry()
multiprocess.MultiProcessCollector(registry)


app = FastAPI()


@app.get("/metrics")
def prom_metrics():
    return Response(content=generate_latest(registry), media_type="text/plain")


@app.get("/health")
def check_health():
    return {"status": "up"}


@app.put("/rollback-active-down")
def rollback_active_down_api():
    rollback_active_down(os.environ.get("PY_ENV"))
    return {"done": "true"}


@app.get("/set-primary")
def check_health():
    set_primary_cluster(os.environ.get("PATRONI_LOCAL_API"))
    return {"done": "true"}


@app.get("/set-readonly")
def check_health():
    set_readonly_cluster(os.environ.get("PATRONI_LOCAL_API"))
    return {"done": "true"}


@app.get("/restart-kong")
def restart_konghc():
    restart_deployment(
        os.environ.get("KUBE_NAMESPACE"),
        config.get('deployment_kong_control_plane'),
        os.environ.get("PY_ENV"),
    )
    return {"done": "true"}


@app.get("/initiate_primary")
def initiate_primary():
    logic = Logic()
    logic.initiate_primary(
        os.environ.get("KUBE_NAMESPACE"),
        os.environ.get("PATRONI_LOCAL_API"),
        os.environ.get("PY_ENV"),
    )
    return {"done": "true"}


@app.get("/initiate_standby")
def initiate_standby():
    logic = Logic()
    logic.initiate_standby(
        os.environ.get("KUBE_NAMESPACE"),
        os.environ.get("PATRONI_LOCAL_API"),
        os.environ.get("PY_ENV"),
    )
    return {"done": "true"}


@app.put("/maintenance/on")
def maintenance_on():
    set_maintenance(os.environ.get("MAINTENANCE_URL"), True)
    return {"maintenance": True}


@app.put("/maintenance/off")
def maintenance_off():
    set_maintenance(os.environ.get("MAINTENANCE_URL"), False)
    return {"maintenance": False}


@app.get("/keycloak_start")
def keycloak_start():
    namespace = os.environ.get("KUBE_NAMESPACE")
    py_env = os.environ.get("PY_ENV")
    scale(namespace, 'statefulset',
          config.get('statefulset_keycloak'),
          1, py_env)

    return {"done": "true"}


@app.get("/keycloak_service_flow")
def keycloak_service_flow_get():
    keycloak_service_flow()
    return {"done": "true"}


@app.get("/keycloak_service_block")
def keycloak_service_block_get():
    keycloak_service_block()
    return {"done": "true"}


def fastapi():
    uvicorn.run(app, host="0.0.0.0", port=8000, log_level='warning')


logging.basicConfig(
    stream=sys.stdout,
    level=logging.DEBUG,
    format='%(asctime)s [%(levelname)-5s] %(name)-20s %(message)s')
logging.getLogger('websockets').setLevel(logging.INFO)
logging.getLogger('asyncio').setLevel(logging.INFO)
logging.getLogger('urllib3').setLevel(logging.INFO)
logging.getLogger('fastapi').setLevel(logging.INFO)

log_level = os.getenv('LOG_LEVEL', '')
for level in log_level.split(','):
    if level != '':
        logger_name = level.split('=')[0]
        logger_level = level.split('=')[1]
        logging.getLogger(logger_name).setLevel(
            logging.getLevelName(logger_level))

logger = logging.getLogger(__name__)

if __name__ == '__main__':

    fastapi_proc = Process(target=fastapi)
    fastapi_proc.start()

if __name__ == '__main__':

    processes = []
    logic_q = Queue()
    fwd_to_peer_q = Queue()

    if is_enabled('logic_handler'):
        logic = Logic()
        t = Process(target=logic.handler, args=(
            os.environ.get("KUBE_CLUSTER"),
            os.environ.get("KUBE_NAMESPACE"),
            config.get('switchover_state_label_selector'),
            os.environ.get("PATRONI_LOCAL_API"),
            os.environ.get("PY_ENV"),
            logic_q,
            fwd_to_peer_q
        ))
        processes.append(t)

    #t = Process(target=prom_server)
    # processes.append(t)

    if is_enabled('dns_watch'):
        t = Process(target=dns_watch, args=(
            os.environ.get("GSLB_DOMAIN"),
            logic_q
        ))
        processes.append(t)

    if is_enabled('kube_watch'):
        t = Process(target=kube_watch, args=(
            os.environ.get("KUBE_HEALTH_NAMESPACE"),
            'configmap',
            config.get("switchover_state_label_selector"),
            os.environ.get("PY_ENV"),
            logic_q
        ))
        processes.append(t)

    if is_enabled('tekton_watch'):
        t = Process(target=kube_stream_watch, args=(
            os.environ.get("KUBE_TEKTON_NAMESPACE"),
            'tekton',
            config.get('tekton_label_selector'),
            os.environ.get("PY_ENV"),
            logic_q
        ))
        processes.append(t)

    if is_enabled('peer_server'):
        t = Process(target=peer_server, args=(
            os.environ.get("TLS_CA"),
            os.environ.get("TLS_LOCAL_CRT"),
            os.environ.get("TLS_LOCAL_KEY"),
            config.get('wss_server_host'),
            config.get('wss_server_port'),
            logic_q))
        processes.append(t)

    if is_enabled('peer_client'):
        t = Process(target=peer_client, args=(
            os.environ.get("TLS_CA"),
            os.environ.get("TLS_LOCAL_CRT"),
            os.environ.get("TLS_LOCAL_KEY"),
            os.environ.get("PEER_HOST"),
            os.environ.get("PEER_PORT"),
            logic_q
        ))
        processes.append(t)

    if is_enabled('peer_client_fwd'):
        t = Process(target=peer_client_fwd, args=(
            os.environ.get("TLS_CA"),
            os.environ.get("TLS_LOCAL_CRT"),
            os.environ.get("TLS_LOCAL_KEY"),
            os.environ.get("PEER_HOST"),
            os.environ.get("PEER_PORT"),
            fwd_to_peer_q
        ))
        processes.append(t)

    if is_enabled('patroni_worker'):
        t = Process(target=patroni_worker, args=(
            os.environ.get("PATRONI_LOCAL_API"),
            logic_q
        ))
        processes.append(t)

    try:
        for process in processes:
            process.start()
        for process in processes:
            process.join()
    except KeyboardInterrupt:
        logger.error("Keyboard Exit")
    except:
        logger.error("Unknown error.  Exiting")

    for process in processes:
        process.terminate()
    fastapi_proc.terminate()

    logger.error("All terminated.")
