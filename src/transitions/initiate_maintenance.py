import logging
import logging
from transitions.shared import scale_health_api, set_in_maintenance
from transitions.initiate_primary import deploy_primary
from config import config

logger = logging.getLogger(__name__)

# Maintenance on Passive Site
# - in_recovery must be False
# - in_maintenance set to True
# - scale the health api down to 0 so no traffic is sent to the Passive site
# - intiate work to make passive site detached and "live"


def initiate_passive_maintenance(logic_context, patroni_local_url: str, py_env: str):
    set_in_maintenance(True, py_env)
    scale_health_api(config.get('kube_health_namespace'), 
                     config.get('deployment_health_api'), 
                     0, py_env)
    return deploy_primary(logic_context, patroni_local_url, py_env)
