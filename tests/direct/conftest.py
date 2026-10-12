"""Direct-mode gltest fixtures for MatchStake.

Pins SDK resolution to THIS contract (the py-genlayer hash in its header)
per the gltest setup_sdk_paths pitfall so CI and dev boxes resolve the same
runner + std-lib deterministically.
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from gltest.direct.sdk_loader import setup_sdk_paths

CONTRACT_PATH = ROOT / "contracts" / "matchstake.py"
setup_sdk_paths(CONTRACT_PATH)
