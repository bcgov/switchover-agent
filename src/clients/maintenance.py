import requests
import logging

logger = logging.getLogger(__name__)

def set_maintenance(maintenance_url: str, state: bool):
    state_str = 'false'
    if state:
      state_str = 'true'
    r = requests.put("%s/maintenance/%s" % (maintenance_url, state_str), timeout=2)
    if r.status_code != 200:
        raise Exception('Failed communication with Portal Maintenance %d' % r.status_code)
    logger.debug("set_maintenance (%s) result %s" % (state, r.json()))
    return dict(
        maintenance=state
    )
