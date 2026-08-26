#!/usr/bin/env python3
"""send_as.py — send Gmail with an explicit From (a verified send-as alias).

gws gmail send always uses the account's default send-as identity; this script
calls the Gmail API directly with the same OAuth credentials gws holds, so the
From header can be any alias verified in Gmail's "Send mail as" settings.
Gmail silently rewrites From to the default identity if the alias is NOT verified.

Usage:
  send_as.py --from claude-clt@anthonypero.com --to user@example.com \
             --subject "..." (--body "..." | --body-file path) \
             [--thread-id ID --in-reply-to RFC822_MSG_ID]
"""
import argparse
import base64
import json
import sys
import urllib.parse
import urllib.request
from email.message import EmailMessage
from pathlib import Path

GWS_DIR = Path.home() / ".config" / "gws"


def gws_credentials() -> dict:
    creds = {}
    for line in (GWS_DIR / "config.yaml").read_text().splitlines():
        if ":" in line:
            k, _, v = line.partition(":")
            creds[k.strip()] = v.strip().strip('"')
    creds.update(json.loads((GWS_DIR / "token.json").read_text()))
    return creds


def access_token(creds: dict) -> str:
    data = urllib.parse.urlencode({
        "client_id": creds["client_id"],
        "client_secret": creds["client_secret"],
        "refresh_token": creds["refresh_token"],
        "grant_type": "refresh_token",
    }).encode()
    req = urllib.request.Request("https://oauth2.googleapis.com/token", data=data)
    with urllib.request.urlopen(req) as resp:
        return json.load(resp)["access_token"]


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--from", dest="sender", required=True)
    p.add_argument("--to", required=True)
    p.add_argument("--subject", required=True)
    p.add_argument("--body")
    p.add_argument("--body-file")
    p.add_argument("--thread-id")
    p.add_argument("--in-reply-to")
    args = p.parse_args()

    body = args.body if args.body is not None else Path(args.body_file).read_text()

    msg = EmailMessage()
    msg["From"] = args.sender
    msg["To"] = args.to
    msg["Subject"] = args.subject
    if args.in_reply_to:
        msg["In-Reply-To"] = args.in_reply_to
        msg["References"] = args.in_reply_to
    msg.set_content(body)

    payload = {"raw": base64.urlsafe_b64encode(msg.as_bytes()).decode()}
    if args.thread_id:
        payload["threadId"] = args.thread_id

    token = access_token(gws_credentials())
    req = urllib.request.Request(
        "https://gmail.googleapis.com/gmail/v1/users/me/messages/send",
        data=json.dumps(payload).encode(),
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req) as resp:
            print(json.dumps(json.load(resp), indent=2))
    except urllib.error.HTTPError as e:
        print(f"HTTP {e.code}: {e.read().decode()}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
