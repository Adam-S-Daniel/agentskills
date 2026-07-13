#!/usr/bin/env python3
"""Discover alias addresses worth sending from, then add them as Fastmail
identities.

Pipeline (each stage is a plain function, unit-tested in tests/):
  1. Collect every distinct X-Delivered-To address across all messages.
  2. Keep only those where at least one sender is someone you have sent mail to
     (i.e. a real correspondent), dropping aliases that only ever received
     one-way mail such as newsletters.
  3. Drop any that are already available as sending identities.
Then feed the survivors to the same add routine the add-from-address skill uses.

Auth: FASTMAIL_API_TOKEN env var, or ~/.fastmail_token. Needs read-write Mail
access. See the add-from-address skill for token setup.

Usage:
  discover_and_add.py [--apply] [--name "Full Name"] [--max N]

Defaults to a dry run (prints the addresses it would add). Pass --apply to
actually create the identities.
"""
import argparse
import email.utils
import json
import os
import subprocess
import sys
import urllib.request
import urllib.error

SESSION_URL = "https://api.fastmail.com/jmap/session"
USING = [
    "urn:ietf:params:jmap:core",
    "urn:ietf:params:jmap:mail",
    "urn:ietf:params:jmap:submission",
]
DELIVERED_TO_PROP = "header:X-Delivered-To:asText:all"


# --- JMAP transport (mirrors add-from-address/add_identity.py) ---

def get_token():
    tok = os.environ.get("FASTMAIL_API_TOKEN")
    if not tok:
        cmd = os.environ.get("FASTMAIL_TOKEN_CMD")
        if cmd:
            proc = subprocess.run(cmd, shell=True, capture_output=True, text=True)
            if proc.returncode != 0:
                sys.exit("error: FASTMAIL_TOKEN_CMD failed: " + (proc.stderr.strip() or "exit %d" % proc.returncode))
            tok = proc.stdout.strip()
    if not tok:
        path = os.path.expanduser("~/.fastmail_token")
        if os.path.exists(path):
            with open(path) as fh:
                tok = fh.read().strip()
    if not tok:
        sys.exit("error: no token. Set FASTMAIL_API_TOKEN, or FASTMAIL_TOKEN_CMD (a command that prints the token), or write the token to ~/.fastmail_token")
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
        self.max_get = min(core.get("maxObjectsInGet") or 500, 500)

    def call(self, method_calls):
        payload = {"using": USING, "methodCalls": method_calls}
        resp = _req(self.api_url, self.token, payload)
        return resp["methodResponses"]


# --- pure logic (unit-tested; no network) ---

def norm_addr(value):
    _name, addr = email.utils.parseaddr(value or "")
    return addr.strip().lower()


def build_delivered_map(emails):
    """emails: Email/get objects with 'from' and the X-Delivered-To header list.

    Returns {delivered_to_addr: set(sender_addrs)}.
    """
    result = {}
    for e in emails:
        senders = set()
        for f in (e.get("from") or []):
            a = norm_addr(f.get("email"))
            if a:
                senders.add(a)
        for raw in (e.get(DELIVERED_TO_PROP) or []):
            dto = norm_addr(raw)
            if dto:
                result.setdefault(dto, set()).update(senders)
    return result


def sent_recipient_addresses(emails):
    """Every address you have sent to (to/cc/bcc across your Sent mail)."""
    out = set()
    for e in emails:
        for field in ("to", "cc", "bcc"):
            for r in (e.get(field) or []):
                a = norm_addr(r.get("email"))
                if a:
                    out.add(a)
    return out


def filter_known_correspondents(delivered_map, sent_recipients):
    """Delivered-to addresses that received mail from at least one person you
    have also written to."""
    return {a for a, senders in delivered_map.items() if senders & sent_recipients}


def filter_new_identities(candidates, existing_emails):
    """Candidates that are not already sending identities, sorted."""
    existing = {e.lower() for e in existing_emails}
    return sorted(a for a in candidates if a not in existing)


# --- JMAP data gathering ---

def get_identities(j):
    r = j.call([["Identity/get", {"accountId": j.account_id, "ids": None}, "0"]])
    return r[0][1]["list"]


def get_sent_mailbox_id(j):
    r = j.call([["Mailbox/get", {"accountId": j.account_id, "properties": ["id", "role"]}, "0"]])
    for mb in r[0][1]["list"]:
        if mb.get("role") == "sent":
            return mb["id"]
    return None


def query_email_ids(j, filt, max_n=None):
    ids = []
    position = 0
    limit = j.max_get
    while True:
        q = {
            "accountId": j.account_id,
            "position": position,
            "limit": limit,
            "calculateTotal": True,
            "collapseThreads": False,
            "sort": [{"property": "receivedAt", "isAscending": False}],
        }
        if filt is not None:
            q["filter"] = filt
        r = j.call([["Email/query", q, "0"]])
        res = r[0][1]
        batch = res.get("ids", [])
        ids.extend(batch)
        total = res.get("total")
        position += len(batch)
        if max_n is not None and len(ids) >= max_n:
            return ids[:max_n], True
        if not batch or (total is not None and position >= total):
            break
    return ids, False


def fetch_emails(j, ids, properties):
    out = []
    step = j.max_get
    for k in range(0, len(ids), step):
        chunk = ids[k:k + step]
        r = j.call([["Email/get", {"accountId": j.account_id, "ids": chunk, "properties": properties}, "0"]])
        out.extend(r[0][1]["list"])
    return out


def add_identities(j, addresses, name, sent_id, existing_emails, apply=True):
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
    ap = argparse.ArgumentParser(description="Discover and add worth-having Fastmail From identities.")
    ap.add_argument("--apply", action="store_true", help="Actually create the identities (default: dry run).")
    ap.add_argument("--name", default=None, help="Display name for new identities (defaults to an existing identity's name).")
    ap.add_argument("--max", type=int, default=None, help="Cap the number of messages scanned (for a quick sample).")
    args = ap.parse_args()

    j = Jmap(get_token())

    identities = get_identities(j)
    existing = {i["email"].lower() for i in identities}
    name = args.name
    if name is None:
        name = next((i.get("name") for i in identities if i.get("name")), "")

    sent_id = get_sent_mailbox_id(j)
    sent_count = 0
    sent_recipients = set()
    if sent_id:
        sent_ids, _ = query_email_ids(j, {"inMailbox": sent_id})
        sent_count = len(sent_ids)
        sent_emails = fetch_emails(j, sent_ids, ["to", "cc", "bcc"])
        sent_recipients = sent_recipient_addresses(sent_emails)
    print("scanned %d sent messages -> %d distinct recipients you have written to" % (
        sent_count, len(sent_recipients)))

    all_ids, truncated = query_email_ids(j, None, max_n=args.max)
    if truncated:
        print("WARNING: scan capped at --max=%d messages; results are a sample, not exhaustive." % args.max)
    all_emails = fetch_emails(j, all_ids, ["from", DELIVERED_TO_PROP])
    delivered_map = build_delivered_map(all_emails)

    distinct = set(delivered_map)
    known = filter_known_correspondents(delivered_map, sent_recipients)
    new = filter_new_identities(known, existing)

    print("scanned %d messages" % len(all_emails))
    print("stage 1: %d distinct X-Delivered-To addresses" % len(distinct))
    print("stage 2: %d have a known correspondent" % len(known))
    print("stage 3: %d are not already identities" % len(new))

    if not new:
        print("nothing to add.")
        return

    print("")
    print("addresses to add:")
    for a in new:
        why = sorted(delivered_map[a] & sent_recipients)[:3]
        print("  %s   (correspondent: %s)" % (a, ", ".join(why)))

    if not args.apply:
        print("")
        print("dry run — re-run with --apply to add these %d address(es)." % len(new))
        return

    print("")
    results = add_identities(j, new, name, sent_id, existing, apply=True)
    for addr, status, detail in results:
        print("%-10s %s  %s" % (status, addr, detail))


if __name__ == "__main__":
    main()
