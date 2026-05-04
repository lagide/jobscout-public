"""Monkey-patch jobspy: bump Indeed API timeout from 10s to 30s.

JobSpy hard-codes timeout=10 in jobspy/indeed/__init__.py, which causes frequent
ReadTimeouts on the NAS link to apis.indeed.com. We monkey-patch requests.Session.post
to detect Indeed API calls and bump the timeout. Safe and rebuild-proof.
"""
import logging
import requests

_INDEED_HOST = "apis.indeed.com"
_NEW_TIMEOUT = 30

_orig_post = requests.Session.post

def _patched_post(self, url, *args, **kwargs):
    if isinstance(url, str) and _INDEED_HOST in url:
        if kwargs.get("timeout") is None or (
            isinstance(kwargs.get("timeout"), (int, float)) and kwargs["timeout"] < _NEW_TIMEOUT
        ):
            kwargs["timeout"] = _NEW_TIMEOUT
    return _orig_post(self, url, *args, **kwargs)

requests.Session.post = _patched_post
logging.getLogger(__name__).info(
    "jobspy_patch: requests.Session.post patched (Indeed timeout %ds)", _NEW_TIMEOUT
)
