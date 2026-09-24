#!/usr/bin/env python3
"""Fail when the illustrations fall behind the code.

The README prose has been updated by hand three times while assets/menu.svg
still advertised a renamed rewrite mode or a menu item that no longer
existed. Images are the first thing a reader sees, and nothing else in CI
looks at them.
"""

import pathlib
import re
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
SOURCE = (ROOT / "sotto.py").read_text()
MENU_SVG = (ROOT / "assets" / "menu.svg").read_text()


def rewrite_modes():
    block = re.search(r"^REWRITE_MODES = \{(.*?)^\}", SOURCE, re.S | re.M)
    if not block:
        sys.exit("could not find REWRITE_MODES in sotto.py")
    return re.findall(r'"[a-z]+":\s*"([^"]+)"', block.group(1))


def menu_items():
    """Titles from the status menu's `actions` tuple."""
    block = re.search(r"^        actions = \((.*?)^        \)", SOURCE, re.S | re.M)
    if not block:
        sys.exit("could not find the status menu actions tuple in sotto.py")
    return re.findall(r'\("([^"]+)",\s*"[a-zA-Z]+:"', block.group(1))


def main():
    missing = []
    for label in rewrite_modes():
        if f">{label}<" not in MENU_SVG:
            missing.append(f"rewrite mode {label!r}")
    for title in menu_items():
        if title not in MENU_SVG:
            missing.append(f"menu item {title!r}")
    if missing:
        print("assets/menu.svg is out of date with sotto.py:")
        for item in missing:
            print(f"  - {item} is in the code but not the illustration")
        print("\nUpdate assets/menu.svg so the README screenshots match the app.")
        return 1
    print(f"menu.svg matches sotto.py ({len(menu_items())} items, "
          f"{len(rewrite_modes())} rewrite modes)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
