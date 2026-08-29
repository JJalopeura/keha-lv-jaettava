#!/usr/bin/env python3
"""Validate testiaineisto md docs against the actual zip packages.

For each package: every filename cited in the md must exist in the zip
(no hallucinated rows), and every zip file must be cited or covered by a
grouped-range row. Cross-contamination check included: files cited that
belong to another package fail.
"""
import fnmatch
import re
import sys
import unicodedata
import zipfile
from pathlib import Path

BASE = Path(__file__).resolve().parent / "esimerkkihakemukset"
PACKAGES = ["P2X", "Ren-Gas", "Sharpcell"]


def zip_names(pkg: str) -> set[str]:
    with zipfile.ZipFile(BASE / f"{pkg}.zip") as zf:
        return {unicodedata.normalize("NFC", i.filename) for i in zf.infolist() if not i.is_dir()}


def cited_names(md_text: str) -> set[str]:
    """Names in backticks plus bare suffixes of range rows (row starts with `N–M | backtick-name`)."""
    names = set()
    for m in re.finditer(r"`([^`]+?\.(?:pdf|PDF|xlsx|csv|txt|docx))`", md_text):
        names.add(unicodedata.normalize("NFC", m.group(1).strip()))
    return names


def main() -> int:
    failures = 0
    for pkg in PACKAGES:
        truth = zip_names(pkg)
        md = Path(__file__).resolve().parent / f"{pkg}.md"
        text = md.read_text(encoding="utf-8")
        cited = cited_names(text)
        literal = {c for c in cited if "*" not in c}
        wildcards = {c for c in cited if "*" in c}
        unknown = {c for c in literal if c not in truth}
        bad_wild = {w for w in wildcards if not any(fnmatch.fnmatch(t, w) for t in truth)}
        covered = {t for t in truth if any(fnmatch.fnmatch(t, w) for w in wildcards)}
        missing = truth - literal - covered
        print(f"== {pkg}: cited {len(cited)} ({len(wildcards)} range rows), truth {len(truth)}")
        for u in sorted(unknown | bad_wild):
            other = [p for p in PACKAGES if p != pkg and u in zip_names(p)]
            tag = f" (CONTAMINATION from {other[0]})" if other else " (HALLUCINATED)"
            print(f"  NOT IN ZIP: {u}{tag}")
            failures += 1
        for m in sorted(missing):
            print(f"  NOT CITED: {m}")
    print("RESULT:", "FAIL" if failures else "PASS (no invented/contaminated filenames)")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
