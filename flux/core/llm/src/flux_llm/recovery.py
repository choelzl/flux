"""Bounded recovery from a model server reload, without replaying tools or changing sessions."""

from __future__ import annotations

import re
import time
import urllib.error
import urllib.request
from collections.abc import Callable

# A model's API error, not arbitrary tool output containing a number or a failed check.
_TRANSIENT = re.compile(r"\b(?:502|503|504|529)\b|bad gateway|service unavailable|gateway timeout|"
                        r"connection (?:reset|refused|aborted)|ECONN(?:RESET|REFUSED)|"
                        r"server disconnected|remote end closed connection|socket hang up", re.I)
WAIT_S = 300.0
POLL_S = 5.0


def transient_error(message: str) -> bool:
    return bool(_TRANSIENT.search(message))


def wait_ready(base_url: str | None, headers: dict[str, str], deadline: float,
               report: Callable[[str], None]) -> bool:
    """Wait for this provider's models endpoint. Unknown/unsupported probes retry the agent
    after a pause: its next request tests readiness. A models probe may be forbidden even
    when the credential can generate; the agent's real API response decides whether it failed.
    The caller owns the deadline, which includes every wait and resumed turn.
    """
    while (remaining := deadline - time.monotonic()) > 0:
        report("waiting for the model endpoint to recover")
        if base_url:
            url = base_url.rstrip("/")
            if not url.endswith("/v1") and url.rsplit("/", 1)[-1] not in ("v2", "v3", "v4"):
                url += "/v1"
            request = urllib.request.Request(url + "/models", headers=headers)
            try:
                with urllib.request.urlopen(request, timeout=min(5.0, remaining)) as response:
                    if response.status == 200:
                        return True
            except urllib.error.HTTPError as exc:
                exc.close()
                if exc.code in (401, 403, 404, 405):
                    base_url = None      # this server does not offer a models probe
            except (urllib.error.URLError, OSError):
                pass
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            break
        time.sleep(min(POLL_S, remaining))
        if base_url is None:
            return time.monotonic() < deadline
    report("model endpoint recovery reached the time limit")
    return False
