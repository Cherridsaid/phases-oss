"""Group E: env-style secret names and vendor token grammars are redacted.

Positive cases: the names the 2026-07-15 audit found leaking, plus raw
vendor tokens pasted outside any assignment. Negative cases: prose that
merely contains a sensitive word must survive untouched -- the gate stays
best effort, but it must not cry wolf.
"""

import unittest

from phases_oss.data_gate import redact


class TestEnvStyleAssignments(unittest.TestCase):
    POSITIVE = [
        "AWS_SECRET_ACCESS_KEY=wJalrXUtnFEMI/K7MDENGbPxRfiCYEXAMPLEKEY",
        "AWS_SESSION_TOKEN=FwoGZXIvYXdzEBEaDDEXAMPLEEXAMPLE",
        "OPENAI_API_KEY=sk-" + "proj-abc123def456ghi789",
        "ANTHROPIC_API_KEY=sk-" + "ant-api03-abcdef123456",
        "SLACK_TOKEN=xox" + "b-123456789-abcdefghij",
        "STRIPE_SECRET_KEY=sk_" + "live_abcdef123456",
        "GOOGLE_API_KEY=AI" + "zaSyAbCdEf123456789",
        "DB_PASSWORD=hunter2secret",
        "MY_APP_SECRET: some-long-secret-value",
        "export CUSTOM_TOKEN=abcdef123456789012345",
        "GITHUB_TOKEN=gh" + "p_abcdef123456789012345",
        '{"api_key": "abcdef123456"}',
        "password = supersecret123",
        "DATABASE_CREDENTIALS=user:pass@host",
    ]

    def test_assignments_are_redacted(self):
        for sample in self.POSITIVE:
            with self.subTest(sample=sample[:32]):
                redacted, actions = redact(sample)
                self.assertNotEqual(redacted, sample, "leaked: %r" % sample)
                self.assertTrue(actions)

    def test_value_is_gone_but_field_name_is_kept(self):
        redacted, _ = redact("AWS_SECRET_ACCESS_KEY=wJalrXUtnFEMI/K7MDENGbPx")
        self.assertIn("AWS_SECRET_ACCESS_KEY", redacted)
        self.assertNotIn("wJalrXUtnFEMI", redacted)


class TestVendorTokensOutsideAssignments(unittest.TestCase):
    """A raw token in a pasted log line has no NAME= context; the grammar
    itself must be enough."""

    TOKENS = [
        ("sk-" + "ant-api03-Zm9vYmFyYmF6cXV4", "anthropic"),
        ("sk-" + "proj-abcdefghij1234567890", "openai"),
        ("xox" + "b-1234567890-abcdefghijklmno", "slack"),
        ("sk_" + "live_abcdefghij1234567890", "stripe"),
        ("AI" + "zaSyD-abcdefghij1234567890", "google"),
    ]

    def test_raw_tokens_are_redacted(self):
        for token, vendor in self.TOKENS:
            line = "curl failed, request id 42, auth was %s (retrying)" % token
            with self.subTest(vendor=vendor):
                redacted, actions = redact(line)
                self.assertNotIn(token, redacted, "%s token leaked" % vendor)
                self.assertTrue(actions)


class TestNoFalsePositives(unittest.TestCase):
    CLEAN = [
        "the token bucket algorithm limits requests per second",
        "password managers are recommended for everyone",
        "secretary: Johnathan will take the minutes",
        "tokenizer: whitespace with fallback to bytes",
        "GET /users/123/profile returned 200",
        "the secret to good bread is patience",
        "risk_level: 3 and proof_scope: full-suite",
    ]

    def test_prose_survives_untouched(self):
        for sample in self.CLEAN:
            with self.subTest(sample=sample[:32]):
                redacted, _ = redact(sample)
                self.assertEqual(redacted, sample, "over-redacted: %r" % sample)


if __name__ == "__main__":
    unittest.main()
