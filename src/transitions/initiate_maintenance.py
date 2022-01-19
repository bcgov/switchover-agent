import logging
import logging
import datetime
from clients.kube import scale
from clients.patroni import set_readonly_cluster, set_primary_cluster
from clients.kube import scale, scale_and_wait, delete_pvc, delete_configmap
from clients.tekton import trigger_tekton_build
from transitions.wait_for import WaitFor
from transitions.shared import maintenance_on, set_in_recovery, set_in_maintenance, update_patroni_spilo_env_vars
from transitions.initiate_primary import initiate_primary
from config import config

logger = logging.getLogger(__name__)

# Maintenance on Passive Site
# - in_recovery must be False
# - in_maintenance set to True
# - scale the health api down to 0 so no traffic is sent to the Passive site
# - intiate work to make passive site detached and "live"


def initiate_passive_maintenance(logic_context, namespace: str, patroni_local_url: str, py_env: str):
    set_in_maintenance(True, py_env)
    scale(config.get('kube_health_namespace'), 'deployment',
          config.get('deployment_health_api'), 0, py_env)
    return initiate_primary(logic_context, namespace, patroni_local_url, py_env)
