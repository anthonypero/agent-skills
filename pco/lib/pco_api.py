"""
Generic Planning Center (PCO) API client — the importable core of the `pco`
skill. Standard library only (urllib); no dependencies to install.

Every PCO product (Services, People, Check-Ins, Giving, Groups, Calendar)
speaks the same JSON:API dialect behind one host. This module handles what is
common to every call — credential resolution, HTTP Basic auth with a Personal
Access Token, rate-limit retry, pagination — and knows nothing about any
product's schema. Paths therefore always include the product prefix:

    client.get("/services/v2/songs/123")
    client.get_all("/people/v2/people")        # generator over every page

Credential resolution order (first hit wins):
  1. Explicit PCOClient(app_id=..., secret=...) arguments
  2. PCO_APP_ID / PCO_SECRET environment variables
  3. The nearest .agents/PROJECT_SECRETS.md walking up from the working
     directory — the per-project convention, so each project automatically
     talks to the PCO account whose token its secrets file holds
  4. ~/.config/pco/credentials.md, with optional `## <profile>` sections —
     select one via PCOClient(profile=...), `pco --profile <name>`, or
     $PCO_PROFILE (default section name: 'default'; a file with no `##`
     headings is treated as the default profile)

Secrets files use the standard markdown convention:  - **NAME** = `value`
"""

import base64
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request

API_ROOT = "https://api.planningcenteronline.com"

_SECRET_LINE_RE = re.compile(r"^\s*-\s*\*\*([A-Za-z0-9_]+)\*\*\s*=\s*`([^`]*)`",
                             re.MULTILINE)
_PROFILE_HEADING_RE = re.compile(r"^##\s+(.+?)\s*$", re.MULTILINE)


def _parse_secret_lines(text: str) -> dict[str, str]:
    return {m.group(1): m.group(2) for m in _SECRET_LINE_RE.finditer(text)}


def _parse_profiles(text: str) -> dict[str, dict[str, str]]:
    """Split a credentials file into {profile_name: {NAME: value}}. Content
    before the first `## heading` (or the whole file if there are none) is
    the 'default' profile."""
    profiles: dict[str, dict[str, str]] = {}
    matches = list(_PROFILE_HEADING_RE.finditer(text))
    head = text[: matches[0].start()] if matches else text
    if _parse_secret_lines(head):
        profiles["default"] = _parse_secret_lines(head)
    for i, m in enumerate(matches):
        end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        section = _parse_secret_lines(text[m.start():end])
        if section:
            profiles[m.group(1).strip().lower()] = section
    return profiles


def _nearest_project_secrets(start_dir: str) -> str | None:
    """Path of the closest .agents/PROJECT_SECRETS.md at or above start_dir."""
    d = os.path.abspath(start_dir)
    while True:
        candidate = os.path.join(d, ".agents", "PROJECT_SECRETS.md")
        if os.path.isfile(candidate):
            return candidate
        parent = os.path.dirname(d)
        if parent == d:
            return None
        d = parent


def resolve_credentials(app_id: str | None = None, secret: str | None = None,
                        profile: str | None = None) -> tuple[str, str, str]:
    """Return (app_id, secret, source_description). Raises RuntimeError with
    a checklist of everywhere it looked if nothing resolves."""
    if app_id and secret:
        return app_id, secret, "explicit arguments"

    env_id, env_secret = os.environ.get("PCO_APP_ID"), os.environ.get("PCO_SECRET")
    if env_id and env_secret:
        return env_id, env_secret, "environment variables"

    project_secrets = _nearest_project_secrets(os.getcwd())
    if project_secrets:
        with open(project_secrets, "r", encoding="utf-8") as f:
            vals = _parse_secret_lines(f.read())
        if vals.get("PCO_APP_ID") and vals.get("PCO_SECRET"):
            return vals["PCO_APP_ID"], vals["PCO_SECRET"], project_secrets

    config_path = os.path.expanduser("~/.config/pco/credentials.md")
    wanted = (profile or os.environ.get("PCO_PROFILE") or "default").lower()
    if os.path.isfile(config_path):
        with open(config_path, "r", encoding="utf-8") as f:
            profiles = _parse_profiles(f.read())
        section = profiles.get(wanted)
        if section is None:
            raise RuntimeError(
                f"No profile '{wanted}' in {config_path} "
                f"(found: {', '.join(sorted(profiles)) or 'none'}).")
        if section.get("PCO_APP_ID") and section.get("PCO_SECRET"):
            return (section["PCO_APP_ID"], section["PCO_SECRET"],
                    f"{config_path} [{wanted}]")

    raise RuntimeError(
        "No PCO credentials found. Looked for: PCO_APP_ID/PCO_SECRET env vars; "
        f"{project_secrets or '.agents/PROJECT_SECRETS.md above ' + os.getcwd()}; "
        f"{config_path} (profile '{wanted}'). "
        "Tokens: https://api.planningcenteronline.com/oauth/applications")


class PCOClient:
    def __init__(self, app_id: str | None = None, secret: str | None = None,
                 profile: str | None = None):
        app_id, secret, self.credential_source = resolve_credentials(
            app_id, secret, profile)
        token = f"{app_id}:{secret}"
        self._auth_header = "Basic " + base64.b64encode(token.encode()).decode()

    def _request(self, method: str, path: str, body: dict | None = None) -> dict:
        url = path if path.startswith("http") else API_ROOT + path
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(url, data=data, method=method)
        req.add_header("Authorization", self._auth_header)
        if data is not None:
            req.add_header("Content-Type", "application/json")
        for attempt in range(3):
            try:
                with urllib.request.urlopen(req) as resp:
                    raw = resp.read()
                    return json.loads(raw.decode()) if raw else {}
            except urllib.error.HTTPError as e:
                # 429 = rate limited (PCO allows ~100 req / 20s); back off and retry.
                if e.code == 429 and attempt < 2:
                    wait = int(e.headers.get("Retry-After", "5"))
                    print(f"Rate limited; waiting {wait}s...", file=sys.stderr)
                    time.sleep(wait)
                    continue
                detail = e.read().decode(errors="replace")[:500]
                raise RuntimeError(f"PCO API {e.code} on {method} {url}: {detail}") from e
        raise RuntimeError(f"PCO API retries exhausted on {method} {url}")

    def get(self, path: str) -> dict:
        return self._request("GET", path)

    def post(self, path: str, body: dict) -> dict:
        return self._request("POST", path, body)

    def patch(self, path: str, body: dict) -> dict:
        return self._request("PATCH", path, body)

    def delete(self, path: str) -> dict:
        return self._request("DELETE", path)

    def get_all(self, path: str, per_page: int = 100):
        """Yield every item of a paginated collection, following next links."""
        sep = "&" if "?" in path else "?"
        url = f"{API_ROOT}{path}{sep}per_page={per_page}"
        while url:
            page = self._request("GET", url)
            yield from page["data"]
            url = page.get("links", {}).get("next")
