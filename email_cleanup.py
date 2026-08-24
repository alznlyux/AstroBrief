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


def _cleanup_tex_spacing(text: str) -> str:
    """Turn common TeX spacing commands in ordinary text into email-safe spacing."""
    value = text
    for command in (r"\,", r"\;", r"\:", r"\ "):
        value = value.replace(command, " ")
    value = value.replace(r"\!", "")
    # Avoid doubled spaces introduced by source markup while preserving newlines.
    value = re.sub(r"[ \t]{2,}", " ", value)
    return value


def _cleanup_magnitude_superscripts(text: str) -> str:
    """Render the common astronomical magnitude superscript m as Unicode ᵐ.

    arXiv abstracts sometimes write magnitudes as $4^{m}$ or $6^m$.
    This is only a presentation cleanup; it does not reinterpret arbitrary
    variables or normalize A_V/Av notation.
    """

    def convert_math(match: re.Match[str]) -> str:
        fragment = match.group(1)
        fragment = re.sub(r"\^\{m\}", "ᵐ", fragment)
        fragment = re.sub(r"\^m(?![A-Za-z])", "ᵐ", fragment)
        return f"${fragment}$"

    return re.sub(r"(?<!\\)\$(.+?)(?<!\\)\$", convert_math, text, flags=re.S)


def cleanup_email_markdown(text: str) -> str:
    value = _cleanup_author_lines(text)
    value = _cleanup_simple_charge_scripts(value)
    value = _cleanup_magnitude_superscripts(value)
    value = _cleanup_tex_spacing(value)
    return value


def send_email(markdown_text: str, n_selected: int) -> None:
    """Apply small source-normalization fixes before semantic_daily formatting."""
    _semantic_send_email(cleanup_email_markdown(markdown_text), n_selected)
