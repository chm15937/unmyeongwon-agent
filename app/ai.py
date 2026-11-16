"""AI 댓글 생성: 고민 정리 → 장면 선택 → 시청자 페르소나 → 댓글, 그리고 코드로 규칙 검사.

모델이 규칙을 지키리라 믿지 않고, 생성 후 코드로 한 번 더 검사합니다.
위반이 있으면 위반 내용을 알려주며 다시 생성합니다 (최대 ai.max_attempts 회).
"""
import re

import anthropic
from pydantic import BaseModel, Field

from . import db, youtube
from .config import AI, COMMENT

BRAND = COMMENT["brand"]
MIN_C, MAX_C = COMMENT["min_chars"], COMMENT["max_chars"]


class Draft(BaseModel):
    worry: str = Field(description="영상이 다루는 연애 고민을 시청자 입장의 한 문장으로 (60자 이내)")
    scene: str = Field(description="댓글에서 짚을 영상 속 구체적인 장면이나 말 (가능하면 [mm:ss] 포함, 인용은 짧게)")
    persona: str = Field(description="이 댓글을 쓰는 시청자 페르소나 한 줄 (연애 상황)")
    candidates: list[str] = Field(description=f"서로 다른 말투·구조의 댓글 후보 3개 (각 {MIN_C}~{MAX_C}자)")
    best: int = Field(description="candidates 중 가장 실제 사람이 쓴 것 같은 후보의 번호 (0, 1, 2)")


SYSTEM = f"""너는 한국 유튜브 연애 콘텐츠를 매일 보고 댓글도 자주 다는 20~30대 시청자다.
주어진 영상 자료(제목·설명·자막·인기 댓글)를 읽고, 아래 순서대로 결과를 채운다.

1) worry: 이 영상이 다루는 연애 고민을 시청자가 속으로 하는 질문처럼 한 줄로 정리한다.
   예) "연락이 뜸해진 썸남, 마음이 식은 걸까 바쁜 걸까?"
2) scene: 댓글에서 짚을 영상 속 구체적인 장면이나 출연자의 말 하나를 고른다.
   자막이 있으면 실제 자막에 나온 말에서 고르고, 없으면 제목·설명의 구체적인 내용에서 고른다. 지어내지 않는다.
3) persona: 이 영상을 끝까지 본 시청자 한 명의 연애 상황 (예: 썸만 6개월째 / 헤어진 지 한 달 / 소개팅만 계속함)
4) candidates: 그 시청자가 폰으로 툭 달 법한 댓글 후보 3개. 셋은 첫마디·구조·말끝이 서로 달라야 한다.
5) best: 셋 중 광고 티가 가장 안 나고 진짜 사람이 쓴 것 같은 후보 번호.

[사람이 쓴 댓글처럼 — 가장 중요]
- 다듬어진 글이 아니라 생각나는 대로 쓴 한두 마디. "장면 요약 → 내 얘기 → 결론" 같은 깔끔한 3단 구성은 AI 티가 난다.
- 장면을 설명하지 말고 장면에 '반응'한다.
  나쁜 예: "비 오는 날 캠핑카로 커피 마시러 가는 장면을 보니 설레네요"
  좋은 예: "아니 비 온다고 캠핑카 끌고 나가는 거 뭔데ㅋㅋㅋ"
- 말끝을 다양하게: ~함, ~임, ~듯, ~네, ~다, ~나, ㅠ, ?, .. 처럼. 마침표는 거의 안 쓴다. 반말 위주, 가끔 존댓말 섞여도 됨.
- 구어 표현은 자연스럽게: 근데, 걍, 진짜, 아니, 암튼, 나만 그럼?, 개(강조), 넘. 단, 억지로 많이 넣지 않는다.
- 내 얘기는 구체적인 한 조각만 ("전남친이 딱 저랬음", "어제 답장 3시간 만에 옴"). 나이·직업 자기소개 하지 않는다.
- 이모지 쓰지 않는다. 타임스탬프는 쓰고 싶으면 3:25 처럼 대괄호 없이, 후보 셋 중 최대 하나만.
- AI 같은 상투어 금지: 설렘 폭발, 공감 백배, 너무 와닿아요, 찐, 심쿵, 몽글, 힐링, 응원합니다, 감사합니다, ~하는 부분 너무 좋네요, 저 같은 분 계신가요.
- 길이: 공백 포함 {MIN_C}~{MAX_C}자. 50~90자 정도가 가장 자연스럽다.

[{BRAND} 유도]
- '{BRAND}'은 후보마다 정확히 한 번, 지나가듯 자연스럽게. 항상 맨 끝 같은 고정 위치에 두지 않는다.
- {BRAND}은 '연애사주 잘 본다고 소문난 곳'이다. 소문으로 들었거나 궁금한 곳으로만 말한다.
  ("{BRAND} 연애사주 잘 본다던데", "{BRAND}에서 궁합 봐준다던데", "{BRAND} 얘기 많이 하던데")
- 영상의 고민과 사주가 답해 줄 수 있는 것을 이어서 읽는 사람도 "나도 볼까?" 싶게 한다:
  언제 인연이 오는지(시기), 이 사람과 맞는지(궁합), 다시 만날 수 있는지(재회운), 왜 늘 같은 연애를 반복하는지(연애 성향).

[구조 참고용 예시 — 문장을 그대로 쓰지 말 것]
- (남자심리 분석 영상) 근데 진짜 남자심리는 알다가도 모르겠다...ㅠㅠ 맨날 차이기만 했던 1인. 심리 알기는 개뿔 걍 인연이면 만나는 듯. {BRAND} 연애사주 잘 본다던데 나도 언제 짝 만나는지 보고 싶다
- (썸 연락 텀 영상) 답장 텀 길어지면 이미 끝난 거라는 말에 폰 내려놨다ㅋㅋ {BRAND}에서 궁합 잘 봐준다던데 걍 이 사람이랑 되는지만 알고 싶음
- (재회 영상) 헤어지고 3주쯤 사진 올리라는 거 지금 내 얘기라 뜨끔.. 재회운도 {BRAND}에서 본다던데 연락하기 전에 그거부터 봐야 하나

[금지]
- 링크, URL, 해시태그(#), 전화번호, 가격·할인 언급.
- {BRAND}을 직접 이용해 본 것처럼 지어낸 후기: "봤는데", "받아봤는데", "다녀왔는데", "맞더라", "소름", "덕분에" 등 이용 경험 표현.
  지인의 구체적인 이용 경험을 지어내는 것도 금지.
- 광고 말투: "강추", "꼭 가보세요", "최고예요", "추천합니다", 업체 칭찬 나열.
- 다른 시청자의 인기 댓글 문장을 베끼는 것.
- 최근에 쓴 댓글(아래 제공)과 같은 첫마디, 같은 문장 구조, 같은 끝맺음.
"""

URL_RE = re.compile(r"(https?://|www\.|\.com\b|\.co\.kr\b|\.kr/|\.net\b|bit\.ly|youtu\.be)", re.I)
FAKE_REVIEW_RE = re.compile(
    rf"{BRAND}[^.!?\n]{{0,25}}?(봤는데|봤더니|봤었|받아봤|받았는데|받았더니|다녀왔|갔다왔|갔었|가봤|봐봤|상담받|맞더라|맞았|맞췄|소름|덕분에|후기)"
    rf"|(봤던|받았던|다녀온|가본|상담받은)\s*{BRAND}"
)
AD_RE = re.compile(r"(강추|꼭 가보세요|가보세요|추천합니다|추천해요|최고예요|할인|이벤트|문의)")
CLICHE_RE = re.compile(r"(설렘 ?폭발|공감 ?백배|와닿아요|심쿵|몽글|힐링|응원합니다|감사합니다|저 같은 분)")


def check(comment: str) -> list[str]:
    """규칙 위반 목록. 빈 리스트면 통과."""
    problems = []
    n = len(comment)
    if not (MIN_C <= n <= MAX_C):
        problems.append(f"길이 {n}자 — {MIN_C}~{MAX_C}자여야 함")
    cnt = comment.count(BRAND)
    if cnt == 0:
        problems.append(f"'{BRAND}' 언급이 없음")
    elif cnt > 1:
        problems.append(f"'{BRAND}'을 {cnt}번 언급 — 한 번만")
    if URL_RE.search(comment):
        problems.append("링크/URL 포함")
    if "#" in comment:
        problems.append("해시태그 포함")
    if FAKE_REVIEW_RE.search(comment):
        problems.append(f"{BRAND}을 직접 이용한 것 같은 후기 표현")
    if AD_RE.search(comment):
        problems.append("광고 말투 표현")
    if CLICHE_RE.search(comment):
        problems.append("AI 같은 상투어")
    return problems


def _client() -> anthropic.Anthropic:
    return anthropic.Anthropic()


def _material(video: dict, subs: str | None, comments: list[str]) -> str:
    parts = [
        f"제목: {video['title']}",
        f"채널: {video['channel']}",
        f"설명:\n{video.get('description') or '(없음)'}",
    ]
    if subs:
        parts.append(f"자막:\n{subs}")
    else:
        parts.append("자막: (없음 — 제목과 설명만으로 판단할 것)")
    parts.append("인기 댓글:\n" + ("\n".join(comments) if comments else "(없음)"))
    return "\n\n".join(parts)


def generate(video: dict) -> dict:
    """영상 하나에 대한 고민 한 줄 + 댓글 초안. 결과는 DB에 저장됩니다."""
    subs = youtube.transcript(video["video_id"])
    comments = youtube.top_comments(video["video_id"])
    material = _material(video, subs, comments)
    recent = db.recent_comments()
    recent_block = "\n".join(f"- {c}" for c in recent) if recent else "(아직 없음)"

    client = _client()
    feedback = ""
    last: Draft | None = None
    chosen = ""
    problems: list[str] = []
    attempts = 0
    for attempts in range(1, AI["max_attempts"] + 1):
        user = (f"<영상 자료>\n{material}\n</영상 자료>\n\n"
                f"<최근에 쓴 댓글 — 겹치지 않게>\n{recent_block}\n</최근에 쓴 댓글>")
        if feedback:
            user += f"\n\n{feedback}"
        resp = client.beta.messages.parse(
            model=AI["model"],
            max_tokens=16000,
            betas=["server-side-fallback-2026-07-01"],
            fallbacks="default",
            output_config={"effort": AI["effort"]},
            system=SYSTEM,
            messages=[{"role": "user", "content": user}],
            output_format=Draft,
        )
        if resp.stop_reason == "refusal" or resp.parsed_output is None:
            raise RuntimeError("AI가 이 영상에 대한 댓글 생성을 거절했어요. 다른 영상을 골라 주세요.")
        last = resp.parsed_output
        cands = [c.strip().strip('"“”') for c in last.candidates if c.strip()]
        best = last.best if 0 <= last.best < len(cands) else 0
        # AI가 고른 후보부터 코드 규칙 검사 → 통과하는 첫 후보를 채택
        order = [best] + [i for i in range(len(cands)) if i != best]
        checked = [(cands[i], check(cands[i])) for i in order]
        passing = [c for c, p in checked if not p]
        if passing:
            chosen, problems = passing[0], []
            last.candidates = [c for c, _ in checked]
            break
        chosen, problems = checked[0] if checked else ("", ["후보가 비어 있음"])
        feedback = "직전 후보들은 규칙 위반이 있었다:\n" + "\n".join(
            f'- "{c}" → {", ".join(p)}' for c, p in checked
        ) + "\n위반을 고쳐서 후보 3개를 새로 써라."

    result = {
        "worry": last.worry,
        "scene": last.scene,
        "persona": last.persona,
        "comment": chosen,
        "alternatives": [c for c in last.candidates if c != chosen and not check(c)],
        "chars": len(chosen),
        "problems": problems,
        "attempts": attempts,
        "had_transcript": bool(subs),
        "top_comment_count": len(comments),
        "top_comments": comments[:3],
        "model": AI["model"],
    }
    db.save_draft(video["video_id"], result)
    return result
