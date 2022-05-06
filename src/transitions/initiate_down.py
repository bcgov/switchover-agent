import logging
from clients.tekton import trigger_tekton_build
from clients.kube import scale, restart_deployment
from transitions.shared import maintenance_on, maintenance_off, set_in_recovery
from config import config

logger = logging.getLogger(__name__)


# Only applicable to the Active site, and puts Active in maintenance and stops accepting traffic
def initiate_active_down(py_env: str):

    logger.info("initiate_active_down - health down and in maintenance")

    set_in_recovery(True, py_env)

    scale(config.get('kube_health_namespace'), 'deployment',
          config.get('deployment_health_api'), 0, py_env)

    maintenance_on()

    # cycle the kong control plane to force data planes to re-establish connections
    restart_deployment(config.get('solution_namespace'),
                       config.get('deployment_kong_control_plane'),
                       config.get('py_env'))

# Be really careful with this one; make sure Passive site is not live!
def rollback_active_down(py_env: str):

    logger.info("rollback_active_down")

    maintenance_off(py_env)

    scale(config.get('kube_health_namespace'), 'deployment',
          config.get('deployment_health_api'), 2, py_env)

    set_in_recovery(False, py_env)
