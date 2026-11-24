"""Package initialiser.

`backend/.env` is read here, before any submodule imports. That ordering is
deliberate: `app/scraper.py` reads its pacing knobs at import time, so a `.env`
loaded any later would arrive too late for them.
"""

from .env import load_dotenv

load_dotenv()
