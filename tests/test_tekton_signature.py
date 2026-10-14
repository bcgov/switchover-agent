"""The Tekton GitHub interceptor accepts only X-Hub-Signature-256."""
import hashlib
import hmac
import json

from clients.tekton import trigger_tekton_build


SECRET = "webhook-secret"
REF = "refs/heads/deploy/prod"
REPO = "https://github.com/bcgov-c/api-serv-infra.git"


class _Response:
    status_code = 202
    text = ""

    def raise_for_status(self):
        return None

    def json(self):
        return {"eventID": "evt-1"}

    def __str__(self):
        return "<Response [202]>"


def _mac(algo, body: bytes) -> str:
    return hmac.new(SECRET.encode(), body, algo).hexdigest()


def test_trigger_signs_posted_body_with_sha256(monkeypatch):
    captured = {}

    def fake_post(url, headers, data):
        captured["url"] = url
        captured["headers"] = headers
        captured["data"] = data
        return _Response()

    monkeypatch.setattr("clients.tekton.requests.post", fake_post)

    result = trigger_tekton_build("http://el.example/hook", REPO, REF, SECRET)

    body = captured["data"].encode()
    assert json.loads(captured["data"])["ref"] == REF
    assert captured["headers"]["X-GitHub-Event"] == "push"
    assert captured["headers"]["X-Hub-Signature"] == "sha1=%s" % _mac(hashlib.sha1, body)
    assert captured["headers"]["X-Hub-Signature-256"] == "sha256=%s" % _mac(hashlib.sha256, body)
    assert result["eventID"] == "evt-1"
