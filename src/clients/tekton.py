import os
import requests
from requests.structures import CaseInsensitiveDict
import logging
import asyncio
import time
import json
import urllib3
from config import config

logger = logging.getLogger(__name__)


def trigger_tekton_build(tekton_url: str, github_repo: str, github_ref: str, hmac_secret: str):

    base_template = {
        "ref": github_ref,
        "repository": {"name": "", "clone_url": github_repo},
        "head_commit": {"id": "", "message": "", "author": { "username": "switchover_agent" } }
    }

    payload = json.dumps(base_template)

    headers = CaseInsensitiveDict()
    headers["Accept"] = "application/json"
    headers["Content-Type"] = "application/json"
    headers["X-GitHub-Event"] = "push"
    headers["X-Hub-Signature"] = sign_request(
        hmac_secret.encode(), payload.encode())

    r = requests.post(tekton_url, headers=headers,
                      data=payload)
    logger.info("trigger_tekton_build %s" % r)
    r.raise_for_status()
    return r.json()


def sign_request(key, bytedata):
    from hashlib import sha1
    import hmac

    hashed = hmac.new(key, bytedata, sha1)

    return "sha1=%s" % hashed.hexdigest()
