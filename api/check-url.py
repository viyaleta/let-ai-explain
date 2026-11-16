"""GET /api/check-url?url=…: checks that a cited page exists and reads its title (lae_reader.check_url)."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _shared import Handler as Base, WEB_PORTS  # noqa: E402
from lae_reader import check_url  # noqa: E402


class handler(Base):
    NAME, PER_MINUTE = "check-url", 120  # one explanation checks up to ~30 references

    def answer(self, query):
        return check_url(self.url_param(query), ports=WEB_PORTS)
