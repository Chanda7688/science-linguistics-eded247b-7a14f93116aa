#!/usr/bin/env python3
"""Validate that [metadata].subcategory is a real subdomain of the declared [metadata].category.

    check-subcategory.py <task_dir>

The valid (domain -> subdomains) map is parsed from docs/TAXONOMY.md in this repo, so the check
stays in sync with the taxonomy doc. A subcategory that isn't listed under its category fails
(closed set). Casing is matched case-insensitively but the canonical spelling is recommended.
"""
import os
import re
import sys

try:
    import tomllib
except ModuleNotFoundError:  # py<3.11
    import tomli as tomllib  # type: ignore

VALID_DOMAINS = {"Science", "Software", "ML", "Operations", "Security", "Hardware", "Media"}


def load_taxonomy(path):
    """Parse '### <Domain>' sections and their '- **<Subdomain>**' bullets."""
    domains, cur = {}, None
    with open(path, encoding="utf-8") as f:
        for line in f:
            m = re.match(r"^###\s+(.+?)\s*$", line)
            if m:
                cur = m.group(1).strip()
                if cur in VALID_DOMAINS:
                    domains[cur] = []
                else:
                    cur = None  # ignore non-domain headers (e.g. "Adding a domain…")
                continue
            m = re.match(r"^-\s+\*\*(.+?)\*\*", line)
            if m and cur:
                domains[cur].append(m.group(1).strip())
    return domains


def main():
    if len(sys.argv) < 2:
        print("usage: check-subcategory.py <task_dir>")
        return 0
    task_dir = sys.argv[1]
    toml = os.path.join(task_dir, "task.toml")
    tax = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "docs", "TAXONOMY.md")
    if not os.path.isfile(toml):
        print(f"FAIL {task_dir}: missing task.toml")
        return 1
    if not os.path.isfile(tax):
        print(f"FAIL: docs/TAXONOMY.md not found (expected at {tax})")
        return 1
    domains = load_taxonomy(tax)
    try:
        data = tomllib.load(open(toml, "rb"))
    except Exception as e:
        print(f"FAIL {toml}: invalid TOML: {e}")
        return 1

    md = data.get("metadata") or {}
    cat = str(md.get("category") or "").strip()
    sub = str(md.get("subcategory") or "").strip()

    if not sub:
        print(f"FAIL {toml}: [metadata].subcategory is empty — set it to a subdomain from docs/TAXONOMY.md")
        return 1

    if cat not in domains:
        # The category itself is invalid; check-task-fields reports that. Still confirm the
        # subcategory is a known subdomain somewhere so we don't silently pass a bogus value.
        all_subs = {s for subs in domains.values() for s in subs}
        if any(sub.lower() == s.lower() for s in all_subs):
            print(f'PASS (category "{cat}" is not a domain — see check-task-fields; "{sub}" is a known subdomain)')
            return 0
        print(f'FAIL {toml}: subcategory "{sub}" is not a subdomain listed in docs/TAXONOMY.md')
        return 1

    subs = domains[cat]
    if sub in subs:
        print(f"PASS {task_dir}: {cat} / {sub}")
        return 0
    ci = [s for s in subs if s.lower() == sub.lower()]
    if ci:
        print(f'Warning subcategory "{sub}" should use the canonical spelling "{ci[0]}"')
        print(f"PASS {task_dir}: {cat} / {ci[0]}")
        return 0
    print(f'FAIL {toml}: subcategory "{sub}" is not a subdomain of {cat}. '
          f'Valid {cat} subdomains: {", ".join(subs)}. See docs/TAXONOMY.md')
    return 1


if __name__ == "__main__":
    sys.exit(main())
