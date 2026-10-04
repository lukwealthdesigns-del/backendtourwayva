"""HTML-safety helpers for outgoing email.

Anything that reaches an email body from a user (a name, a trip title, a message
body) MUST be escaped: an invitation email is sent to a THIRD PARTY, so an
unescaped trip title or inviter name would let one user put their own markup —
links, fake "verify your account" buttons — into an email that arrives from
Tour-Wayva. Pure stdlib.
"""
from __future__ import annotations

import html
from typing import Any


def esc(value: Any) -> str:
    """Escape a value for inclusion in HTML text or a quoted attribute."""
    return html.escape("" if value is None else str(value), quote=True)


def paragraphs_html(text: Any) -> str:
    """Plain text -> safe HTML: escaped, blank lines start a new paragraph,
    single newlines become <br>."""
    blocks = [b for b in str(text or "").replace("\r\n", "\n").split("\n\n") if b.strip()]
    if not blocks:
        return "<p></p>"
    return "".join(f"<p>{esc(block.strip()).replace(chr(10), '<br>')}</p>" for block in blocks)
