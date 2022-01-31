import os

default = 'logic_handler,peer_server,peer_client,peer_client_fwd,dns_watch'

def is_enabled (a):
  processes = os.environ.get('PROCESS_LIST', default).split(',')
  return a in processes
