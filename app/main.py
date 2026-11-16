"""대시보드 서버. 실행: run.bat (또는 python -m uvicorn app.main:app --port 8000)"""
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import ai, db, poster, youtube
from .config import (COMMENT, LIMITS, PROFILES, RISING, SCREENSHOT_DIR, SEARCH,
                     anthropic_key_present, profile_dir, youtube_key)

app = FastAPI(title="운명원 댓글 에이전트")
STATIC = Path(__file__).parent / "static"
app.mount("/static", StaticFiles(directory=STATIC), name="static")
app.mount("/screenshots", StaticFiles(directory=SCREENSHOT_DIR), name="screenshots")
db.init()


def _err(e: Exception, code: int = 400):
    raise HTTPException(status_code=code, detail=str(e))


def _with_flags(videos: list[dict]) -> list[dict]:
    posted = db.posted_video_ids()
    out = []
    for v in videos:
        db.save_video(v)
        light = {k: v[k] for k in ("video_id", "title", "channel", "published_at", "thumbnail",
                                   "views", "views_per_hour", "comment_count", "url")}
        light["rising"] = youtube.is_rising(v)
        light["posted"] = v["video_id"] in posted
        light["has_draft"] = db.get_draft(v["video_id"]) is not None
        out.append(light)
    return out


@app.get("/")
def index():
    return FileResponse(STATIC / "index.html")


@app.get("/api/status")
def status():
    profiles = []
    counts = db.profile_counts_today()
    for p in PROFILES:
        d = profile_dir(p)
        profiles.append({"name": p, "ready": d.exists() and any(d.iterdir()), "today": counts[p]})
    return {
        "youtube_key": bool(youtube_key()),
        "anthropic_key": anthropic_key_present(),
        "profiles": profiles,
        "today": db.today_count(),
        "daily_max": LIMITS["daily_max_comments"],
        "min_chars": COMMENT["min_chars"],
        "max_chars": COMMENT["max_chars"],
        "brand": COMMENT["brand"],
        "default_keywords": SEARCH["keywords"],
        "rising_keywords": RISING["keywords"],
        "rising_rule": {k: RISING[k] for k in ("min_views", "max_views", "min_views_per_hour")},
    }


# ---------- ① 영상 불러오기 ----------

@app.get("/api/videos")
async def videos(keyword: str | None = None, rising: bool = True):
    try:
        vs = await run_in_threadpool(youtube.search_videos, keyword or None, rising)
    except youtube.YouTubeError as e:
        _err(e)
    return _with_flags(vs)


class LinkIn(BaseModel):
    url: str


@app.post("/api/videos/link")
async def add_link(body: LinkIn):
    vid = youtube.parse_video_id(body.url)
    if not vid:
        _err(ValueError("유튜브 영상 링크를 알아보지 못했어요. 예: https://www.youtube.com/watch?v=..."))
    try:
        vs = await run_in_threadpool(youtube.video_details, [vid])
    except youtube.YouTubeError as e:
        _err(e)
    if not vs:
        _err(ValueError("영상을 찾지 못했어요 (비공개이거나 삭제된 영상일 수 있음)"), 404)
    return _with_flags(vs)[0]


# ---------- ② 영상 선택 → 고민 한 줄 + 댓글 초안 ----------

class DraftIn(BaseModel):
    video_id: str
    regenerate: bool = False


@app.post("/api/draft")
async def draft(body: DraftIn):
    video = db.get_video(body.video_id)
    if not video:
        _err(ValueError("먼저 영상 목록을 불러오세요"), 404)
    if not body.regenerate:
        d = db.get_draft(body.video_id)
        if d and "top_comments" in d:  # 예전 형식(후보·인기 댓글 없음)의 초안은 새로 생성
            return {**d, "cached": True, "posted": db.video_post(body.video_id)}
    if not anthropic_key_present():
        _err(ValueError("ANTHROPIC_API_KEY가 없어요. .env 파일에 키를 넣고 서버를 다시 켜 주세요."))
    try:
        d = await run_in_threadpool(ai.generate, video)
    except Exception as e:
        _err(e, 500)
    return {**d, "cached": False, "posted": db.video_post(body.video_id)}


class CheckIn(BaseModel):
    comment: str


@app.post("/api/check")
def check(body: CheckIn):
    """사람이 초안을 고쳤을 때도 같은 규칙으로 검사합니다."""
    c = body.comment.strip()
    return {"chars": len(c), "problems": ai.check(c)}


# ---------- ③ 댓글 달기 ----------

class PostIn(BaseModel):
    video_id: str
    comment: str
    profile: str = "auto"


@app.post("/api/post")
def post(body: PostIn):
    video = db.get_video(body.video_id)
    if not video:
        _err(ValueError("영상 정보를 찾지 못했어요. 목록을 다시 불러오세요."), 404)
    comment = body.comment.strip()
    problems = ai.check(comment)
    if problems:
        _err(ValueError("댓글이 규칙에 맞지 않아요: " + ", ".join(problems)))
    try:
        profile = db.auto_profile() if body.profile == "auto" else body.profile
        if profile not in PROFILES:
            raise db.RuleError(f"알 수 없는 프로필: {profile}")
        d = profile_dir(profile)
        if not (d.exists() and any(d.iterdir())):
            raise db.RuleError(f"'{profile}' 프로필이 아직 준비되지 않았어요. [로그인] 버튼으로 먼저 로그인하세요.")
        post_id = db.reserve_post(video["video_id"], video["title"], profile, comment)
    except db.RuleError as e:
        _err(e, 409)
    job_id = poster.start(post_id, video, profile, comment)
    return {"job_id": job_id, "profile": profile, "post_id": post_id}


@app.get("/api/jobs/{job_id}")
def job(job_id: str):
    j = poster.jobs.get(job_id)
    if not j:
        _err(KeyError("작업을 찾지 못했어요"), 404)
    return j


@app.post("/api/profiles/{name}/login")
def login(name: str):
    if name not in PROFILES:
        _err(ValueError("알 수 없는 프로필"), 404)
    try:
        poster.open_login_window(name)
    except Exception as e:
        _err(e, 500)
    return {"ok": True}


@app.get("/api/history")
def history():
    return db.history()
