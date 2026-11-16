"""유튜브 데이터: 영상 검색(Data API v3), 영상 정보, 인기 댓글, 자막."""
import re
import time
from datetime import datetime, timedelta, timezone

import httpx

from .config import RISING, SEARCH, youtube_key

API = "https://www.googleapis.com/youtube/v3"


class YouTubeError(Exception):
    pass


def _get(path: str, **params) -> dict:
    key = youtube_key()
    if not key:
        raise YouTubeError("YOUTUBE_API_KEY가 없어요. .env 파일에 키를 넣고 서버를 다시 켜 주세요.")
    params["key"] = key
    r = httpx.get(f"{API}/{path}", params=params, timeout=20)
    if r.status_code != 200:
        try:
            err = r.json()["error"]
            reasons = {e.get("reason") for e in err.get("errors", [])}
        except Exception:
            err, reasons = {"message": r.text[:200]}, set()
        if "commentsDisabled" in reasons:
            raise YouTubeError("commentsDisabled")
        if "quotaExceeded" in reasons:
            raise YouTubeError("YouTube API 오늘 할당량을 다 썼어요. 내일 다시 시도하세요.")
        raise YouTubeError(f"YouTube API 오류: {err.get('message')}")
    return r.json()


VIDEO_ID_RE = re.compile(r"(?:v=|youtu\.be/|shorts/|embed/|live/)([A-Za-z0-9_-]{11})")


def parse_video_id(text: str) -> str | None:
    text = text.strip()
    if re.fullmatch(r"[A-Za-z0-9_-]{11}", text):
        return text
    m = VIDEO_ID_RE.search(text)
    return m.group(1) if m else None


def _iso_to_dt(s: str) -> datetime:
    return datetime.fromisoformat(s.replace("Z", "+00:00"))


def _duration_seconds(iso: str) -> int:
    m = re.fullmatch(r"P(?:(\d+)D)?T?(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?", iso or "")
    if not m:
        return 0
    d, h, mi, s = (int(x or 0) for x in m.groups())
    return d * 86400 + h * 3600 + mi * 60 + s


def video_details(ids: list[str]) -> list[dict]:
    """videos.list → 대시보드에서 쓰는 형태로 정리. 입력 순서를 유지합니다."""
    out: dict[str, dict] = {}
    for i in range(0, len(ids), 50):
        data = _get("videos", part="snippet,statistics,contentDetails",
                    id=",".join(ids[i:i + 50]), hl="ko")
        now = datetime.now(timezone.utc)
        for it in data.get("items", []):
            sn, st = it["snippet"], it.get("statistics", {})
            published = _iso_to_dt(sn["publishedAt"])
            hours = max((now - published).total_seconds() / 3600, 0.1)
            views = int(st.get("viewCount", 0))
            thumbs = sn.get("thumbnails", {})
            thumb = (thumbs.get("medium") or thumbs.get("high") or thumbs.get("default") or {}).get("url", "")
            out[it["id"]] = {
                "video_id": it["id"],
                "title": sn["title"],
                "channel": sn["channelTitle"],
                "published_at": sn["publishedAt"],
                "thumbnail": thumb,
                "description": sn.get("description", ""),
                "tags": sn.get("tags", []),
                "views": views,
                "comment_count": int(st["commentCount"]) if "commentCount" in st else None,
                "views_per_hour": round(views / hours, 1),
                "duration": _duration_seconds(it.get("contentDetails", {}).get("duration", "")),
                "url": f"https://www.youtube.com/watch?v={it['id']}",
            }
    return [out[i] for i in ids if i in out]


def _search_ids(q: str, published_after: datetime, max_results: int, order: str = "date") -> list[str]:
    data = _get(
        "search", part="id", type="video", q=q, order=order,
        regionCode="KR", relevanceLanguage="ko", maxResults=min(max_results, 50),
        publishedAfter=published_after.strftime("%Y-%m-%dT%H:%M:%SZ"),
    )
    return [it["id"]["videoId"] for it in data.get("items", []) if it.get("id", {}).get("videoId")]


def _usable(v: dict) -> bool:
    # 댓글이 꺼진 영상(comment_count 없음)과 60초 이하 쇼츠는 제외
    return v["comment_count"] is not None and v["duration"] > 60


def is_rising(v: dict) -> bool:
    return (RISING["min_views"] <= v["views"] <= RISING["max_views"]
            and v["views_per_hour"] >= RISING["min_views_per_hour"])


_cache: dict[tuple, tuple[float, list[dict]]] = {}


def search_videos(keyword: str | None = None, rising: bool = True) -> list[dict]:
    """[영상 불러오기]. 결과는 최신순.

    rising=True (기본): 최근 36시간 영상 중 조회수 1,000~5,000 · 시간당 150회 이상만.
      최신순(date)만 보면 막 올라온 조회수 0~100짜리만 잡히므로, 관련도순(relevance)도 함께 모아 후보를 넓힙니다.
    rising=False: 최근 N일 키워드 최신 영상 (조건 없음).
    """
    key = (keyword or "", rising)
    hit = _cache.get(key)
    if hit and time.time() - hit[0] < RISING["cache_minutes"] * 60:
        return hit[1]

    ids: list[str] = []

    def add(found: list[str]) -> None:
        for vid in found:
            if vid not in ids:
                ids.append(vid)

    if rising:
        after = datetime.now(timezone.utc) - timedelta(hours=RISING["lookback_hours"])
        for kw in ([keyword] if keyword else RISING["keywords"]):
            add(_search_ids(kw, after, 50, "date"))
            add(_search_ids(kw, after, 50, "relevance"))
        videos = [v for v in video_details(ids) if _usable(v) and is_rising(v)]
    else:
        after = datetime.now(timezone.utc) - timedelta(days=SEARCH["days"])
        keywords = [keyword] if keyword else SEARCH["keywords"]
        per = max(10, SEARCH["max_results"] // len(keywords) + 5)
        for kw in keywords:
            add(_search_ids(kw, after, per, "date"))
        videos = [v for v in video_details(ids) if _usable(v)]

    videos.sort(key=lambda v: v["published_at"], reverse=True)
    videos = videos[: SEARCH["max_results"]]
    _cache[key] = (time.time(), videos)
    return videos


def top_comments(video_id: str, n: int = 20) -> list[str]:
    try:
        data = _get("commentThreads", part="snippet", videoId=video_id, order="relevance",
                    maxResults=n, textFormat="plainText")
    except YouTubeError as e:
        if str(e) == "commentsDisabled":
            return []
        raise
    out = []
    for it in data.get("items", []):
        c = it["snippet"]["topLevelComment"]["snippet"]
        out.append(f"(좋아요 {c.get('likeCount', 0)}) {c['textDisplay'].strip()}")
    return out


def transcript(video_id: str) -> str | None:
    """자막(한국어 우선, 자동 생성 포함). 없거나 막히면 None → 제목·설명으로 대체."""
    try:
        from youtube_transcript_api import YouTubeTranscriptApi

        fetched = YouTubeTranscriptApi().fetch(video_id, languages=["ko", "en"])
        lines = []
        for s in fetched:
            t = int(s.start)
            lines.append(f"[{t // 60:02d}:{t % 60:02d}] {s.text}")
        return "\n".join(lines) or None
    except Exception:
        return None
