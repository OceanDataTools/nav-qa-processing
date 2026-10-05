import os
from pathlib import Path

os.environ.setdefault("MPLBACKEND", "Agg")

ROOT = Path(__file__).parents[1]
FIXTURES = Path(__file__).parent / "fixtures"
DATA = ROOT / "data"
CONFIGS = ROOT / "configs"
