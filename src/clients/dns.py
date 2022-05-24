import socket
import asyncio
import logging
import time
import requests
from prometheus_client import Counter

logger = logging.getLogger(__name__)

COUNTER = Counter('switchover_dns', 'Switchover DNS results', ['ip'])


def dns_watch(mechanism, domain_name: str, logic_q):

    new_loop = asyncio.new_event_loop()
    asyncio.set_event_loop(new_loop)
    new_loop.create_task(dns_lookup(mechanism, domain_name, logic_q))
    new_loop.run_forever()


async def dns_lookup(dns_service_url: str, domain_name: str, logic_q):
    logger.info("DNS Inspection %s" % domain_name)
    last_result = "unknown"
    while True:
        result = 'none'
        try:
            if dns_service_url == '':
              lookup = socket.getaddrinfo(domain_name, 0)
            else:
              lookup = requests.get(dns_service_url).json()
              if len(lookup) == 0:
                raise socket.gaierror('Simulated no DNS response')
              
            logger.debug("DNS %s", lookup)
            if len(lookup) > 0:
                ip = lookup[0][4][0]
                logger.debug("IP => %s" % ip)
                result = ip

        except socket.gaierror:
            logger.error("No DNS response")
            result = 'error'

        COUNTER.labels(ip=result).inc()
        if last_result != result:
            logic_q.put(
                {'event': 'dns', 'result': result, 'message': 'IP CHANGE %s' % result})
            last_result = result
        time.sleep(5)
