"""GET /api/fetch?url=…: reads a public web page for the page to explain (lae_reader.read_page)."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _shared import Handler as Base, WEB_PORTS  # noqa: E402
from lae_reader import read_page  # noqa: E402


class handler(Base):
    NAME, PER_MINUTE = "fetch", 20

    def answer(self, query):
        return read_page(self.url_param(query), ports=WEB_PORTS)
