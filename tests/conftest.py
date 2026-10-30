import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))  # import modules from the project root
sys.path.insert(0, str(Path(__file__).resolve().parent))  # and shared fakes from tests/
