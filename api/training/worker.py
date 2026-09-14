from __future__ import annotations

import sys

from api.training.application import main as run_application


def main() -> int:
    return run_application()


if __name__ == "__main__":
    sys.exit(main())
