import os
import sys

# Allow `import fetch_owned_reviews`, `import fetch_competitor_ratings`,
# `import http_utils`, etc. regardless of the directory pytest is invoked
# from (CI runs `pytest backend/tests/` from the repo root).
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
