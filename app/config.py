"""설정 로딩: config.yaml(공개 가능한 값) + .env(비밀 값)."""
import os
from pathlib import Path

import yaml
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data"
SCREENSHOT_DIR = DATA_DIR / "screenshots"
DB_PATH = DATA_DIR / "agent.db"

load_dotenv(ROOT / ".env")

with open(ROOT / "config.yaml", encoding="utf-8") as f:
    CONFIG = yaml.safe_load(f)

PROFILES: list[str] = CONFIG["profiles"]
COMMENT = CONFIG["comment"]
LIMITS = CONFIG["limits"]
SEARCH = CONFIG["search"]
RISING = CONFIG["rising"]
AI = CONFIG["ai"]
BROWSER = CONFIG["browser"]

PROFILES_DIR = Path(
    CONFIG.get("profiles_dir")
    or Path(os.environ.get("LOCALAPPDATA", str(Path.home()))) / "unmyeongwon-agent" / "chrome-profiles"
)

CHROME_CANDIDATES = [
    Path(os.environ.get("ProgramFiles", r"C:\Program Files")) / "Google/Chrome/Application/chrome.exe",
    Path(os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)")) / "Google/Chrome/Application/chrome.exe",
    Path(os.environ.get("LOCALAPPDATA", "")) / "Google/Chrome/Application/chrome.exe",
]


def chrome_path() -> Path | None:
    for p in CHROME_CANDIDATES:
        if p.exists():
            return p
    return None


def profile_dir(name: str) -> Path:
    if name not in PROFILES:
        raise ValueError(f"알 수 없는 프로필: {name}")
    return PROFILES_DIR / name


def youtube_key() -> str | None:
    return os.environ.get("YOUTUBE_API_KEY") or None


def anthropic_key_present() -> bool:
    return bool(os.environ.get("ANTHROPIC_API_KEY"))


for d in (DATA_DIR, SCREENSHOT_DIR, PROFILES_DIR):
    d.mkdir(parents=True, exist_ok=True)
