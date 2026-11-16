"""GET /api/config: tells the page it is on the hosted site (the local server answers "local")."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _shared import Handler as Base  # noqa: E402


class handler(Base):
    NAME, PER_MINUTE = "config", 0  # no limit: answered from memory

    def do_GET(self):
        self.send_json(200, {"mode": "hosted"})
