import logging
from clients.tekton import trigger_tekton_build
from config import config

logger = logging.getLogger(__name__)


def retry_deploy() -> str:
    """Re-triggers the Tekton deployment pipeline without repeating any
    transition side-effects (cluster mutations, env-var patches, etc.) that
    already ran on the first attempt.

    Returns the new Tekton event ID to update tracking state.
    """
    pipeline_event = trigger_tekton_build(
        config.get("tekton_trigger_url"),
        config.get("tekton_github_repo"),
        config.get("tekton_github_ref"),
        config.get("tekton_github_hmac_signature"),
    )
    logger.warning("Retry triggered tekton event %s" % pipeline_event['eventID'])
    return pipeline_event['eventID']
