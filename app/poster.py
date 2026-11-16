"""크롬 프로필을 열어 댓글창 → 한 글자씩 타이핑 → 게시 → 내 댓글 확인.

- Playwright launch_persistent_context + 프로필 폴더(--user-data-dir) 방식.
  평소 쓰는 기본 크롬 프로필은 건드리지 않습니다.
- 진행 상태는 jobs[job_id]에 기록되고, 대시보드가 이를 주기적으로 읽어 표시합니다.
- 한 번에 한 작업만 실행합니다 (같은 프로필 폴더를 두 창이 동시에 열 수 없기 때문).
"""
import re
import subprocess
import threading
import time
import traceback
import uuid
from urllib.parse import parse_qs, urlparse

from playwright.sync_api import TimeoutError as PWTimeout
from playwright.sync_api import sync_playwright

from . import db
from .config import BROWSER, COMMENT, SCREENSHOT_DIR, chrome_path, profile_dir

STEPS = {
    "queued": "대기 중",
    "opening": "프로필 여는 중",
    "loading": "영상 여는 중",
    "comment_box": "댓글 창 여는 중",
    "typing": "입력 중",
    "posting": "게시 중",
    "verifying": "내 댓글 확인 중",
    "done": "게시 완료",
    "failed": "실패",
}

jobs: dict[str, dict] = {}
_run_lock = threading.Lock()


class PostError(Exception):
    pass


def _norm(s: str) -> str:
    return re.sub(r"\s+", "", s or "")


def _set(job: dict, step: str, msg: str | None = None, **kw) -> None:
    job.update(step=step, label=STEPS[step], message=msg or STEPS[step], updated=time.time(), **kw)
    job.setdefault("timeline", []).append({"step": step, "label": STEPS[step], "t": db.now()[11:]})


def start(post_id: int, video: dict, profile: str, comment: str) -> str:
    job_id = uuid.uuid4().hex[:10]
    job = {"id": job_id, "post_id": post_id, "video_id": video["video_id"], "profile": profile,
           "comment": comment, "typed": 0}
    jobs[job_id] = job
    _set(job, "queued")
    threading.Thread(target=_run, args=(job, video, profile, comment), daemon=True).start()
    return job_id


def _run(job: dict, video: dict, profile: str, comment: str) -> None:
    with _run_lock:
        shot = str(SCREENSHOT_DIR / f"post{job['post_id']}.png")
        submitted = False
        page = None
        try:
            with sync_playwright() as pw:
                _set(job, "opening", f"크롬 프로필 '{profile}' 여는 중")
                try:
                    ctx = pw.chromium.launch_persistent_context(
                        user_data_dir=str(profile_dir(profile)),
                        channel="chrome",
                        headless=False,
                        chromium_sandbox=True,  # 기본값(--no-sandbox)이면 크롬 상단에 경고 줄이 뜸
                        locale="ko-KR",
                        viewport=None,
                        args=["--lang=ko-KR", "--start-maximized", "--autoplay-policy=user-gesture-required"],
                    )
                except Exception as e:
                    if "ProcessSingleton" in str(e) or "already" in str(e).lower() or "exitCode=21" in str(e):
                        raise PostError(f"'{profile}' 크롬 창이 이미 열려 있어요. 그 창을 닫고 다시 시도하세요.")
                    raise PostError(f"크롬을 열지 못했어요: {e}")
                try:
                    page = ctx.pages[0] if ctx.pages else ctx.new_page()
                    page.set_default_timeout(20000)
                    submitted = _post(page, job, video, comment)
                    url = _verify(page, job, video, comment)
                    page.screenshot(path=shot)
                    _set(job, "done", "게시 완료 · 내 댓글이 보이는 것을 확인했어요", comment_url=url,
                         screenshot=f"/screenshots/post{job['post_id']}.png")
                    db.finish_post(job["post_id"], "success", screenshot=job["screenshot"], comment_url=url)
                    time.sleep(BROWSER["keep_open_seconds"])
                finally:
                    try:
                        ctx.close()
                    except Exception:
                        pass
        except Exception as e:
            reason = str(e) if isinstance(e, PostError) else f"예상하지 못한 오류: {e}"
            if not isinstance(e, PostError):
                traceback.print_exc()
            shot_url = f"/screenshots/post{job['post_id']}.png"
            try:
                if page is not None and not page.is_closed():
                    page.screenshot(path=shot)
                else:
                    shot_url = None
            except Exception:
                shot_url = None
            # 게시 버튼을 누른 뒤 실패했다면 실제로 달렸을 수 있음 → unverified 로 기록(영상당 1개 규칙에 포함)
            status = "unverified" if submitted else "failed"
            if submitted:
                reason = "게시 버튼은 눌렀지만 확인하지 못했어요 — " + reason
            _set(job, "failed", reason, screenshot=shot_url)
            db.finish_post(job["post_id"], status, reason=reason, screenshot=shot_url)


def _post(page, job: dict, video: dict, comment: str) -> bool:
    _set(job, "loading", "유튜브 영상 여는 중 (한국어 화면)")
    page.goto(f"https://www.youtube.com/watch?v={video['video_id']}&hl=ko&persist_hl=1", wait_until="domcontentloaded")
    page.wait_for_selector("ytd-watch-flexy", timeout=30000)
    time.sleep(1.5)
    page.evaluate("() => { const v = document.querySelector('video'); if (v) { v.muted = true; v.pause(); } }")

    # 로그인 여부 확인 (상단 '로그인' 버튼이 보이면 로그아웃 상태)
    if page.locator('ytd-masthead a[href*="accounts.google.com/ServiceLogin"]').count() > 0:
        raise PostError(f"'{job['profile']}' 프로필이 유튜브에 로그인되어 있지 않아요. 대시보드의 [로그인] 버튼으로 먼저 로그인하세요.")

    _set(job, "comment_box", "댓글 창으로 이동하는 중")
    placeholder = page.locator("ytd-comments #simplebox-placeholder").first
    for _ in range(25):  # 댓글 영역은 스크롤해야 불러와짐
        if placeholder.count() and placeholder.is_visible():
            break
        if page.locator("ytd-comments ytd-message-renderer").count():
            msg = page.locator("ytd-comments ytd-message-renderer").first.inner_text().strip()
            raise PostError(f"이 영상은 댓글을 달 수 없어요: {msg[:60]}")
        page.mouse.wheel(0, 500)
        time.sleep(0.6)
    else:
        raise PostError("댓글 창을 찾지 못했어요 (댓글이 꺼져 있거나 화면 구조가 바뀌었을 수 있음)")

    placeholder.scroll_into_view_if_needed()
    time.sleep(0.5)
    placeholder.click()
    editor = page.locator("ytd-comments #contenteditable-root").first
    try:
        editor.wait_for(state="visible", timeout=10000)
    except PWTimeout:
        if "accounts.google.com" in page.url or page.locator("text=로그인").count():
            raise PostError("댓글을 달려면 로그인이 필요하다고 나와요. 프로필 로그인을 확인하세요.")
        raise PostError("댓글 입력칸이 열리지 않았어요")
    editor.click()

    _set(job, "typing", "한 글자씩 입력 중")
    delay = COMMENT["typing_delay_ms"]
    for i, ch in enumerate(comment, 1):
        page.keyboard.type(ch, delay=0)
        job["typed"] = i
        time.sleep(delay / 1000)
    time.sleep(0.6)

    typed = editor.inner_text()
    if _norm(typed) != _norm(comment):
        raise PostError("입력된 내용이 초안과 달라요 (입력 중 포커스를 잃었을 수 있음)")

    _set(job, "posting", "게시 버튼 누르는 중")
    submit = page.locator("ytd-comments ytd-commentbox #submit-button button, ytd-comments ytd-commentbox #submit-button").first
    submit.wait_for(state="visible")
    submit.click()
    return True


def _find_my_comment(page, comment: str, wait_s: float = 15):
    key = _norm(comment)[:25]
    end = time.time() + wait_s
    while time.time() < end:
        threads = page.locator("ytd-comments ytd-comment-thread-renderer")
        n = min(threads.count(), 10)
        for i in range(n):
            th = threads.nth(i)
            try:
                text = th.locator("#content-text").first.inner_text(timeout=1000)
            except Exception:
                continue
            if key in _norm(text):
                return th
        time.sleep(0.8)
    return None


def _verify(page, job: dict, video: dict, comment: str) -> str:
    _set(job, "verifying", "방금 단 댓글이 목록에 보이는지 확인 중")
    th = _find_my_comment(page, comment)
    if th is None:
        raise PostError("게시 후 댓글 목록에서 내 댓글을 찾지 못했어요 (유튜브가 보류했을 수 있음)")
    th.scroll_into_view_if_needed()

    # 댓글 고유 링크(lc=댓글ID) 얻기 → 그 링크로 다시 열어 '강조된 댓글'로 보이는지 한 번 더 확인
    url = video["url"]
    try:
        href = th.locator("#published-time-text a, a#published-time-text, #header-author a[href*='lc=']").first.get_attribute("href", timeout=3000)
        lc = parse_qs(urlparse(href or "").query).get("lc", [None])[0]
        if lc:
            url = f"https://www.youtube.com/watch?v={video['video_id']}&lc={lc}"
    except Exception:
        pass

    if "lc=" in url:
        time.sleep(1.5)
        page.goto(url + "&hl=ko", wait_until="domcontentloaded")
        page.wait_for_selector("ytd-watch-flexy", timeout=30000)
        time.sleep(1.5)
        page.evaluate("() => { const v = document.querySelector('video'); if (v) { v.muted = true; v.pause(); } }")
        for _ in range(20):
            if page.locator("ytd-comments ytd-comment-thread-renderer").count():
                break
            page.mouse.wheel(0, 500)
            time.sleep(0.6)
        th2 = _find_my_comment(page, comment, wait_s=10)
        if th2 is not None:
            th2.scroll_into_view_if_needed()
            try:
                th2.evaluate("el => { el.style.outline = '3px solid #c9a227'; el.style.borderRadius = '8px'; }")
            except Exception:
                pass
    return url


# ---------- 프로필 로그인 ----------

def open_login_window(profile: str) -> None:
    """일반 크롬 창(자동 제어 아님)으로 프로필 폴더를 열어 구글 로그인을 하게 합니다.
    자동 제어 중인 크롬에서는 구글이 로그인을 막는 경우가 많아서 로그인만 이렇게 따로 합니다."""
    exe = chrome_path()
    if not exe:
        raise PostError("크롬 실행 파일을 찾지 못했어요. 구글 크롬을 설치해 주세요.")
    d = profile_dir(profile)
    d.mkdir(parents=True, exist_ok=True)
    subprocess.Popen([
        str(exe), f"--user-data-dir={d}", "--lang=ko-KR", "--no-first-run", "--no-default-browser-check",
        "https://accounts.google.com/ServiceLogin?hl=ko&continue=https://www.youtube.com/?hl=ko",
    ])
