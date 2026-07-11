import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from discover_and_add import (
    norm_addr,
    build_delivered_map,
    sent_recipient_addresses,
    filter_known_correspondents,
    filter_new_identities,
    DELIVERED_TO_PROP,
)


def mail(from_addr, delivered_to):
    return {
        "from": [{"email": from_addr}] if from_addr else [],
        DELIVERED_TO_PROP: list(delivered_to),
    }


class TestNormAddr(unittest.TestCase):
    def test_plain(self):
        self.assertEqual(norm_addr("Foo@Example.COM"), "foo@example.com")

    def test_display_name(self):
        self.assertEqual(norm_addr("Alice <Alice@Ex.com>"), "alice@ex.com")

    def test_empty(self):
        self.assertEqual(norm_addr(None), "")
        self.assertEqual(norm_addr(""), "")


class TestBuildDeliveredMap(unittest.TestCase):
    def test_groups_senders_and_lowercases(self):
        emails = [
            mail("Bob@Ex.com", ["Alias1@Me.com"]),
            mail("carol@ex.com", ["alias1@me.com"]),
            mail("dave@ex.com", ["Alias2@Me.com"]),
        ]
        m = build_delivered_map(emails)
        self.assertEqual(set(m), {"alias1@me.com", "alias2@me.com"})
        self.assertEqual(m["alias1@me.com"], {"bob@ex.com", "carol@ex.com"})
        self.assertEqual(m["alias2@me.com"], {"dave@ex.com"})

    def test_multiple_delivered_to_headers(self):
        m = build_delivered_map([mail("s@ex.com", ["a@me.com", "b@me.com"])])
        self.assertEqual(m["a@me.com"], {"s@ex.com"})
        self.assertEqual(m["b@me.com"], {"s@ex.com"})

    def test_ignores_missing(self):
        m = build_delivered_map([mail(None, [])])
        self.assertEqual(m, {})


class TestSentRecipients(unittest.TestCase):
    def test_collects_all_fields(self):
        sent = [{
            "to": [{"email": "A@ex.com"}],
            "cc": [{"email": "b@ex.com"}],
            "bcc": [{"email": "c@ex.com"}],
        }]
        self.assertEqual(sent_recipient_addresses(sent), {"a@ex.com", "b@ex.com", "c@ex.com"})

    def test_empty(self):
        self.assertEqual(sent_recipient_addresses([{}]), set())


class TestFilterKnownCorrespondents(unittest.TestCase):
    def test_keeps_only_aliases_with_a_known_sender(self):
        delivered = {
            "alias1@me.com": {"bob@ex.com", "news@spam.com"},
            "alias2@me.com": {"news@spam.com"},
        }
        sent = {"bob@ex.com"}
        self.assertEqual(filter_known_correspondents(delivered, sent), {"alias1@me.com"})

    def test_none_known(self):
        self.assertEqual(filter_known_correspondents({"a@me.com": {"x@ex.com"}}, set()), set())


class TestFilterNewIdentities(unittest.TestCase):
    def test_drops_existing_and_sorts(self):
        candidates = {"b@me.com", "a@me.com", "c@me.com"}
        existing = {"C@Me.com"}
        self.assertEqual(filter_new_identities(candidates, existing), ["a@me.com", "b@me.com"])


if __name__ == "__main__":
    unittest.main()
