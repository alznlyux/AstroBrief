# coding: utf-8
"""Small presentation-layer fixes for arXiv email text.

This module only normalizes a few source forms that arXiv metadata may expose
in mixed TeX/Unicode form. The archived report/data remain unchanged.
"""
from __future__ import annotations

import re

from semantic_daily import send_email as _semantic_send_email


def _cleanup_author_lines(text: str) -> str:
    """Remove harmless TeX grouping braces around already-Unicode name letters."""
    cleaned = []
    for line in text.splitlines(keepends=True):
        if line.lstrip().startswith("- **Authors:**"):
            # Examples seen in arXiv Atom metadata include Stanimirovi{ć}
            # and D{é}nes: the accent is already Unicode, but TeX grouping
            # braces are still present. Strip braces only around one Unicode
            # letter, and only on the Authors line.
            line = re.sub(r"\{([^\W\d_])\}", r"\1", line, flags=re.UNICODE)
        cleaned.append(line)
    return "".join(cleaned)


def _cleanup_simple_charge_scripts(text: str) -> str:
    """Render common unbraced TeX charge scripts used between math delimiters."""
    return (
        text.replace("$^+$", "⁺")
        .replace("$^-$", "⁻")
        .replace("$_+$", "₊")
        .replace("$_-$", "₋")
    )


def cleanup_email_markdown(text: str) -> str:
    value = _cleanup_author_lines(text)
    value = _cleanup_simple_charge_scripts(value)
    return value


def send_email(markdown_text: str, n_selected: int) -> None:
    """Apply small source-normalization fixes before semantic_daily formatting."""
    _semantic_send_email(cleanup_email_markdown(markdown_text), n_selected)
