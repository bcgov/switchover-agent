from kubernetes.client.rest import ApiException
from kubernetes import client, config, watch
from urllib3.exceptions import ReadTimeoutError
import asyncio
import logging
import json
import traceback
import sys
import time
import datetime

logger = logging.getLogger(__name__)

# Reference: https://github.com/kubernetes-client/python/blob/master/kubernetes/docs/AppsV1Api.md


def kube_stream_watch(namespace: str, kind: str, label_selector: str, py_env: str, logic_q):
    new_loop = asyncio.new_event_loop()
    asyncio.set_event_loop(new_loop)
    new_loop.create_task(watch_stream(
        namespace, kind, label_selector, py_env, logic_q))
    new_loop.run_forever()


async def watch_stream(namespace: str, kind: str, label_selector: str, py_env: str, logic_q):

    # Configs can be set in Configuration class directly or using helper utility
    v1 = init_client(py_env)
    v1_app = init_apps_client(py_env)

    w = watch.Watch()

    last_result = "unknown"

    while True:
        logger.info("Watching for %s matching %s..." % (kind, label_selector))
        try:
            if kind == 'tekton':
              crds = client.CustomObjectsApi()
              list = crds.list_namespaced_custom_object
              stream = w.stream(list, 'tekton.dev', 'v1beta1', namespace, 'pipelineruns', label_selector=label_selector, watch=True, timeout_seconds=600)
            if kind == 'configmap':
              list = v1.list_namespaced_config_map
              stream = w.stream(list, namespace=namespace, label_selector=label_selector, watch=True, timeout_seconds=600)
            if kind == 'statefulset':
              list = v1.list_namespaced_pod
              stream = w.stream(list, namespace=namespace, label_selector=label_selector, watch=True, timeout_seconds=600)
            for event in stream:
                logger.debug("Event: %s %s %s" % (
                    event['type'], event['object']['kind'], event['object']['metadata']['name']))
                logic_q.put(
                    {"event": "kube_stream", "kind": kind, "data": event})

        except ReadTimeoutError:
            logger.warning("There was a timeout error accessing the Kube API. "
                           "Retrying request.", exc_info=True)
            time.sleep(1)
        except Exception:
            logger.error('Unknown error in KubernetesJobWatcher. Failing')
            traceback.print_exc(file=sys.stdout)
            time.sleep(5)
        else:
            logger.warning('Watch died gracefully, starting back up')

def init_client(py_env: str):
    if py_env == 'production':
        config.load_incluster_config()
    else:
        config.load_kube_config()
    return client.CoreV1Api()

def init_apps_client(py_env: str):
    if py_env == 'production':
        config.load_incluster_config()
    else:
        config.load_kube_config()
    return client.AppsV1Api()