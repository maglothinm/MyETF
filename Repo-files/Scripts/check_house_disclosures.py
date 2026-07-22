#!/usr/bin/env python3
"""Compatibility entrypoint for the consolidated House disclosure monitor."""

from __future__ import annotations

import sys

from monitor_disclosures import main


if __name__ == "__main__":
    raise SystemExit(main(["--source", "house", *sys.argv[1:]]))
