"""Make scripts/ importable so tests can `import news_archive`, `import poll_live_news`, etc."""
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "scripts"))
