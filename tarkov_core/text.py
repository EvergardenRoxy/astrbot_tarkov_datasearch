"""Cleaning of third-party strings before they reach a chat message."""
import re
import unicodedata

SPACES = re.compile(r'\s+')


def clean(value, limit):
    """`value` on one line: control and line/paragraph separators become spaces, format characters (zero-width,
    bidi overrides) are dropped, runs of whitespace collapse, then it is cut to `limit` characters."""
    out = []
    for char in str(value):
        category = unicodedata.category(char)
        if category in ('Cc', 'Zl', 'Zp'):
            out.append(' ')
        elif category != 'Cf':
            out.append(char)
    return SPACES.sub(' ', ''.join(out)).strip()[:limit]
