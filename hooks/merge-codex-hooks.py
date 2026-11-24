import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from merge_hooks import main


if __name__ == "__main__":
    main(True)
