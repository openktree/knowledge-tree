"""Tests for authentication email templates."""

from kt_api.auth.email_templates import password_reset_email_html, verification_email_html


class TestVerificationEmailHtml:
    def test_contains_verify_url(self) -> None:
        url = "https://research.openktree.com/verify?token=abc123"
        html = verification_email_html(url, "user@example.com")
        # Button link + fallback plain-text link
        assert html.count(url) >= 2

    def test_contains_user_email(self) -> None:
        html = verification_email_html("https://example.com/verify?token=x", "alice@example.com")
        assert "alice@example.com" in html

    def test_contains_welcome_message(self) -> None:
        html = verification_email_html("https://example.com/verify?token=x", "a@b.com")
        assert "Welcome" in html
        assert "Knowledge Tree" in html

    def test_contains_ignore_disclaimer(self) -> None:
        html = verification_email_html("https://example.com/verify?token=x", "a@b.com")
        assert "did not create an account" in html

    def test_is_valid_html(self) -> None:
        html = verification_email_html("https://example.com/verify?token=x", "a@b.com")
        assert html.startswith("<!DOCTYPE html>")
        assert "</html>" in html


class TestPasswordResetEmailHtml:
    def test_contains_reset_url(self) -> None:
        url = "https://research.openktree.com/reset-password?token=xyz789"
        html = password_reset_email_html(url, "user@example.com")
        assert html.count(url) >= 2

    def test_contains_user_email(self) -> None:
        html = password_reset_email_html("https://example.com/reset?token=x", "bob@example.com")
        assert "bob@example.com" in html

    def test_contains_security_disclaimer(self) -> None:
        html = password_reset_email_html("https://example.com/reset?token=x", "a@b.com")
        assert "did not request a password reset" in html
        assert "password will not be changed" in html

    def test_is_valid_html(self) -> None:
        html = password_reset_email_html("https://example.com/reset?token=x", "a@b.com")
        assert html.startswith("<!DOCTYPE html>")
        assert "</html>" in html
