"""Professional HTML email templates for authentication flows."""

from __future__ import annotations

# Shared inline styles for email-safe rendering (no external CSS).
_WRAPPER_STYLE = (
    "margin:0;padding:0;background-color:#f4f4f5;"
    "font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,Helvetica,Arial,sans-serif"
)
_CARD_STYLE = (
    "max-width:520px;margin:40px auto;background:#ffffff;"
    "border-radius:8px;overflow:hidden;box-shadow:0 1px 3px rgba(0,0,0,0.1)"
)
_HEADER_STYLE = "background:#16a34a;padding:24px 32px;text-align:center"
_HEADER_TEXT_STYLE = "margin:0;color:#ffffff;font-size:20px;font-weight:600"
_BODY_STYLE = "padding:32px"
_BTN_STYLE = (
    "display:inline-block;padding:12px 32px;background:#16a34a;color:#ffffff;"
    "text-decoration:none;border-radius:6px;font-size:16px;font-weight:600"
)
_MUTED_STYLE = "color:#71717a;font-size:13px;line-height:1.5"
_FOOTER_STYLE = "padding:16px 32px;border-top:1px solid #e4e4e7;text-align:center"
_LINK_STYLE = "color:#16a34a;word-break:break-all"


def _wrap(header_text: str, body_html: str) -> str:
    """Wrap body content in the shared email chrome."""
    return f"""\
<!DOCTYPE html>
<html lang="en">
<head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"></head>
<body style="{_WRAPPER_STYLE}">
<table width="100%" cellpadding="0" cellspacing="0" role="presentation">
<tr><td align="center" style="padding:20px">
  <div style="{_CARD_STYLE}">
    <div style="{_HEADER_STYLE}">
      <h1 style="{_HEADER_TEXT_STYLE}">{header_text}</h1>
    </div>
    <div style="{_BODY_STYLE}">
      {body_html}
    </div>
    <div style="{_FOOTER_STYLE}">
      <p style="{_MUTED_STYLE}">&copy; Knowledge Tree &mdash; Built from real sources, not model knowledge.</p>
    </div>
  </div>
</td></tr>
</table>
</body>
</html>"""


def verification_email_html(verify_url: str, user_email: str) -> str:
    """Return the HTML body for a verification email."""
    body = f"""\
<p style="margin:0 0 16px;font-size:16px;color:#18181b">
  Welcome to <strong>Knowledge Tree</strong>!
</p>
<p style="margin:0 0 24px;font-size:15px;color:#3f3f46;line-height:1.6">
  We're excited to have you on board. To get started, please verify your email
  address (<strong>{user_email}</strong>) by clicking the button below.
</p>
<p style="text-align:center;margin:0 0 24px">
  <a href="{verify_url}" style="{_BTN_STYLE}">Verify Email Address</a>
</p>
<p style="margin:0 0 8px;{_MUTED_STYLE}">
  If the button doesn't work, copy and paste this link into your browser:
</p>
<p style="margin:0 0 24px;{_MUTED_STYLE}">
  <a href="{verify_url}" style="{_LINK_STYLE}">{verify_url}</a>
</p>
<p style="margin:0;{_MUTED_STYLE}">
  If you did not create an account with Knowledge Tree, you can safely ignore
  this email and no account will be activated.
</p>"""
    return _wrap("Welcome to Knowledge Tree", body)


def password_reset_email_html(reset_url: str, user_email: str) -> str:
    """Return the HTML body for a password-reset email."""
    body = f"""\
<p style="margin:0 0 16px;font-size:16px;color:#18181b">
  Password Reset Request
</p>
<p style="margin:0 0 24px;font-size:15px;color:#3f3f46;line-height:1.6">
  We received a request to reset the password for the account associated with
  <strong>{user_email}</strong>. Click the button below to choose a new password.
</p>
<p style="text-align:center;margin:0 0 24px">
  <a href="{reset_url}" style="{_BTN_STYLE}">Reset Password</a>
</p>
<p style="margin:0 0 8px;{_MUTED_STYLE}">
  If the button doesn't work, copy and paste this link into your browser:
</p>
<p style="margin:0 0 24px;{_MUTED_STYLE}">
  <a href="{reset_url}" style="{_LINK_STYLE}">{reset_url}</a>
</p>
<p style="margin:0;{_MUTED_STYLE}">
  If you did not request a password reset, you can safely ignore this email.
  Your password will not be changed.
</p>"""
    return _wrap("Password Reset", body)
