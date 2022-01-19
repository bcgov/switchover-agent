import logging
from clients.tekton import trigger_tekton_build
from clients.kube import scale
from transitions.shared import maintenance_on, set_in_recovery
from config import config

logger = logging.getLogger(__name__)


# Only applicable to the Active site, and puts Active in maintenance and stops accepting traffic
def initiate_active_down(namespace: str, py_env: str):

    logger.info("initiate_active_down - health down and in maintenance")

    set_in_recovery(True, py_env)

    scale(config.get('kube_health_namespace'), 'deployment',
          config.get('deployment_health_api'), 0, py_env)

    maintenance_on(namespace, py_env)
