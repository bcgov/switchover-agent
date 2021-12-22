import os
import logging
from clients.kube import get_service, update_service

logger = logging.getLogger(__name__)

def keycloak_service_flow():
    namespace = os.environ.get("KUBE_NAMESPACE")
    py_env = os.environ.get("PY_ENV")
    svc = get_service(namespace, 'app.kubernetes.io/component=http,app.kubernetes.io/instance=keycloak', py_env)
    patch = { 'spec': {'selector': { 'app': None, 'app.kubernetes.io/instance': 'keycloak',
                       'app.kubernetes.io/name': 'keycloak' }}}

    update_service(namespace, svc.metadata.name, py_env, patch)
    return {"done": "true"}

def keycloak_service_block():
    namespace = os.environ.get("KUBE_NAMESPACE")
    py_env = os.environ.get("PY_ENV")
    svc = get_service(namespace, 'app.kubernetes.io/component=http,app.kubernetes.io/instance=keycloak', py_env)
    patch = { 'spec': {'selector': { 'app':'keycloak-maintenance-page', 'app.kubernetes.io/instance': None,
                       'app.kubernetes.io/name': None }}}

    update_service(namespace, svc.metadata.name, py_env, patch)
    return {"done": "true"}
