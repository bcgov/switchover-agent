import requests
from requests.structures import CaseInsensitiveDict
import logging
import asyncio
import time
import json
import urllib3
from prometheus_client import Gauge

logger = logging.getLogger(__name__)


def patroni_worker(patroni_url: str, logic_q):

    GAUGE = Gauge('switchover_patroni', 'Switchover Patroni Status',
                  ['state']).labels(state="active")

    new_loop = asyncio.new_event_loop()
    asyncio.set_event_loop(new_loop)
    new_loop.create_task(patroni_query(patroni_url, logic_q, GAUGE))
    new_loop.run_forever()


async def patroni_query(patroni_url: str, logic_q, GAUGE):
    logger.info("Patroni Query %s" % patroni_url)
    last_result = "unknown"
    while True:
        try:
            config = inspect_config(patroni_url)
            config.update(inspect_cluster(patroni_url))
            config_str = json.dumps(config)
            if last_result != config_str:
                logger.debug("New information %s", config_str)
                message = dict(event="patroni", control="up")
                message.update(config)
                logic_q.put(message)
                last_result = config_str
            GAUGE.set(1)

        except urllib3.exceptions.ReadTimeoutError:
            logger.error(
                'Read timeout in patroni query. Failing.')
            GAUGE.set(0)

            message = dict(event="patroni", control="down")
            logic_q.put(message)
            last_result = json.dumps(message)

        except requests.exceptions.ConnectionError:
            logger.error(
                'Failed to connect to Patroni Controller API')
            GAUGE.set(0)

            message = dict(event="patroni", control="down")
            logic_q.put(message)
            last_result = json.dumps(message)
        except Exception as ex:
            logger.error(
                'Unknown error in patroni query. Failing.')
            GAUGE.set(0)

            message = dict(event="patroni", control="down")
            logic_q.put(message)
            last_result = json.dumps(message)
        time.sleep(5)


def inspect_config(patroni_url: str):
    r = requests.get("%s/config" % patroni_url, timeout=2)
    if r.status_code != 200:
        raise Exception('Failed communication with Patroni %d' % r.status_code)
    data = r.json()
    return dict(
        is_standby_configured="standby_cluster" in data
    )


def inspect_cluster(patroni_url: str):
    r = requests.get("%s/cluster" % patroni_url, timeout=2)
    if r.status_code != 200:
        raise Exception('Failed communication with Patroni %d' % r.status_code)
    data = r.json()
    concerns = []
    leader = None
    for member in data['members']:
        if member['state'] != 'running':
            concerns.append(
                dict(member=member['name'], role=member['role'], state=member['state']))
        if member['role'] == 'leader' or member['role'] == 'standby_leader':
            leader = dict(member=member['name'], role=member['role'])

    return dict(
        member_count=len(data['members']),
        concerns=concerns,
        leader=leader
    )


def set_readonly_cluster(patroni_url: str):

    data = {"standby_cluster": {"create_replicas_methods": [
        "basebackup_fast_xlog"], "restore_command": "envdir \"/run/etc/wal-e.d/env-standby-patroni-spilo\" /scripts/restore_command.sh \"%f\" \"%p\""}}

    headers = CaseInsensitiveDict()
    headers["Accept"] = "application/json"
    headers["Content-Type"] = "application/json"

    r = requests.patch('%s/config' %
                       (patroni_url), headers=headers, data=json.dumps(data))
    logger.info("set_readonly_cluster %s" % r)


def set_standby_cluster(patroni_url: str, primary_host: str, primary_port: int):

    data = {"standby_cluster": {"host": primary_host, "port": primary_port, "create_replicas_methods": [
        "basebackup_fast_xlog"], "restore_command": "envdir \"/run/etc/wal-e.d/env-standby-patroni-spilo\" /scripts/restore_command.sh \"%f\" \"%p\""}}

    headers = CaseInsensitiveDict()
    headers["Accept"] = "application/json"
    headers["Content-Type"] = "application/json"

    r = requests.patch('%s/config' %
                       (patroni_url), headers=headers, data=json.dumps(data))
    logger.info("set_standby_cluster %s" % r)


def set_primary_cluster(patroni_url: str):

    data = dict(standby_cluster=None)

    headers = CaseInsensitiveDict()
    headers["Accept"] = "application/json"
    headers["Content-Type"] = "application/json"

    r = requests.patch('%s/config' %
                       (patroni_url), headers=headers, data=json.dumps(data))
    logger.info("set_primary_cluster %s" % r)
