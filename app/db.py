"""SQLite 저장소 + 운영 규칙 강제 (영상당 1개 · 하루 상한 · 기록)."""
import json
import sqlite3
import threading
from datetime import datetime

from .config import DB_PATH, LIMITS, PROFILES, profile_dir

_lock = threading.Lock()

# 영상당 1개 / 하루 상한 계산에 포함되는 상태.
# unverified = 게시 버튼은 눌렀지만 확인을 못 한 경우 → 실제로 달렸을 수 있으므로 '게시됨'으로 취급.
COUNTED = ("running", "success", "unverified")


def _conn() -> sqlite3.Connection:
    c = sqlite3.connect(DB_PATH, check_same_thread=False)
    c.row_factory = sqlite3.Row
    return c


def now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def today() -> str:
    return datetime.now().strftime("%Y-%m-%d")


def init() -> None:
    with _conn() as c:
        c.executescript(
            """
            CREATE TABLE IF NOT EXISTS videos (
                video_id TEXT PRIMARY KEY,
                data TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS drafts (
                video_id TEXT PRIMARY KEY,
                data TEXT NOT NULL,
                created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS posts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                created_at TEXT NOT NULL,
                finished_at TEXT,
                video_id TEXT NOT NULL,
                video_title TEXT,
                profile TEXT NOT NULL,
                comment TEXT NOT NULL,
                status TEXT NOT NULL,
                reason TEXT,
                screenshot TEXT,
                comment_url TEXT
            );
            CREATE INDEX IF NOT EXISTS idx_posts_video ON posts(video_id);
            """
        )
        # 서버가 게시 도중 꺼졌다면 'running'으로 남은 기록을 정리
        c.execute(
            "UPDATE posts SET status='unverified', reason='서버가 게시 도중 종료됨 — 유튜브에서 직접 확인 필요', finished_at=? "
            "WHERE status='running'",
            (now(),),
        )


# ---------- 영상 / 초안 ----------

def save_video(v: dict) -> None:
    with _conn() as c:
        c.execute(
            "INSERT INTO videos(video_id, data, updated_at) VALUES(?,?,?) "
            "ON CONFLICT(video_id) DO UPDATE SET data=excluded.data, updated_at=excluded.updated_at",
            (v["video_id"], json.dumps(v, ensure_ascii=False), now()),
        )


def get_video(video_id: str) -> dict | None:
    with _conn() as c:
        r = c.execute("SELECT data FROM videos WHERE video_id=?", (video_id,)).fetchone()
    return json.loads(r["data"]) if r else None


def save_draft(video_id: str, d: dict) -> None:
    with _conn() as c:
        c.execute(
            "INSERT INTO drafts(video_id, data, created_at) VALUES(?,?,?) "
            "ON CONFLICT(video_id) DO UPDATE SET data=excluded.data, created_at=excluded.created_at",
            (video_id, json.dumps(d, ensure_ascii=False), now()),
        )


def get_draft(video_id: str) -> dict | None:
    with _conn() as c:
        r = c.execute("SELECT data FROM drafts WHERE video_id=?", (video_id,)).fetchone()
    return json.loads(r["data"]) if r else None


def recent_comments(limit: int = 8) -> list[str]:
    """최근 생성한 댓글들 — AI가 같은 문장 패턴을 반복하지 않도록 프롬프트에 넣습니다."""
    with _conn() as c:
        rows = c.execute("SELECT data FROM drafts ORDER BY created_at DESC LIMIT ?", (limit,)).fetchall()
    return [json.loads(r["data"])["comment"] for r in rows]


# ---------- 운영 규칙 ----------

class RuleError(Exception):
    pass


def video_post(video_id: str) -> dict | None:
    with _conn() as c:
        r = c.execute(
            f"SELECT * FROM posts WHERE video_id=? AND status IN {COUNTED} ORDER BY id DESC LIMIT 1",
            (video_id,),
        ).fetchone()
    return dict(r) if r else None


def posted_video_ids() -> set[str]:
    with _conn() as c:
        rows = c.execute(f"SELECT DISTINCT video_id FROM posts WHERE status IN {COUNTED}").fetchall()
    return {r["video_id"] for r in rows}


def today_count() -> int:
    with _conn() as c:
        r = c.execute(
            f"SELECT COUNT(*) n FROM posts WHERE status IN {COUNTED} AND substr(created_at,1,10)=?",
            (today(),),
        ).fetchone()
    return r["n"]


def profile_counts_today() -> dict[str, int]:
    with _conn() as c:
        rows = c.execute(
            f"SELECT profile, COUNT(*) n FROM posts WHERE status IN {COUNTED} AND substr(created_at,1,10)=? GROUP BY profile",
            (today(),),
        ).fetchall()
    counts = {p: 0 for p in PROFILES}
    counts.update({r["profile"]: r["n"] for r in rows})
    return counts


def auto_profile() -> str:
    """자동 배정: 준비된(로그인 폴더가 있는) 프로필 중 오늘 가장 적게 쓴 프로필, 같으면 앞 번호."""
    ready = [p for p in PROFILES if profile_dir(p).exists() and any(profile_dir(p).iterdir())]
    if not ready:
        raise RuleError("로그인된 크롬 프로필이 없어요. 오른쪽 아래 [로그인]으로 유튜브1을 먼저 준비하세요.")
    counts = profile_counts_today()
    return min(ready, key=lambda p: (counts[p], PROFILES.index(p)))


def reserve_post(video_id: str, video_title: str, profile: str, comment: str) -> int:
    """규칙 검사와 기록 생성을 한 번에(원자적으로) 처리합니다. 버튼을 연타해도 두 번 달리지 않습니다."""
    with _lock:
        existing = video_post(video_id)
        if existing:
            raise RuleError(
                f"이 영상에는 이미 {existing['profile']} 계정으로 댓글을 달았어요 ({existing['created_at'][5:16].replace('T', ' ')}). 영상당 1개만 가능합니다."
            )
        cap = LIMITS["daily_max_comments"]
        if today_count() >= cap:
            raise RuleError(f"오늘 게시 상한({cap}개)에 도달했어요. 내일 다시 시도하세요.")
        with _conn() as c:
            cur = c.execute(
                "INSERT INTO posts(created_at, video_id, video_title, profile, comment, status) VALUES(?,?,?,?,?, 'running')",
                (now(), video_id, video_title, profile, comment),
            )
            return cur.lastrowid


def finish_post(post_id: int, status: str, reason: str | None = None,
                screenshot: str | None = None, comment_url: str | None = None) -> None:
    with _conn() as c:
        c.execute(
            "UPDATE posts SET status=?, reason=?, screenshot=?, comment_url=?, finished_at=? WHERE id=?",
            (status, reason, screenshot, comment_url, now(), post_id),
        )


def history(limit: int = 200) -> list[dict]:
    with _conn() as c:
        rows = c.execute("SELECT * FROM posts ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
    return [dict(r) for r in rows]
