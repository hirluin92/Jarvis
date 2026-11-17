"""Glob matching for `protected_paths` of a product (repo-relative, forward slashes)."""

import re


def _regex(pattern: str) -> re.Pattern[str]:
    p = pattern.strip().replace("\\", "/").lstrip("./")
    if p.endswith("/"):
        p += "**"
    out, i = "", 0
    while i < len(p):
        if p.startswith("**", i):
            out += ".*"
            i += 2
        elif p[i] == "*":
            out += "[^/]*"
            i += 1
        elif p[i] == "?":
            out += "[^/]"
            i += 1
        else:
            out += re.escape(p[i])
            i += 1
    return re.compile(rf"^{out}(/.*)?$")  # a plain path also protects everything below it


def violations(changed: list[str], patterns: list[str]) -> list[str]:
    """The changed files that fall under a protected pattern."""
    rules = [_regex(p) for p in patterns if p.strip()]
    hit = []
    for f in changed:
        norm = f.replace("\\", "/").lstrip("./")
        if any(r.match(norm) for r in rules):
            hit.append(f)
    return hit
