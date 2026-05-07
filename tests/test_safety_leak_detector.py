"""Tests for credential leak detector."""

from __future__ import annotations

from src.safety.leak_detector import (
    LeakAction,
    LeakDetector,
    _check_high_entropy,
    _shannon_entropy,
    redact,
    scrub_credentials,
)


class TestLeakDetector:
    def test_clean_text(self) -> None:
        d = LeakDetector()
        result = d.scan("This is a normal response about Python programming")
        assert result.clean
        assert not result.matches

    def test_anthropic_key(self) -> None:
        d = LeakDetector()
        result = d.scan("Your API key is sk-ant-abc123456789")
        assert not result.clean
        assert any(m.pattern_name == "anthropic_api_key" for m in result.matches)
        assert any(m.action == LeakAction.BLOCK for m in result.matches)

    def test_slack_token(self) -> None:
        d = LeakDetector()
        result = d.scan("Token: xoxb-123456-789012-abcdef")
        assert not result.clean
        assert any(m.pattern_name == "slack_bot_token" for m in result.matches)

    def test_github_pat(self) -> None:
        d = LeakDetector()
        result = d.scan("Use ghp_ABCDEFghijklmnop123456 for auth")
        assert not result.clean
        assert any(m.pattern_name == "github_pat" for m in result.matches)

    def test_prefix_fallback_detects_repeated_matches(self) -> None:
        d = LeakDetector()
        d._automaton = None
        result = d.scan("first sk-ant-one123456 second sk-ant-two123456")
        matches = [m for m in result.matches if m.pattern_name == "anthropic_api_key"]
        assert len(matches) == 2
        assert result.redacted_text is not None
        assert "one123456" not in result.redacted_text
        assert "two123456" not in result.redacted_text

    def test_private_key(self) -> None:
        d = LeakDetector()
        result = d.scan("-----BEGIN RSA PRIVATE KEY-----\nMIIE...")
        assert not result.clean
        assert any(m.pattern_name == "rsa_private_key" for m in result.matches)

    def test_jwt_token(self) -> None:
        d = LeakDetector()
        # Minimal valid-looking JWT
        header = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9"
        payload = "eyJzdWIiOiIxMjM0NTY3ODkwIiwibmFtZSI6IkpvaG4ifQ"
        sig = "SflKxwRJSMeKKF2QT4fwpMeJf36POk6yJV_adQssw5c"
        result = d.scan(f"Token: {header}.{payload}.{sig}")
        assert not result.clean
        assert any(m.pattern_name == "jwt_token" for m in result.matches)

    def test_db_connection_string(self) -> None:
        d = LeakDetector()
        result = d.scan("postgres://user:password@localhost:5432/mydb")
        assert not result.clean
        assert any(m.pattern_name == "db_connection_string" for m in result.matches)

    def test_redacted_output(self) -> None:
        d = LeakDetector()
        result = d.scan("Key is sk-ant-abc123 and more text")
        assert result.redacted_text is not None
        assert "sk-ant-[REDACTED]" in result.redacted_text
        assert "abc123" not in result.redacted_text

    def test_empty_text(self) -> None:
        d = LeakDetector()
        result = d.scan("")
        assert result.clean

    def test_aws_key(self) -> None:
        d = LeakDetector()
        result = d.scan("AWS key: AKIAIOSFODNN7EXAMPLE")
        assert not result.clean
        assert any(m.pattern_name == "aws_access_key" for m in result.matches)

    def test_should_block(self) -> None:
        d = LeakDetector()
        result = d.scan("sk-ant-secret123")
        assert result.should_block


def test_redact_partial_mask():
    assert redact("sk-ant-abc123xyz") == "sk-a***"


def test_redact_short_value():
    assert redact("abc") == "***"


def test_redact_exact_boundary():
    assert redact("abcd") == "***"
    assert redact("abcde") == "abcd***"


def test_redact_custom_visible():
    assert redact("sk-ant-abc123xyz", visible=8) == "sk-ant-a***"


def test_scrub_credentials_json_double_quoted():
    text = '"api_key": "sk-ant-abc123xyz789"'
    result = scrub_credentials(text)
    assert "sk-a***" in result
    assert "sk-ant-abc123xyz789" not in result


def test_scrub_credentials_env_style():
    text = "password=mysecretpassword123"
    result = scrub_credentials(text)
    assert "myse***" in result
    assert "mysecretpassword123" not in result


def test_scrub_credentials_single_quoted():
    text = "token = 'ghp_abcdef123456789012345'"
    result = scrub_credentials(text)
    assert "ghp_***" in result


def test_scrub_credentials_short_value_ignored():
    text = 'password="short"'
    result = scrub_credentials(text)
    assert result == text


def test_scrub_credentials_case_insensitive():
    text = 'API_KEY="sk-proj-abcdefghijk"'
    result = scrub_credentials(text)
    assert "sk-p***" in result


def test_scrub_credentials_no_match():
    text = "Hello world, nothing secret here."
    assert scrub_credentials(text) == text


def test_shannon_entropy_uniform():
    e = _shannon_entropy("abcdefghijklmnop")
    assert e > 3.5


def test_shannon_entropy_repetitive():
    assert _shannon_entropy("aaaaaaaaaaaaaaaa") == 0.0


def test_high_entropy_detects_random_token():
    text = "found token xK9mR2pL7qW4nJ6vB8cY3hF5gT0sA1dE in response"
    hits = _check_high_entropy(text, sensitivity=0.7)
    assert len(hits) >= 1
    assert hits[0][2] == "[REDACTED_HIGH_ENTROPY_TOKEN]"


def test_high_entropy_ignores_uuid():
    text = "id=550e8400-e29b-41d4-a716-446655440000 ok"
    hits = _check_high_entropy(text, sensitivity=0.7)
    assert len(hits) == 0


def test_high_entropy_ignores_hex_hash():
    text = "sha256=e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"
    hits = _check_high_entropy(text, sensitivity=0.7)
    assert len(hits) == 0


def test_high_entropy_ignores_base64_padded():
    text = "data=aGVsbG8gd29ybGQgdGhpcyBpcyBiYXNlNjQ="
    hits = _check_high_entropy(text, sensitivity=0.7)
    assert len(hits) == 0


def test_high_entropy_ignores_standard_base64_padded():
    text = "data=d2ljPDh+QSMmUVRZc28rSz9hICp0aGNHLjBDaj5pKlw="
    hits = _check_high_entropy(text, sensitivity=0.7)
    assert len(hits) == 0


def test_high_entropy_detects_padded_base64_secret():
    token = "xK9mR2pL7qW4nJ6vB8cY3hF5gT=="
    result = LeakDetector(entropy_sensitivity=0.7).scan(f"token={token}")
    assert not result.clean
    assert result.redacted_text is not None
    assert token not in result.redacted_text


def test_high_entropy_detects_encoded_known_secret_text():
    token = "YXBpIGtleSBzay1hbnQtc2VjcmV0MTIzNDU2Nzg5MA=="
    result = LeakDetector(entropy_sensitivity=0.7).scan(f"data={token}")
    assert not result.clean
    assert result.should_block
    assert result.redacted_text is not None
    assert token not in result.redacted_text


def test_high_entropy_detects_unpadded_standard_base64_with_slash_secret():
    token = "YXBpIGtleSBzay1hbnQtPz8/P3NlY3JldDEyMzQ1Njc4OTA"
    result = LeakDetector(entropy_sensitivity=0.7).scan(f"data={token}")
    assert not result.clean
    assert result.should_block
    assert result.redacted_text is not None
    assert token not in result.redacted_text


def test_high_entropy_detects_unpadded_standard_base64_with_multiple_slash_secret():
    token = "c2stYW50LT8/Pz8/Pz8/c2VjcmV0MTIzNDU2Nzg5MA"
    result = LeakDetector(entropy_sensitivity=0.7).scan(f"data={token}")
    assert not result.clean
    assert result.should_block
    assert result.redacted_text is not None
    assert token not in result.redacted_text


def test_high_entropy_detects_unpadded_standard_base64_with_slash_opaque_token():
    token = "e2HqrehIuRqovC2l1mD4Z/dHhdFJFJvyMytFBfYdac0"
    hits = _check_high_entropy(f"data={token}", sensitivity=0.7)
    assert len(hits) == 1


def test_high_entropy_detects_direct_multi_slash_token():
    token = "xK9mR2pL7qW4nJ6v/B8cY3hF5gT0sA1dE/Z9qW"
    hits = _check_high_entropy(f"data={token}", sensitivity=0.7)
    assert len(hits) == 1


def test_high_entropy_detects_encoded_generic_credential_text():
    token = "YXBpIGtleSBhYmNkZWZnaGlqa2xtbm9wcXJzdHV2d3h5ejEyMzQ1Ng=="
    result = LeakDetector(entropy_sensitivity=0.7).scan(f"data={token}")
    assert not result.clean
    assert result.redacted_text is not None
    assert token not in result.redacted_text


def test_high_entropy_detects_encoded_credential_prose():
    token = "YXBpIGtleSBpcyBhYmNkZWZnaGlqa2xtbm9wcXJzdHV2d3h5ejEyMzQ1Ng=="
    result = LeakDetector(entropy_sensitivity=0.7).scan(f"data={token}")
    assert not result.clean
    assert result.redacted_text is not None
    assert token not in result.redacted_text


def test_high_entropy_detects_encoded_high_entropy_text():
    token = "aGVyZSBpcyB4SzltUjJwTDdxVzRuSjZ2QjhjWTNoRjVnVDBzQTFkRQ=="
    result = LeakDetector(entropy_sensitivity=0.7).scan(f"data={token}")
    assert not result.clean
    assert result.redacted_text is not None
    assert token not in result.redacted_text


def test_high_entropy_detects_encoded_slash_delimited_token_text():
    token = "aGVyZSBpcyB4SzltUjJwTDdxVzRuSjZ2L0I4Y1kzaEY1Z1Qwc0ExZEU="
    result = LeakDetector(entropy_sensitivity=0.7).scan(f"data={token}")
    assert not result.clean
    assert result.redacted_text is not None
    assert token not in result.redacted_text


def test_high_entropy_detects_encoded_path_like_slash_token_text():
    token = "aGVyZSBpcyB0bXAveEs5bVIycEw3cVc0bko2di9COGNZM2hGNVFnVDBzQTFkRS9aOXFX"
    result = LeakDetector(entropy_sensitivity=0.7).scan(f"data={token}")
    assert not result.clean
    assert result.redacted_text is not None
    assert token not in result.redacted_text


def test_high_entropy_detects_encoded_low_entropy_credential_text():
    token = "cGFzc3dvcmQgYWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFhYWE="
    result = LeakDetector(entropy_sensitivity=0.7).scan(f"data={token}")
    assert not result.clean
    assert result.redacted_text is not None
    assert token not in result.redacted_text


def test_high_entropy_detects_unpadded_encoded_known_secret_text():
    token = "c2stYW50LWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFh"
    result = LeakDetector(entropy_sensitivity=0.7).scan(f"data={token}")
    assert not result.clean
    assert result.should_block
    assert result.redacted_text is not None
    assert token not in result.redacted_text


def test_high_entropy_detects_padded_encoded_known_secret_text_without_spaces():
    token = "c2stYW50LWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFhYWE="
    result = LeakDetector(entropy_sensitivity=0.7).scan(f"data={token}")
    assert not result.clean
    assert result.should_block
    assert result.redacted_text is not None
    assert token not in result.redacted_text


def test_high_entropy_ignores_safe_base64_with_slash_subtoken():
    text = (
        "data=UGxlYXNlIGVuY29kZSB0aGlzIGhhcm1sZXNzIG1lc3NhZ2UgYWI/"
        "IGZvciB0cmFuc3BvcnQgYmV0d2VlbiBzZXJ2aWNlcyB4eQ=="
    )
    hits = _check_high_entropy(text, sensitivity=0.7)
    assert len(hits) == 0


def test_high_entropy_does_not_treat_equals_delimiter_as_padding():
    token = "xK9mR2pL7qW4nJ6vB8cY3hF5gT0sA1dE"
    text = f"key={token}=next"
    hits = _check_high_entropy(text, sensitivity=0.7)
    assert len(hits) == 1
    start, end, _tag = hits[0]
    assert text[start:end] == token


def test_high_entropy_invalid_padding_still_detected():
    token = "xK9mR2pL7qW4nJ6vB8cY3hF5gT0sA1dE"
    for text in (f"key={token}=", f"key={token}=.", f"key={token}= ", f"key={token}==="):
        hits = _check_high_entropy(text, sensitivity=0.7)
        assert len(hits) == 1
        start, end, _tag = hits[0]
        assert text[start:end] == token


def test_high_entropy_slash_token_invalid_padding_still_detected():
    token = "xK9mR2pL7qW4nJ6v/B8cY3hF5gT0sA1dE"
    hits = _check_high_entropy(f"key={token}=", sensitivity=0.7)
    assert len(hits) == 1
    start, end, _tag = hits[0]
    assert f"key={token}="[start:end] == token


def test_high_entropy_ignores_url_content():
    text = "visit https://example.com/xK9mR2pL7qW4nJ6vB8cY3hF5gT0sA1dE/page"
    hits = _check_high_entropy(text, sensitivity=0.7)
    assert len(hits) == 0


def test_high_entropy_ignores_path_like_text():
    text = "/opt/homebrew/lib/node_modules/openclaw/dist/io-DaEsZ_NY.js"
    hits = _check_high_entropy(text, sensitivity=0.7)
    assert len(hits) == 0


def test_high_entropy_ignores_path_like_text_with_line_suffix():
    text = "/opt/homebrew/lib/node_modules/openclaw/dist/io-DaEsZ_NY.js:2888:32"
    hits = _check_high_entropy(text, sensitivity=0.7)
    assert len(hits) == 0


def test_high_entropy_ignores_relative_asset_path():
    text = "assets/chunk-A1B2C3D4E5F6G7H8I9J0K1L2.js"
    hits = _check_high_entropy(text, sensitivity=0.7)
    assert len(hits) == 0


def test_high_entropy_ignores_common_build_asset_paths():
    for text in (
        "assets/index-A1B2C3D4E5F6G7H8I9J0K1L2.js",
        "assets/main-A1B2C3D4E5F6G7H8I9J0K1L2.css",
        "dist/client/index-A1B2C3D4E5F6G7H8I9J0K1L2.js",
        "assets/chunk-A1B2C3D4E5F6G7H8I9J0K1L2.js:1:2",
    ):
        hits = _check_high_entropy(text, sensitivity=0.7)
        assert len(hits) == 0


def test_high_entropy_detects_path_leaf_token():
    token = "xK9mR2pL7qW4nJ6vB8cY3hF5gT0sA1dE"
    hits = _check_high_entropy(f"tmp/{token}", sensitivity=0.7)
    assert len(hits) == 1


def test_high_entropy_detects_path_leaf_slash_token():
    token = "xK9mR2pL7qW4nJ6v/B8cY3hF5gT0sA1dE/Z9qW"
    result = LeakDetector(entropy_sensitivity=0.7).scan(f"tmp/{token}")
    assert not result.clean
    assert result.redacted_text == "tmp/[REDACTED_HIGH_ENTROPY_TOKEN]"


def test_high_entropy_detects_path_intermediate_slash_token_with_extension():
    token = "xK9mR2pL7qW4nJ6v/B8cY3hF5gT0sA1dE"
    result = LeakDetector(entropy_sensitivity=0.7).scan(f"/tmp/{token}/file.txt")
    assert not result.clean
    assert result.redacted_text == "/tmp/[REDACTED_HIGH_ENTROPY_TOKEN].txt"


def test_high_entropy_detects_path_leaf_token_with_extension():
    token = "xK9mR2pL7qW4nJ6vB8cY3hF5gT0sA1dE"
    result = LeakDetector(entropy_sensitivity=0.7).scan(f"tmp/{token}.txt")
    assert not result.clean
    assert result.redacted_text == "tmp/[REDACTED_HIGH_ENTROPY_TOKEN].txt"


def test_high_entropy_detects_absolute_path_leaf_token_with_extension():
    token = "xK9mR2pL7qW4nJ6vB8cY3hF5gT0sA1dE"
    result = LeakDetector(entropy_sensitivity=0.7).scan(f"/tmp/{token}.txt")
    assert not result.clean
    assert result.redacted_text == "/tmp/[REDACTED_HIGH_ENTROPY_TOKEN].txt"


def test_high_entropy_detects_deep_path_leaf_token_with_extension():
    token = "xK9mR2pL7qW4nJ6vB8cY3hF5gT0sA1dE"
    result = LeakDetector(entropy_sensitivity=0.7).scan(f"tmp/foo/{token}.txt")
    assert not result.clean
    assert result.redacted_text == "tmp/foo/[REDACTED_HIGH_ENTROPY_TOKEN].txt"


def test_high_entropy_respects_sensitivity():
    token = "abcdefghijabcdefghij1234"
    text = f"key={token}"
    hits_low = _check_high_entropy(text, sensitivity=0.0)
    hits_high = _check_high_entropy(text, sensitivity=1.0)
    assert hits_low == []
    assert len(hits_high) == 1
    assert _check_high_entropy(text, sensitivity=-1.0) == hits_low
    assert _check_high_entropy(text, sensitivity=2.0) == hits_high


def test_high_entropy_url_overlap_fully_contained():
    """Token fully containing a URL span should still be ignored."""
    text = "see https://example.com/xK9mR2pL7qW4nJ6vB8cY3hF5gT0sA1dE end"
    hits = _check_high_entropy(text, sensitivity=0.7)
    assert len(hits) == 0


class TestLeakDetectorEntropy:
    """Verify entropy-based scanning is wired into LeakDetector.scan()."""

    def test_entropy_disabled_by_default(self) -> None:
        d = LeakDetector()
        token = "xK9mR2pL7qW4nJ6vB8cY3hF5gT0sA1dE"
        result = d.scan(f"token is {token}")
        assert result.clean  # no prefix/regex match, entropy off

    def test_entropy_enabled_detects_random_token(self) -> None:
        d = LeakDetector(entropy_sensitivity=0.7)
        token = "xK9mR2pL7qW4nJ6vB8cY3hF5gT0sA1dE"
        result = d.scan(f"token is {token}")
        assert not result.clean
        assert any(m.pattern_name == "high_entropy_token" for m in result.matches)
        assert result.redacted_text is not None
        assert token not in result.redacted_text
        assert "[REDACTED_HIGH_ENTROPY_TOKEN]" in result.redacted_text

    def test_entropy_redaction_preserves_offsets_after_known_secret(self) -> None:
        d = LeakDetector(entropy_sensitivity=0.7)
        secret = "sk-ant-secret1234567890"
        token = "xK9mR2pL7qW4nJ6vB8cY3hF5gT0sA1dE"
        result = d.scan(f"prefix {secret} token {token} suffix")

        assert not result.clean
        assert result.redacted_text is not None
        assert secret not in result.redacted_text
        assert token not in result.redacted_text
        assert "sk-ant-[REDACTED]" in result.redacted_text
        assert "[REDACTED_HIGH_ENTROPY_TOKEN]" in result.redacted_text

    def test_entropy_redaction_preserves_known_secret_style_on_overlap(self) -> None:
        d = LeakDetector(entropy_sensitivity=0.7)
        secret = "sk-ant-xK9mR2pL7qW4nJ6vB8cY3hF5gT0sA1dE"
        result = d.scan(f"prefix {secret} suffix")

        assert not result.clean
        assert result.redacted_text == "prefix sk-ant-[REDACTED] suffix"

    def test_entropy_redaction_handles_partial_known_secret_overlap(self) -> None:
        d = LeakDetector(entropy_sensitivity=0.7)
        token = "xK9mR2pL7qW4nJ6vB8cY3hF5gT0sA1dE"
        result = d.scan(f"prefix {token}sk-ant-secret123456 suffix")

        assert not result.clean
        assert result.redacted_text is not None
        assert token not in result.redacted_text
        assert result.redacted_text == (
            "prefix [REDACTED_HIGH_ENTROPY_TOKEN]sk-ant-[REDACTED] suffix"
        )

    def test_entropy_redaction_handles_full_slash_token_once(self) -> None:
        d = LeakDetector(entropy_sensitivity=0.7)
        token = "xK9mR2pL7qW4nJ6vB8cY3hF5gT0s/A1dExK9mR2pL7qW4nJ6vB8cY3hF5gT0s"
        result = d.scan(f"data={token} tail")

        assert not result.clean
        assert result.redacted_text == "data=[REDACTED_HIGH_ENTROPY_TOKEN] tail"

    def test_entropy_enabled_ignores_hex_hash(self) -> None:
        d = LeakDetector(entropy_sensitivity=0.7)
        sha = "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"
        result = d.scan(f"sha256={sha}")
        assert result.clean
