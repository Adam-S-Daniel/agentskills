#!/usr/bin/env python3
"""Add one or more Fastmail sending identities ("From" addresses) via JMAP.

Auth: set FASTMAIL_API_TOKEN in the environment, or write the token to
~/.fastmail_token. Create the token in Fastmail at
Settings -> Privacy & Security -> Integrations -> API tokens, granting
read-write access to Mail (this covers reading messages and managing sending
identities via the JMAP submission capability).

Usage:
  add_identity.py ADDR [ADDR ...] [--name "Full Name"] [--dry-run]

Adds each address as a selectable From identity. Idempotent: addresses that are
already identities are skipped. Applies by default; pass --dry-run to preview.
Own-domain aliases are auto-verified by Fastmail; an address you do not control
comes back with a pending verification state instead of being usable.
"""
import argparse
import json
import os
import sys
import urllib.request
import urllib.error

SESSION_URL = "https://api.fastmail.com/jmap/session"
USING = [
    "urn:ietf:params:jmap:core",
    "urn:ietf:params:jmap:mail",
    "urn:ietf:params:jmap:submission",
]


def get_token():
    tok = os.environ.get("FASTMAIL_API_TOKEN")
    if not tok:
        path = os.path.expanduser("~/.fastmail_token")
        if os.path.exists(path):
            with open(path) as fh:
                tok = fh.read().strip()
    if not tok:
        sys.exit("error: set FASTMAIL_API_TOKEN (or write the token to ~/.fastmail_token)")
    return tok


def _req(url, token, data=None):
    headers = {"Authorization": "Bearer " + token, "Accept": "application/json"}
    body = None
    method = "GET"
    if data is not None:
        body = json.dumps(data).encode()
        headers["Content-Type"] = "application/json"
        method = "POST"
    req = urllib.request.Request(url, data=body, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req) as resp:
            return json.load(resp)
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode(errors="replace")[:500]
        sys.exit("HTTP %s from Fastmail: %s" % (exc.code, detail))


class Jmap:
    def __init__(self, token):
        self.token = token
        session = _req(SESSION_URL, token)
        self.api_url = session["apiUrl"]
        self.account_id = session["primaryAccounts"]["urn:ietf:params:jmap:mail"]
        core = session["capabilities"].get("urn:ietf:params:jmap:core", {})
        self.max_get = core.get("maxObjectsInGet") or 500

    def call(self, method_calls):
        payload = {"using": USING, "methodCalls": method_calls}
        resp = _req(self.api_url, self.token, payload)
        return resp["methodResponses"]


def get_identities(j):
    r = j.call([["Identity/get", {"accountId": j.account_id, "ids": None}, "0"]])
    return r[0][1]["list"]


def get_sent_mailbox_id(j):
    r = j.call([["Mailbox/get", {"accountId": j.account_id, "properties": ["id", "role"]}, "0"]])
    for mb in r[0][1]["list"]:
        if mb.get("role") == "sent":
            return mb["id"]
    return None


def add_identities(j, addresses, name, sent_id, existing_emails, apply=True):
    """Create a sending identity for each address not already present.

    Returns a list of (address, status, detail) tuples. status is one of
    "skipped", "would-add", "added", "failed".
    """
    results = []
    to_create = {}
    for i, addr in enumerate(addresses):
        if addr.lower() in existing_emails:
            results.append((addr, "skipped", "already an identity"))
            continue
        obj = {
            "email": addr,
            "name": name or "",
            "replyTo": None,
            "bcc": None,
            "textSignature": "",
            "htmlSignature": "",
            "showInCompose": True,
            "useForAutoReply": True,
            "mayDelete": True,
            "isAutoConfigured": False,
            "enableExternalSMTP": False,
        }
        if sent_id:
            obj["saveSentToMailboxId"] = sent_id
        to_create[str(i)] = obj

    if not apply:
        for obj in to_create.values():
            results.append((obj["email"], "would-add", ""))
        return results

    if to_create:
        r = j.call([["Identity/set", {"accountId": j.account_id, "create": to_create}, "0"]])
        setres = r[0][1]
        created = setres.get("created") or {}
        notcreated = setres.get("notCreated") or {}
        for cid, obj in to_create.items():
            if cid in created:
                info = created[cid]
                results.append((
                    obj["email"],
                    "added",
                    "verification=%s id=%s" % (info.get("verificationState", "?"), info.get("id", "?")),
                ))
            elif cid in notcreated:
                results.append((obj["email"], "failed", json.dumps(notcreated[cid])))
            else:
                results.append((obj["email"], "failed", "no result returned"))
    return results


def main():
    ap = argparse.ArgumentParser(description="Add Fastmail sending identities via JMAP.")
    ap.add_argument("addresses", nargs="+", help="Email address(es) to add as From identities.")
    ap.add_argument("--name", default=None, help="Display name (defaults to an existing identity's name).")
    ap.add_argument("--dry-run", action="store_true", help="Show what would be added without changing anything.")
    args = ap.parse_args()

    j = Jmap(get_token())
    identities = get_identities(j)
    existing = {i["email"].lower() for i in identities}
    name = args.name
    if name is None:
        name = next((i.get("name") for i in identities if i.get("name")), "")
    sent_id = get_sent_mailbox_id(j)

    results = add_identities(j, args.addresses, name, sent_id, existing, apply=not args.dry_run)
    for addr, status, detail in results:
        print("%-10s %s  %s" % (status, addr, detail))


if __name__ == "__main__":
    main()
