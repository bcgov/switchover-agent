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
from config import config as switchover_config

logger = logging.getLogger(__name__)

# Reference: https://github.com/kubernetes-client/python/blob/master/kubernetes/docs/AppsV1Api.md


def kube_watch(namespace: str, kind: str, label_selector: str, py_env: str, logic_q):
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
            if kind == 'configmap':
                list = v1.list_namespaced_config_map
            if kind == 'statefulset':
                list = v1.list_namespaced_pod
            for event in w.stream(list, namespace=namespace, label_selector=label_selector, watch=True, timeout_seconds=600):
                logger.debug("Event: %s %s %s" % (
                    event['type'], event['object'].kind, event['object'].metadata.name))
                if kind == 'configmap':
                    config_as_str = json.dumps(event['object'].data, indent=4)
                    logger.info(config_as_str)
                    if last_result != config_as_str and event['type'] != 'DELETED':
                        logic_q.put(
                            {"event": "switchover_state", "data": event['object'].data})
                        last_result = config_as_str

        except ReadTimeoutError:
            logger.warning("There was a timeout error accessing the Kube API. "
                           "Retrying request.", exc_info=True)
            time.sleep(1)
        except Exception:
            logger.error('Unknown error in KubernetesJobWatcher. Failing')
            traceback.print_exc(file=sys.stdout)
            time.sleep(5)
        else:
            logger.debug('Watch died gracefully, starting back up')


def get_configmap(namespace: str, label_selector: str, py_env: str):
    v1 = init_client(py_env)
    logger.debug("[get_configmap] %s %s", namespace, label_selector)
    configmaps = v1.list_namespaced_config_map(
        namespace=namespace, label_selector=label_selector)
    if len(configmaps.items) == 0:
      raise Exception("Configmap not found")
      
    return configmaps.items[0]


def update_configmap(namespace: str, name: str, py_env: str, configmap_spec):

    v1 = init_client(py_env)

    logger.debug("Updating configmap %s to %s", name, configmap_spec)

    try:
        api_response = v1.patch_namespaced_config_map(
            name=name,
            namespace=namespace,
            body=configmap_spec)
    except ApiException as e:
        logger.error(
            "Exception when calling AppsV1Api->patch_namespaced_config_map: %s\n" % e)
        raise


def get_service(namespace: str, label_selector: str, py_env: str):
    v1 = init_client(py_env)

    services = v1.list_namespaced_service(
        namespace=namespace, label_selector=label_selector)
    return services.items[0]


def update_service(namespace: str, name: str, py_env: str, service_spec):

    v1 = init_client(py_env)

    logger.debug("Updating service %s" % name)

    try:
        v1.patch_namespaced_service(
            name=name,
            namespace=namespace,
            body=service_spec)
    except ApiException as e:
        logger.error(
            "Exception when calling AppsV1Api->patch_namespaced_service: %s\n" % e)
        raise


def scale_and_wait(namespace: str, kind: str, name: str, label_selector: str, replicas: int, py_env: str):
    scale(namespace, kind, name, replicas, py_env)
    wait_for_scale(namespace, kind, label_selector, replicas, py_env)


def scale(namespace: str, kind: str, name: str, replicas: int, py_env: str):
    v1 = init_apps_client(py_env)

    body = {"spec": {"replicas": replicas}}

    try:
        if kind == 'deployment':
            v1.patch_namespaced_deployment_scale(
                name, namespace, body)
        else:
            v1.patch_namespaced_stateful_set_scale(
                name, namespace, body)

        if name == switchover_config.get('deployment_health_api'):
            pdb_name = f"{name}-pdb"
            min_available = 1 if replicas > 0 else 0
            update_pdb(namespace, pdb_name, min_available, py_env)

        logger.debug("Scaled %s %s : %s" % (kind, name, body))
    except ApiException as e:
        logger.error(
            "Exception when calling AppsV1Api->patch_namespaced__status: %s\n" % e)
        raise


def wait_for_scale(namespace: str, kind: str, label_selector: str, replicas: int, py_env: str):
    v1 = init_apps_client(py_env)

    w = watch.Watch()

    if kind == 'deployment':
        api_call = v1.list_namespaced_deployment
    else:
        api_call = v1.list_namespaced_stateful_set
    logger.debug("wait_for_scale %s : %s" % (kind, label_selector))

    for event in w.stream(api_call, namespace=namespace, label_selector=label_selector, watch=True, timeout_seconds=120):
        logger.debug("Event: %s %s %s %s" % (
            event['type'], event['object'].kind, event['object'].metadata.name, event['object'].status.ready_replicas))
        if event['object'].status.replicas == 0 and replicas == 0:
            w.stop()
            logger.debug("wait done - %s scaled to %d" %
                         (label_selector, replicas))
            return True
        if event['object'].status.ready_replicas is None and replicas == 0:
            w.stop()
            logger.debug("wait done - %s scaled to NONE" % (label_selector))
            return True

        if event['object'].status.ready_replicas == replicas:
            w.stop()
            logger.debug("wait done - %s scaled to %d" %
                         (label_selector, replicas))
            return True

    raise Exception(
        "Giving up waiting for stateful set scale to %d" % replicas)


def restart_deployment(namespace: str, deployment: str, py_env: str):
    v1 = init_apps_client(py_env)

    now = datetime.datetime.utcnow()
    now = str(now.isoformat("T") + "Z")
    body = {
        'spec': {
            'template': {
                'metadata': {
                    'annotations': {
                        'kubectl.kubernetes.io/restartedAt': now
                    }
                }
            }
        }
    }
    try:
        v1.patch_namespaced_deployment(
            deployment, namespace, body, pretty='true')
        logger.debug("rollout restart for deployment %s" % deployment)
    except ApiException as e:
        logger.error(
            "Exception when calling AppsV1Api->patch_namespaced_deployment")
        logger.error(e)


def delete_pvc(namespace: str, pvcname: str, py_env: str):
    v1 = init_client(py_env)
    try:
        v1.delete_namespaced_persistent_volume_claim(pvcname, namespace)
        logger.debug("Deleted PVC %s" % pvcname)
    except ApiException as e:

        if e.status != 404:
            logger.error(
                "Exception when calling AppsV1Api->patch_namespaced_pvc")
            logger.error(e)
            raise
        else:
            logger.warn("PVC already deleted %s" % pvcname)


def delete_configmap(namespace: str, configmapname: str, py_env: str):
    v1 = init_client(py_env)
    try:
        v1.delete_namespaced_config_map(configmapname, namespace)
        logger.debug("Deleted Configmap %s" % configmapname)
    except ApiException as e:

        if e.status != 404:
            logger.error(
                "Exception when calling AppsV1Api->patch_namespaced_configmap %s" % e)
            raise
        else:
            logger.warn("Configmap already deleted %s" % configmapname)


def patch_secret(namespace: str, name: str, py_env: str, secret_data):

    v1 = init_client(py_env)

    logger.debug("Updating secret %s" % name)

    try:
        v1.patch_namespaced_secret(
            name=name,
            namespace=namespace,
            body={"stringData": secret_data})
        logger.debug("Secret patched %s : %s" % (name, secret_data))

    except ApiException as e:
        logger.error(
            "Exception when calling AppsV1Api->patch_namespaced_secret: %s\n" % e)
        raise


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


def update_pdb(namespace, name, min_available, py_env):
    if py_env == 'local':
        print(f"[LOCAL] Updating PDB {name} in namespace {namespace} with minAvailable: {min_available}")
        return

    try:
        api_instance = client.PolicyV1Api()
        body = {
            "spec": {
                "minAvailable": min_available
            }
        }
        api_instance.patch_namespaced_pod_disruption_budget(name, namespace, body)
        print(f"PDB {name} updated successfully")
    except ApiException as e:
        print(f"Exception when updating PDB: {e}")
