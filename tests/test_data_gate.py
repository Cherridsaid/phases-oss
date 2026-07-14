"""Tests for the data gate: allowlist (deny by default) + best-effort redaction."""

import unittest

from phases_oss.data_gate import DataGate, GateError, disclosure, redact


class TestRedact(unittest.TestCase):
    def _assert_gone(self, secret, text):
        redacted, reds = redact(text)
        self.assertNotIn(secret, redacted, "secret survived redaction: %r" % secret)
        return redacted, reds

    def test_aws_key(self):
        self._assert_gone("AKIA1234567890ABCDEF", "key: AKIA1234567890ABCDEF\n")

    def test_jwt(self):
        jwt = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJzdWIiOiIxMjM0In0.abc123_DEF-456"
        self._assert_gone(jwt, "auth=%s" % jwt)

    def test_github_token(self):
        tok = "ghp_abcdefghijklmnopqrstuvwxyz0123456789"
        self._assert_gone(tok, "token %s here" % tok)

    def test_bearer_token(self):
        red, _ = self._assert_gone("aitsecrettoken123", "Authorization: Bearer aitsecrettoken123")
        self.assertIn("Bearer [REDACTED:token]", red)

    def test_bearer_base64_token_redacted_whole(self):
        # A base64/base64url token carries + / = ; the whole token must go,
        # not just the leading alphanumerics before the first + or /.
        token = "ab+cd/ef=ghIJ0123"
        red, _ = self._assert_gone(token, "Authorization: Bearer " + token)
        self.assertIn("Bearer [REDACTED:token]", red)

    def test_bearer_word_in_prose_not_over_redacted(self):
        # "bearer of bad news" has no 8-char token run: leave the prose intact.
        text = "the bearer of bad news arrived"
        red, reds = redact(text)
        self.assertEqual(red, text)
        self.assertNotIn("REDACTED:token", red)
        self.assertEqual(reds, [])

    def test_secret_assignment_keeps_field_drops_value(self):
        red, _ = redact('password = "hunter2secret"')
        self.assertNotIn("hunter2secret", red)
        self.assertIn("password=[REDACTED:secret]", red)

    def test_private_key_block(self):
        block = (
            "-----BEGIN RSA PRIVATE KEY-----\n"
            "MIIEowIBAAKCAQEA0very+secret/material==\n"
            "-----END RSA PRIVATE KEY-----"
        )
        red, _ = self._assert_gone("0very+secret/material", block)
        self.assertIn("[REDACTED:private-key]", red)

    def test_email(self):
        self._assert_gone("alice.dev@example.com", "contact alice.dev@example.com please")

    def test_windows_user_path_drops_username(self):
        red, reds = redact(r"see C:\Users\jdupont\Desktop\thing.txt")
        self.assertNotIn("jdupont", red)
        self.assertIn(r"C:\Users\[REDACTED:user]", red)
        self.assertTrue(any(r.category == "win-user-path" for r in reds))

    def test_unix_home_drops_username(self):
        red, _ = redact("path /home/bob/code/app.py and /Users/carol/x")
        self.assertNotIn("/home/bob", red)
        self.assertNotIn("/Users/carol", red)

    def test_counts_are_accurate(self):
        _, reds = redact("a@b.com and c@d.org")
        email = [r for r in reds if r.category == "email"][0]
        self.assertEqual(email.count, 2)

    def test_clean_text_unchanged(self):
        text = "just some ordinary code: x = compute(y)\n"
        red, reds = redact(text)
        self.assertEqual(red, text)
        self.assertEqual(reds, [])


class TestDisclosure(unittest.TestCase):
    def test_lists_categories(self):
        _, reds = redact("a@b.com key: AKIA1234567890ABCDEF")
        note = disclosure(reds, "https://example.com/api", 42)
        self.assertIn("email", note)
        self.assertIn("aws-access-key", note)
        self.assertIn("example.com", note)
        self.assertIn("best-effort", note)

    def test_nothing_redacted_note(self):
        note = disclosure([], "https://example.com", 10)
        self.assertIn("No known", note)


class TestAllowlist(unittest.TestCase):
    def test_empty_allowlist_denies_everything(self):
        gate = DataGate()
        self.assertFalse(gate.is_allowed("https://example.com/x"))
        with self.assertRaises(GateError):
            gate.prepare("hello", "https://example.com/x")

    def test_allowed_host_passes(self):
        gate = DataGate(allowlist=["api.example.com"])
        self.assertTrue(gate.is_allowed("https://api.example.com/v1/chat"))
        payload = gate.prepare("token: AKIA1234567890ABCDEF", "https://api.example.com/v1/chat")
        self.assertNotIn("AKIA1234567890ABCDEF", payload.text)
        self.assertIn("api.example.com", payload.disclosure)

    def test_host_not_in_allowlist_denied(self):
        gate = DataGate(allowlist=["api.example.com"])
        self.assertFalse(gate.is_allowed("https://evil.example.org/x"))

    def test_bare_host_parsed(self):
        gate = DataGate(allowlist=["localhost"])
        self.assertTrue(gate.is_allowed("localhost:8789/path"))

    def test_case_insensitive_host(self):
        gate = DataGate(allowlist=["API.Example.COM"])
        self.assertTrue(gate.is_allowed("https://api.example.com/x"))

    def test_empty_destination_denied(self):
        gate = DataGate(allowlist=["api.example.com"])
        self.assertFalse(gate.is_allowed(""))
        self.assertFalse(gate.is_allowed("   "))

    def test_denied_destination_never_returns_text(self):
        # prepare must raise (not return) for a denied destination, so raw text
        # cannot leak through the return value.
        gate = DataGate(allowlist=["good.com"])
        with self.assertRaises(GateError):
            gate.prepare("secret AKIA1234567890ABCDEF", "https://bad.com")


class TestAdversarialHardening(unittest.TestCase):
    """Regression tests from the P3 adversarial audit (3 agents)."""

    def test_windows_path_mixed_separators(self):
        for text in (r"see C:\Users/victim\Desktop", "see C:/Users/victim/x"):
            red, _ = redact(text)
            self.assertNotIn("victim", red, text)

    def test_windows_path_doubled_backslashes(self):
        # The form found in JSON, logs, and Python/JS string literals.
        red, _ = redact("path C:\\\\Users\\\\victim\\\\Desktop")
        self.assertNotIn("victim", red)

    def test_unc_path_drops_username(self):
        red, _ = redact(r"open \\srv\Users\victim\share")
        self.assertNotIn("victim", red)

    def test_forward_slash_windows_path_no_phantom_category(self):
        # A forward-slash Windows path must report only win-user-path, not a
        # phantom unix-home-path from re-matching the placeholder (disclosure
        # accuracy).
        red, reds = redact("C:/Users/victim/x")
        self.assertNotIn("victim", red)
        cats = [r.category for r in reds]
        self.assertIn("win-user-path", cats)
        self.assertNotIn("unix-home-path", cats)

    def test_rest_users_path_not_false_redacted(self):
        # A REST URL path /users/list must NOT be treated as a home directory.
        red, reds = redact("GET https://api.example.com/users/list")
        self.assertIn("/users/list", red)
        self.assertFalse(any(r.category.endswith("user-path") for r in reds))

    def test_disclosure_renders_accurate_count(self):
        _, reds = redact("a@b.com and c@d.org")
        note = disclosure(reds, "https://x.test", 10)
        self.assertIn("email x2", note)

    def test_prepare_disclosure_lists_category_and_post_redaction_size(self):
        gate = DataGate(allowlist=["api.example.com"])
        payload = gate.prepare("key AKIA1234567890ABCDEF", "https://api.example.com/v1")
        self.assertTrue(payload.redactions)
        self.assertIn("aws-access-key", payload.disclosure)
        self.assertIn("payload size: %d" % len(payload.text), payload.disclosure)

    def test_url_userinfo_host_is_denied(self):
        # https://allowed.com@evil.com/ -> real host is evil.com -> denied.
        gate = DataGate(allowlist=["allowed.com"])
        self.assertFalse(gate.is_allowed("https://allowed.com@evil.com/"))

    def test_bare_userinfo_host_is_denied(self):
        # localhost:8789@evil.com -> host after '@' is evil.com -> denied.
        gate = DataGate(allowlist=["localhost"])
        self.assertFalse(gate.is_allowed("localhost:8789@evil.com/path"))

    def test_suffix_confusable_hosts_denied(self):
        # Exact host membership: look-alike suffixes/prefixes/trailing-dot are
        # all denied (fail closed).
        gate = DataGate(allowlist=["api.example.com"])
        for bad in (
            "https://api.example.com.evil.com/",
            "https://evilapi.example.com/",
            "https://api.example.com./",
        ):
            self.assertFalse(gate.is_allowed(bad), bad)


if __name__ == "__main__":
    unittest.main()
