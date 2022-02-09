import logging
import datetime
import traceback
import sys
import time
from clients.tekton import trigger_tekton_build
from clients.kube import scale, scale_and_wait, delete_pvc, delete_configmap
from clients.kube import get_configmap, update_configmap, restart_deployment, patch_secret
from clients.patroni import set_readonly_cluster, set_primary_cluster
from clients.maintenance import set_maintenance
from clients.keycloak import keycloak_service_block, keycloak_service_flow
from transitions.wait_for import WaitFor
from transitions.shared import maintenance_on, maintenance_off
from transitions.initiate_down import initiate_active_down
from transitions.initiate_maintenance import initiate_passive_maintenance
from transitions.initiate_primary import initiate_active_primary, initiate_passive_primary
from transitions.initiate_standby import initiate_active_standby, initiate_passive_standby
from config import config
from prometheus_client import Gauge, Counter, Enum

logger = logging.getLogger(__name__)


class Logic:
    peer = "unknown"
    patroni = dict(control='unknown', concerns=[], leader=dict(role='unknown'))
    pipeline = dict(event_id=None, start_ts=None,
                    maintenance=False)

    triggers = []
    PIPELINE = Counter('switchover_pipeline', 'Switchover Tekton Pipelines',
                       ['release', 'state'])
    METRIC = Counter('switchover_logic', 'Switchover Logic',
                     ['resource', 'state'])
    GAUGE = Gauge('switchover_logic_gauge', 'Switchover Logic Point in Time',
                  ['resource'])

    def handler(self, cluster: str, namespace: str, label_selector: str, patroni_local_url: str, py_env: str, _q, fwd_to_peer_q):
        while True:
            try:
                item = _q.get()
                logger.info(f'logic {item["event"]}')
                self.METRIC.labels(resource="logic", state="info").inc()

                if item['event'] == 'kube_stream':
                    spec = item['data']['object']
                    kind = spec['kind']

                    if kind == 'PipelineRun':

                        mdnm = spec['metadata']['name']
                        event_id = spec['metadata']['labels']['triggers.tekton.dev/triggers-eventid']

                        params = self.pick_params(spec['spec']['params'], [
                            "git-release-branch", "release-namespace"])

                        if item['data']['type'] != "ADDED":
                            logger.debug("   (track %s)" %
                                         self.pipeline['event_id'])
                            logger.debug("   name  = %s" % mdnm)
                            logger.debug(
                                "   event = %s" % event_id)
                            for key in params.keys():
                                logger.debug("   param  %-20s = %s" %
                                             (key, params[key]))

                        status_reason = "Undefined"
                        if 'status' in spec and 'conditions' in spec['status']:
                            status_reason = spec['status']['conditions'][0]['reason']
                            if item['data']['type'] != "ADDED":
                                logger.debug("   reason = %s" %
                                             spec['status']['conditions'][0]['reason'])
                                logger.debug("   status = %s" %
                                             spec['status']['conditions'][0]['status'])
                                logger.debug("   messag = %s" %
                                             spec['status']['conditions'][0]['message'])

                            if self.pipeline['event_id'] == event_id and (status_reason == "Completed" or status_reason == "Succeeded" or status_reason == "Failed" or status_reason == "PipelineRunCancelled"):

                                logger.info("End State for Pipeline!")
                                logger.info("Tekton Start: %s" %
                                            self.pipeline['start_ts'])
                                logger.info("Tekton   End: %s" %
                                            datetime.datetime.now())
                                # set the maintenance mode appropriately
                                if self.pipeline['maintenance']:
                                    maintenance_on()
                                else:
                                    maintenance_off(py_env)
                                self.pipeline = dict(
                                    event_id=None, start_ts=None, maintenance=False)

                        if item['data']['type'] != "ADDED":
                            self.PIPELINE.labels(
                                release=params['release-namespace'], state=status_reason).inc()

                        # Status Reason/Status : Running, Unknown
                        # Status Reason/Status : Succeeded, True
                        # Status Reason/Status : Failed, False

                if item['event'] == 'patroni':
                    self.patroni = item
                    if item['control'] == 'up':
                        self.METRIC.labels(
                            resource="patroni", state=("IsStandby=%s" % item['is_standby_configured'])).inc()
                        self.METRIC.labels(
                            resource="patroni-leader", state="%s-%s" % (item['leader']['member'], item['leader']['role'])).inc()
                        for concern in item['concerns']:
                            self.METRIC.labels(
                                resource="patroni-members", state="%s-%s" % (concern['member'], concern['state'])).inc()

                    if item['control'] == 'up' and item['leader']['role'] == 'leader':
                        self.GAUGE.labels(resource="patroni").set(1)
                    elif item['control'] == 'up' and item['leader']['role'] == 'standby_leader':
                        self.GAUGE.labels(resource="patroni").set(2)
                    else:
                        self.GAUGE.labels(resource="patroni").set(0)

                    for trigger in self.triggers:
                        trigger.eval()

                if item['event'] == 'peer':
                    self.peer = item['state']
                    self.METRIC.labels(resource="peer", state=self.peer).inc()

                    if self.peer == 'ok':
                        self.GAUGE.labels(resource="peer").set(1)
                    else:
                        self.GAUGE.labels(resource="peer").set(0)

                if item['event'] == 'dns':
                    dns = item['result']
                    if dns == config.get('active_ip'):
                        self.METRIC.labels(
                            resource="dns", state="active/%s" % dns).inc()
                        self.GAUGE.labels(resource="dns").set(1)

                    elif dns == config.get('passive_ip'):
                        self.METRIC.labels(
                            resource="dns", state="passive/%s" % dns).inc()
                        self.GAUGE.labels(resource="dns").set(2)
                    else:
                        self.METRIC.labels(
                            resource="dns", state="%s" % dns).inc()
                        self.GAUGE.labels(resource="dns").set(0)

                    # Trigger golddr-primary IF:
                    #   Automation is enabled
                    #   This is the Active site but DNS is not going to the Active site
                    #   This is the Passive site and DNS is going to the Passive site
                    if config.get('automation_enabled'):
                        self.GAUGE.labels(resource="automation").set(1)

                        check_active_site = (cluster == config.get('active_site')
                                             and dns != config.get('active_ip'))

                        check_passive_site = (cluster == config.get(
                            'passive_site') and dns == config.get('passive_ip'))

                        if check_active_site or check_passive_site:
                            logger.warn("Transitioning to golddr-primary")
                            self.update_switchover_state(
                                None, 'golddr-primary', None, py_env)
                    else:
                        self.GAUGE.labels(resource="automation").set(0)

                    fwd_to_peer_q.put({"event": "from_peer", "message": item})

                if item['event'] == 'from_peer':
                    # messages from the peer will be for the following:
                    # - item.message.event == dns
                    # - item.message.event == transition_to (state = active-passive and gold-standby)
                    if item['message']['event'] == 'transition_to':
                        self.update_switchover_state(
                            None, item['message']['state'], None, py_env)

                if item['event'] == 'switchover_state':
                    transition = item['data']['transition']
                    self.METRIC.labels(
                        resource="switchover_state", state=transition).inc()

                    if item['data']['last_stable_state'] == "":
                        self.update_switchover_state(
                            'independent', None, py_env)
                        continue

                    if transition == '':
                        logger.debug(
                            "Configmap update - no transition requested")
                        self.GAUGE.labels(resource="transition").set(0)

                    elif transition != item['data']['last_stable_state']:
                        self.GAUGE.labels(resource="transition").set(1)

                        last_stable_state = item['data']['last_stable_state']
                        # initiate a transition
                        next_state = last_stable_state
                        if transition == 'active-passive':
                            if self.peer == 'error':
                                logger.warn(
                                    "Aborting active-passive transition - peer not reachable")
                                self.METRIC.labels(
                                    resource="logic", state="warning").inc()

                            elif last_stable_state != 'independent' and last_stable_state != 'golddr-maintenance' and last_stable_state != 'gold-standby':
                                logger.warn(
                                    "Aborting active-passive transition - can not transition from %s" % last_stable_state)
                                self.METRIC.labels(
                                    resource="logic", state="warning").inc()

                            elif cluster != config.get('active_site') and cluster != config.get('passive_site'):
                                logger.warn(
                                    "Aborting active-passive transition - invalid cluster config" % cluster)
                                self.METRIC.labels(
                                    resource="logic", state="warning").inc()
                            elif cluster == config.get('active_site'):

                                # TODO: Do a: is_peer_happy_to_proceed()
                                initiate_active_primary(self,
                                                        patroni_local_url, py_env)

                                # Let the Passive peer know active-passive should happen
                                fwd_to_peer_q.put({"event": "from_peer", "message": {
                                                  "event": "transition_to", "state": transition}})

                                next_state = transition
                            elif cluster == config.get('passive_site'):

                                work = initiate_passive_standby(self,
                                                                py_env)
                                if work is None:
                                    next_state = transition
                                else:
                                    self.triggers.append(work)
                                    next_state = "%s-partial" % transition

                            self.update_switchover_state(
                                next_state, '', None, py_env)

                        elif transition == 'gold-standby':

                            if self.peer == 'error':
                                logger.warn(
                                    "Aborting gold-standby transition - peer not reachable")
                                self.METRIC.labels(
                                    resource="logic", state="warning").inc()

                            elif last_stable_state != 'golddr-primary':
                                logger.warn(
                                    "Aborting gold-standby transition - can only transition from golddr-primary")
                                self.METRIC.labels(
                                    resource="logic", state="warning").inc()

                            elif cluster == config.get('active_site'):
                                # TODO: Do a: is_peer_happy_to_proceed()
                                work = initiate_active_standby(
                                    self, py_env)
                                if work is None:
                                    next_state = transition
                                else:
                                    self.triggers.append(work)
                                    next_state = "%s-partial" % transition

                                # Let the Passive peer know gold-standby should happen
                                fwd_to_peer_q.put({"event": "from_peer", "message": {
                                                  "event": "transition_to", "state": transition}})

                            elif cluster == config.get('passive_site'):
                                # do nothing - passive site is already primary
                                # initiate_passive_primary(
                                #     namespace, patroni_local_url, py_env)
                                next_state = transition

                            self.update_switchover_state(
                                next_state, '', None, py_env)

                        elif transition == 'golddr-primary':

                            if cluster == config.get('active_site'):

                                initiate_active_down(py_env)

                                # Let the Passive peer know golddr-primary should happen
                                # : if AUTOMATION_ENABLED, then GSLB should trigger this anyway on peer
                                fwd_to_peer_q.put({"event": "from_peer", "message": {
                                                  "event": "transition_to", "state": transition}})

                                next_state = transition

                            elif cluster == config.get('passive_site'):

                                initiate_passive_primary(self,
                                                         patroni_local_url, py_env)

                                next_state = transition

                            self.update_switchover_state(
                                next_state, '', None, py_env)

                        elif transition == 'golddr-maintenance':

                            if self.peer == 'error':
                                logger.warn(
                                    "Aborting golddr-maintenance transition - peer not reachable")
                                self.METRIC.labels(
                                    resource="logic", state="warning").inc()

                            elif last_stable_state != 'active-passive':
                                logger.warn(
                                    "Aborting golddr-maintenance transition - can only transition from active-passive")
                                self.METRIC.labels(
                                    resource="logic", state="warning").inc()

                            elif cluster == config.get('active_site'):
                                # Do nothing - maintenance not currently tested for Active site
                                next_state = transition

                            elif cluster == config.get('passive_site'):

                                initiate_passive_maintenance(self,
                                                             namespace, patroni_local_url, py_env)
                                next_state = transition

                            self.update_switchover_state(
                                next_state, '', None, py_env)

                        else:
                            logger.error(
                                "Unsupported transition '%s'" % transition)
                    else:
                        logger.debug(
                            "Configmap update - last state is already same as transition - no work to do")
                        self.update_switchover_state(
                            None, '', None, py_env)

            except Exception as ex:
                logger.error(
                    'Unknown error in logic. %s' % ex)
                traceback.print_exc(file=sys.stdout)
                self.METRIC.labels(resource="logic", state="error").inc()

    def clear_triggers(self):
        self.triggers.clear()

    def set_pipeline(self, pipeline: dict):
        self.pipeline = pipeline

    def update_switchover_state(self, last_stable_state: str, transition: str, maintenance: str, py_env: str):
        name = config['switchover_state_configmap']
        ns = config['switchover_namespace']
        data = dict()
        if last_stable_state is not None:
            data['last_stable_state'] = last_stable_state
            data['last_stable_state_ts'] = datetime.datetime.now()
        if transition is not None:
            data['transition'] = transition
        data['maintenance'] = maintenance
        update_configmap(ns, name, py_env, dict(data=data))

    def pick_params(self, list, keys):
        pairs = {}
        for item in list:
            if item['name'] in keys:
                pairs[item['name']] = item['value']
        return pairs

    def patroni_has_no_standby_concerns(self):
        return self.patroni['control'] == 'up' and len(self.patroni['concerns']) == 0 and self.patroni['leader']['role'] == 'standby_leader'
