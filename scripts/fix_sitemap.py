#!/usr/bin/env python3

from pathlib import Path
import re

p = Path("docs/sitemap.xml")

if not p.exists():
    raise SystemExit("docs/sitemap.xml not found")

s = p.read_text()

# Root page:
s = s.replace(
    "https://asengupta.in/index.html",
    "https://asengupta.in/"
)

# Nested pages:
s = re.sub(
    r'https://asengupta\.in/(.+?)/index\.html',
    r'https://asengupta.in/\1/',
    s
)

p.write_text(s)

print("Canonicalized sitemap URLs.")
