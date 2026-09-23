"""Allows `python cli-version` as well as `python -m imt.cli`."""
import sys
from imt.cli import main

sys.exit(main())
