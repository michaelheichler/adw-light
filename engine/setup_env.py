from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from lib.first_run import main


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
