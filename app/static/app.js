const $ = (s) => document.querySelector(s);
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

const state = { status: null, videos: [], selected: null, draft: null, problems: [], profile: "auto", job: null, posting: false };

async function api(path, opts = {}) {
  const r = await fetch(path, { headers: { "Content-Type": "application/json" }, ...opts });
  const data = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(data.detail || `요청 실패 (${r.status})`);
  return data;
}

function ago(iso) {
  const d = new Date(iso), s = (Date.now() - d) / 1000;
  const rel = s < 3600 ? `${Math.max(1, Math.floor(s / 60))}분 전` : s < 86400 ? `${Math.floor(s / 3600)}시간 전` : `${Math.floor(s / 86400)}일 전`;
  return `${rel} · ${d.getFullYear()}.${d.getMonth() + 1}.${d.getDate()}`;
}
const fmt = (n) => Number(n).toLocaleString("ko-KR");

function busy(btn, on) { btn.disabled = on; btn.classList.toggle("loading", on); }

/* ---------- 상단 상태 ---------- */
async function loadStatus() {
  const s = (state.status = await api("/api/status"));
  $("#chips").innerHTML = `
    <span class="chip ${s.youtube_key ? "ok" : "no"}">YouTube 키</span>
    <span class="chip ${s.anthropic_key ? "ok" : "no"}">AI 키</span>
    <span class="chip count">오늘 게시 ${s.today} / ${s.daily_max}</span>`;
  const missing = [];
  if (!s.youtube_key) missing.push("<code>YOUTUBE_API_KEY</code>");
  if (!s.anthropic_key) missing.push("<code>ANTHROPIC_API_KEY</code>");
  const n = $("#setupNotice");
  n.hidden = !missing.length;
  n.innerHTML = missing.length ? `처음 설정이 필요해요: 프로젝트 폴더의 <code>.env</code> 파일에 ${missing.join(", ")}를 넣고 서버를 다시 켜 주세요.` : "";
  $("#kwlist").innerHTML = s.default_keywords.map((k) => `<option value="${esc(k)}">`).join("");
  $("#keyword").placeholder = `키워드 (비우면: ${s.default_keywords.slice(0, 2).join(", ")} 외 ${s.default_keywords.length - 2}개)`;
  $("#rules").innerHTML = `운영 규칙(코드에서 강제): 영상당 댓글 1개 · 계정 1개 · 하루 최대 ${s.daily_max}개 · ${s.min_chars}~${s.max_chars}자 · 링크/해시태그/지어낸 이용 후기 금지`;
  renderProfiles();
}

function renderProfiles() {
  const s = state.status;
  if (!s) return;
  const anyReady = s.profiles.some((p) => p.ready);
  const items = [{ name: "auto", label: "자동 배정", st: "오늘 적게 쓴 계정부터 돌아가며", ready: anyReady }]
    .concat(s.profiles.map((p) => ({ name: p.name, label: p.name, st: p.ready ? `오늘 ${p.today}개` : "로그인 필요", ready: p.ready })));
  $("#profiles").innerHTML = items.map((p) => `
    <label class="prof ${state.profile === p.name ? "sel" : ""}">
      <input type="radio" name="prof" value="${esc(p.name)}" ${state.profile === p.name ? "checked" : ""}>
      ${p.name === "auto" ? "" : `<span class="dot ${p.ready ? "on" : ""}"></span>`}
      <span class="name">${esc(p.label)}</span>
      <span class="st">${esc(p.st)}</span>
      ${p.name === "auto" ? "" : `<button class="btn small" data-login="${esc(p.name)}" title="일반 크롬 창으로 열어 구글 계정에 로그인">${p.ready ? "다시 로그인" : "로그인"}</button>`}
    </label>`).join("");
  document.querySelectorAll('input[name="prof"]').forEach((r) => r.addEventListener("change", () => { state.profile = r.value; renderProfiles(); updatePostButton(); }));
  document.querySelectorAll("[data-login]").forEach((b) => b.addEventListener("click", async (e) => {
    e.preventDefault();
    try {
      await api(`/api/profiles/${encodeURIComponent(b.dataset.login)}/login`, { method: "POST" });
      alert(`'${b.dataset.login}' 크롬 창이 열렸어요.\n\n1) 이 계정으로 쓸 구글 계정에 로그인\n2) 유튜브가 열리면 로그인됐는지 확인\n3) 그 크롬 창을 완전히 닫기\n\n창을 닫아야 댓글 달기에서 이 프로필을 열 수 있어요.`);
      setTimeout(loadStatus, 3000);
    } catch (err) { alert(err.message); }
  }));
}

/* ---------- ① 영상 불러오기 ---------- */
function renderList(meta) {
  if (meta !== undefined) $("#listMeta").textContent = meta;
  if (!state.videos.length) {
    $("#list").innerHTML = `<div class="empty">조건에 맞는 영상이 없어요.<br>다른 키워드로 검색해 보세요.</div>`;
    return;
  }
  $("#list").innerHTML = state.videos.map((v) => `
    <button class="card ${state.selected?.video_id === v.video_id ? "sel" : ""}" data-id="${v.video_id}">
      <img src="${esc(v.thumbnail)}" alt="" loading="lazy">
      <div>
        <div class="t">${esc(v.title)}</div>
        <div class="m">${esc(v.channel)}<br>${ago(v.published_at)} · 조회수 ${fmt(v.views)} · 댓글 ${fmt(v.comment_count ?? 0)}</div>
        <span class="badge ${v.rising ? "hot" : ""}">시간당 ${fmt(Math.round(v.views_per_hour))}회${v.rising ? " 🔥" : ""}</span>
        ${v.posted ? `<span class="badge done">댓글 완료</span>` : v.has_draft ? `<span class="badge draft">초안 있음</span>` : ""}
      </div>
    </button>`).join("");
  document.querySelectorAll(".card").forEach((c) => c.addEventListener("click", () => select(c.dataset.id)));
}

$("#btnLoad").addEventListener("click", async () => {
  const b = $("#btnLoad"), kw = $("#keyword").value.trim(), rising = $("#onlyRising").checked;
  busy(b, true);
  $("#listMeta").textContent = rising ? "최근 36시간 영상 중 뜨기 직전 영상을 찾는 중…" : "유튜브에서 최신 영상을 찾는 중…";
  try {
    const q = new URLSearchParams({ rising });
    if (kw) q.set("keyword", kw);
    state.videos = await api(`/api/videos?${q}`);
    renderList(`${kw ? `‘${kw}’` : "연애 키워드"} ${rising ? "뜨기 직전" : "최신"} 영상 ${state.videos.length}개 · 최신순`);
    if (!state.videos.length) $("#list").innerHTML = `<div class="empty">조건에 맞는 영상이 없어요.<br>${rising ? "‘뜨기 직전 영상만’을 끄거나 " : ""}다른 키워드로 검색해 보세요.</div>`;
  } catch (e) { $("#listMeta").textContent = ""; alert(e.message); }
  busy(b, false);
});
$("#onlyRising").addEventListener("change", () => state.videos.length && $("#btnLoad").click());

async function addLink() {
  const b = $("#btnLink"), url = $("#link").value.trim();
  if (!url) return $("#link").focus();
  busy(b, true);
  try {
    const v = await api("/api/videos/link", { method: "POST", body: JSON.stringify({ url }) });
    state.videos = [v, ...state.videos.filter((x) => x.video_id !== v.video_id)];
    $("#link").value = "";
    renderList("링크로 추가한 영상이 맨 위에 있어요");
    select(v.video_id);
  } catch (e) { alert(e.message); }
  busy(b, false);
}
$("#btnLink").addEventListener("click", addLink);
$("#link").addEventListener("keydown", (e) => e.key === "Enter" && addLink());
$("#keyword").addEventListener("keydown", (e) => e.key === "Enter" && $("#btnLoad").click());

/* ---------- ② 영상 선택 → 초안 ---------- */
async function select(id, regenerate = false) {
  const v = state.videos.find((x) => x.video_id === id);
  if (!v) return;
  state.selected = v; state.draft = null; state.problems = [];
  if (!state.posting) resetSteps();
  renderList();
  updatePostButton();
  $("#detail").innerHTML = videoHead(v) + `
    <div class="loading-box"><div class="spinner"></div>
      AI가 <b>자막과 인기 댓글</b>을 읽고<br>영상 속 연애 고민을 정리하는 중… <br><small>(보통 10~40초)</small></div>`;
  try {
    const d = await api("/api/draft", { method: "POST", body: JSON.stringify({ video_id: id, regenerate }) });
    if (state.selected?.video_id !== id) return; // 그 사이 다른 영상을 골랐으면 무시
    state.draft = d;
    v.has_draft = true;
    if (d.posted) v.posted = true;
    renderList();
    renderDetail();
  } catch (e) {
    if (state.selected?.video_id !== id) return;
    $("#detail").innerHTML = videoHead(v) + `<div class="res bad">초안을 만들지 못했어요: ${esc(e.message)}</div>
      <button class="btn full" style="margin-top:10px" onclick="select('${id}', true)">다시 시도</button>`;
  }
  updatePostButton();
}

function videoHead(v) {
  return `<div class="vhead"><img src="${esc(v.thumbnail)}" alt="">
    <div><div class="t">${esc(v.title)}</div>
    <div class="m">${esc(v.channel)} · ${ago(v.published_at)}</div>
    <a href="${esc(v.url)}" target="_blank" rel="noopener">유튜브에서 보기 ↗</a></div></div>`;
}

function renderDetail() {
  const v = state.selected, d = state.draft;
  const posted = d.posted ? `<div class="res ok">이 영상에는 이미 <b>${esc(d.posted.profile)}</b> 계정으로 댓글을 달았어요. (영상당 1개)</div>` : "";
  $("#detail").innerHTML = videoHead(v) + posted + `
    <div class="box worry"><div class="k">영상 속 고민 한 줄</div><div class="v">${esc(d.worry)}</div></div>
    <div class="two">
      <div class="box"><div class="k">댓글에서 짚은 장면</div><div class="v">${esc(d.scene)}</div></div>
      <div class="box"><div class="k">시청자 페르소나</div><div class="v">${esc(d.persona)}</div></div>
    </div>
    <div class="box">
      <div class="k">댓글 초안 <span style="font-weight:400">(직접 고쳐도 돼요)</span></div>
      <textarea id="draft">${esc(d.comment)}</textarea>
      <div class="draft-foot">
        <span class="count" id="count"></span>
        <button class="btn small" id="btnRegen">↻ 다시 생성</button>
      </div>
      <ul class="checks" id="checks"></ul>
      ${(d.alternatives || []).length ? `<div class="alts"><div class="k">다른 후보 (누르면 초안으로)</div>
        ${d.alternatives.map((a, i) => `<button class="alt" data-alt="${i}">${esc(a)}</button>`).join("")}</div>` : ""}
    </div>
    ${(d.top_comments || []).length ? `<div class="box"><div class="k">AI가 읽은 인기 댓글 (상위 ${d.top_comments.length}개)</div>
      ${d.top_comments.map((c) => `<div class="tc">${esc(c)}</div>`).join("")}</div>` : ""}
    <div class="src">근거: ${d.had_transcript ? "자막" : "제목·설명(자막 없음)"} · 인기 댓글 ${d.top_comment_count}개 · ${esc(d.model)}${d.cached ? " · 저장된 초안" : ""}</div>`;
  $("#draft").addEventListener("input", debounce(recheck, 250));
  $("#btnRegen").addEventListener("click", () => select(v.video_id, true));
  document.querySelectorAll("[data-alt]").forEach((b) => b.addEventListener("click", () => {
    const cur = $("#draft").value.trim();
    $("#draft").value = d.alternatives[b.dataset.alt];
    d.alternatives[b.dataset.alt] = cur;      // 원래 초안은 후보로 돌려놓음
    b.textContent = cur;
    recheck();
  }));
  recheck();
}

function debounce(fn, ms) { let t; return (...a) => { clearTimeout(t); t = setTimeout(() => fn(...a), ms); }; }

async function recheck() {
  const ta = $("#draft");
  if (!ta) return;
  const { chars, problems } = await api("/api/check", { method: "POST", body: JSON.stringify({ comment: ta.value }) });
  state.problems = problems;
  const s = state.status;
  const lenOk = chars >= s.min_chars && chars <= s.max_chars;
  $("#count").className = `count ${lenOk ? "ok" : "bad"}`;
  $("#count").textContent = `${chars}자 / ${s.min_chars}~${s.max_chars}자`;
  const has = (word) => problems.some((p) => p.includes(word));
  const items = [
    ["길이 " + s.min_chars + "~" + s.max_chars + "자", lenOk],
    [`‘${s.brand}’ 한 번 언급`, !has(s.brand + "'") && !has("언급")],
    ["링크·해시태그 없음", !has("링크") && !has("해시태그")],
    ["지어낸 이용 후기 아님", !has("후기")],
    ["광고 말투 · AI 상투어 아님", !has("광고") && !has("상투어")],
  ];
  $("#checks").innerHTML = items.map(([t, ok]) => `<li class="${ok ? "ok" : "bad"}">${esc(t)}</li>`).join("");
  updatePostButton();
}

/* ---------- ③ 댓글 달기 ---------- */
const STEP_ORDER = ["opening", "loading", "comment_box", "typing", "posting", "verifying", "done"];
const STEP_LABEL = { opening: "프로필 여는 중", loading: "영상 여는 중", comment_box: "댓글 창 여는 중", typing: "입력 중", posting: "게시 중", verifying: "내 댓글 확인 중", done: "게시 완료 · 댓글 보기" };

function resetSteps() { renderSteps(null); $("#result").innerHTML = ""; }

function renderSteps(job) {
  const cur = job ? job.step : null;
  const failed = cur === "failed";
  const lastIdx = failed ? STEP_ORDER.indexOf(job.timeline.filter((t) => t.step !== "failed").slice(-1)[0]?.step) : STEP_ORDER.indexOf(cur);
  $("#steps").innerHTML = STEP_ORDER.map((s, i) => {
    let cls = "";
    if (job) {
      if (failed) cls = i < lastIdx ? "done" : i === lastIdx ? "fail" : "";
      else if (cur === "done") cls = "done";
      else cls = i < lastIdx ? "done" : i === lastIdx ? "now" : "";
    }
    let sub = "";
    if (job && s === "typing" && (cls === "now" || cls === "done")) {
      const pct = Math.round((job.typed / job.comment.length) * 100);
      sub = `<span class="sub">${job.typed} / ${job.comment.length}자</span><div class="bar"><i style="width:${pct}%"></i></div>`;
    }
    if (job && s === "opening" && cls) sub = `<span class="sub">${esc(job.profile)}</span>`;
    return `<li class="${cls}"><span class="b"></span><span>${STEP_LABEL[s]}${sub}</span></li>`;
  }).join("");
}

function updatePostButton() {
  const b = $("#btnPost"), h = $("#postHint");
  let why = "";
  if (state.posting) why = "게시 진행 중이에요…";
  else if (!state.selected) why = "먼저 ①에서 영상을 고르세요";
  else if (!state.draft) why = "댓글 초안을 만드는 중이에요";
  else if (state.draft.posted || state.selected.posted) why = "이 영상에는 이미 댓글을 달았어요 (영상당 1개)";
  else if (state.problems.length) why = "초안이 규칙에 맞지 않아요: " + state.problems.join(", ");
  else if (state.status && state.status.today >= state.status.daily_max) why = `오늘 게시 상한(${state.status.daily_max}개)에 도달했어요`;
  else if (state.status && !state.status.profiles.some((p) => p.ready)) why = "로그인된 프로필이 없어요 — 위에서 [로그인]을 먼저 하세요";
  else if (state.profile !== "auto" && !state.status.profiles.find((p) => p.name === state.profile)?.ready) why = `${state.profile} 프로필에 먼저 로그인하세요`;
  b.disabled = !!why;
  h.textContent = why || "누르면 크롬이 열리고 한 글자씩 입력해서 게시해요";
  h.className = `hint ${why && state.selected && state.draft && !state.posting ? "bad" : ""}`;
}

$("#btnPost").addEventListener("click", async () => {
  const comment = $("#draft").value.trim();
  const v = state.selected;
  state.posting = true; updatePostButton(); resetSteps();
  try {
    const { job_id } = await api("/api/post", { method: "POST", body: JSON.stringify({ video_id: v.video_id, comment, profile: state.profile }) });
    v.posted = true; renderList(); loadHistory();
    await poll(job_id, v);
  } catch (e) {
    $("#result").innerHTML = `<div class="res bad">${esc(e.message)}</div>`;
  }
  state.posting = false;
  await loadStatus(); loadHistory(); updatePostButton();
});

async function poll(jobId, v) {
  for (;;) {
    const j = await api(`/api/jobs/${jobId}`);
    renderSteps(j);
    if (j.step === "done") {
      $("#result").innerHTML = `<div class="res ok">✓ <b>${esc(j.profile)}</b> 계정으로 게시하고, 댓글 목록에서 확인했어요.<br>
        <a href="${esc(j.comment_url)}" target="_blank" rel="noopener">댓글 보기 ↗</a>
        ${j.screenshot ? `<a href="${esc(j.screenshot)}" target="_blank"><img src="${esc(j.screenshot)}?t=${Date.now()}" alt="게시 화면"></a>` : ""}</div>`;
      if (state.draft) state.draft.posted = { profile: j.profile };
      return;
    }
    if (j.step === "failed") {
      $("#result").innerHTML = `<div class="res bad"><b>실패:</b> ${esc(j.message)}
        ${j.screenshot ? `<a href="${esc(j.screenshot)}" target="_blank"><img src="${esc(j.screenshot)}?t=${Date.now()}" alt="실패 화면"></a>` : ""}</div>`;
      // 게시 전 실패면 다시 시도할 수 있게 표시를 되돌림 (서버 기록 기준)
      const hist = await api("/api/history");
      const mine = hist.find((h) => h.id === j.post_id);
      if (mine && mine.status === "failed") { v.posted = false; renderList(); }
      return;
    }
    await new Promise((r) => setTimeout(r, 300));
  }
}

/* ---------- 기록 ---------- */
const STATUS_LABEL = { success: "성공", failed: "실패", unverified: "확인 필요", running: "진행 중" };
let newestFirst = true;
$("#btnOrder").addEventListener("click", () => {
  newestFirst = !newestFirst;
  $("#btnOrder").textContent = newestFirst ? "최신순 ↓" : "오래된 순 ↑";
  loadHistory();
});
async function loadHistory() {
  let rows = await api("/api/history");
  if (!rows.length) return;
  if (!newestFirst) rows = rows.reverse();
  $("#history").innerHTML = rows.map((r) => `
    <tr>
      <td style="white-space:nowrap">${esc(r.created_at.slice(5, 16).replace("T", " "))}</td>
      <td class="v"><a href="https://www.youtube.com/watch?v=${esc(r.video_id)}" target="_blank" rel="noopener">${esc(r.video_title || r.video_id)}</a></td>
      <td style="white-space:nowrap">${esc(r.profile)}</td>
      <td class="c">${esc(r.comment)}${r.reason ? `<div class="reason">${esc(r.reason)}</div>` : ""}</td>
      <td><span class="pill ${r.status}">${STATUS_LABEL[r.status] || r.status}</span></td>
      <td>${r.comment_url ? `<a class="link" href="${esc(r.comment_url)}" target="_blank" rel="noopener">댓글 보기</a>` : ""}${r.screenshot ? `<a class="link" href="${esc(r.screenshot)}" target="_blank">화면</a>` : ""}</td>
    </tr>`).join("");
}

resetSteps();
loadStatus().then(updatePostButton);
loadHistory();
