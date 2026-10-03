import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "hermes_plugin"))   # `import mikronous` = the Hermes plugin package
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
