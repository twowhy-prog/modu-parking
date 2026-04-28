/* ── 상태 ─────────────────────────────────────────────── */
const SPEAKER_NAMES  = ["화자1", "화자2", "화자3", "화자4"];
const SPEAKER_COLORS = ["var(--speaker-1)", "var(--speaker-2)", "var(--speaker-3)", "var(--speaker-4)"];

let voices    = [];          // [{name, path}]
let segCount  = 0;           // 세그먼트 고유 ID 카운터
let segments  = [];          // [{id, spk, text, voice}]
let history   = [];          // 서버 히스토리

let audio     = null;        // HTMLAudioElement
let currentUrl = null;       // 현재 재생 중인 audio URL
let saveUrl   = null;        // 다운로드용 URL

/* ── JSON 안전 파싱 헬퍼 ─────────────────────────────── */
async function safeJson(res) {
  const text = await res.text();
  try { return JSON.parse(text); }
  catch { return null; }
}

/* ── 초기화 ──────────────────────────────────────────── */
window.addEventListener("DOMContentLoaded", async () => {
  await loadVoices();
  await loadHistory();
  addSegment();  // 기본 1개
});

async function loadVoices() {
  try {
    const res = await fetch("/api/voices");
    const data = await safeJson(res);
    if (Array.isArray(data)) voices = data;
  } catch { voices = []; }
}

/* ── 세그먼트 관리 ────────────────────────────────────── */
function addSegment(spkIdx = null, text = "", voice = "") {
  const id  = ++segCount;
  const spk = spkIdx !== null ? spkIdx : Math.min(segments.length, 3);
  segments.push({ id, spk, text, voice });
  renderSegments();
  // 마지막 textarea 포커스
  const last = document.querySelector(`.segment:last-of-type .seg-textarea`);
  if (last) last.focus();
}

function removeSegment(id) {
  if (segments.length <= 1) return;
  segments = segments.filter(s => s.id !== id);
  renderSegments();
}

function cycleSpk(id) {
  const s = segments.find(s => s.id === id);
  if (!s) return;
  s.spk = (s.spk + 1) % 4;
  renderSegments();
}

function renderSegments() {
  const container = document.getElementById("segments-container");
  container.innerHTML = "";

  segments.forEach((seg) => {
    const div = document.createElement("div");
    div.className = "segment";
    div.dataset.spk = seg.spk + 1;

    // 화자 선택 버튼
    const btnSpk = document.createElement("button");
    btnSpk.className = "seg-speaker";
    btnSpk.textContent = SPEAKER_NAMES[seg.spk];
    btnSpk.title = "클릭하여 화자 변경";
    btnSpk.onclick = () => cycleSpk(seg.id);

    // 음성 프리셋 선택
    const selVoice = document.createElement("select");
    selVoice.className = "seg-voice";
    const defOpt = document.createElement("option");
    defOpt.value = ""; defOpt.textContent = "기본 음성";
    selVoice.appendChild(defOpt);
    voices.forEach(v => {
      const opt = document.createElement("option");
      opt.value = v.name; opt.textContent = v.name;
      if (v.name === seg.voice) opt.selected = true;
      selVoice.appendChild(opt);
    });
    selVoice.onchange = () => { seg.voice = selVoice.value; };

    // 삭제 버튼
    const btnDel = document.createElement("button");
    btnDel.className = "seg-del";
    btnDel.textContent = "×";
    btnDel.title = "삭제";
    btnDel.onclick = () => removeSegment(seg.id);

    const top = document.createElement("div");
    top.className = "seg-top";
    top.append(btnSpk, selVoice, btnDel);

    // 텍스트 입력
    const ta = document.createElement("textarea");
    ta.className = "seg-textarea";
    ta.placeholder = `${SPEAKER_NAMES[seg.spk]}의 발화를 입력하세요...`;
    ta.value = seg.text;
    ta.rows = 3;
    ta.oninput = () => { seg.text = ta.value; autoResize(ta); };
    ta.onkeydown = e => {
      if (e.ctrlKey && e.key === "Enter") generate();
    };
    autoResize(ta);

    div.append(top, ta);
    container.appendChild(div);
  });
}

function autoResize(ta) {
  ta.style.height = "auto";
  ta.style.height = ta.scrollHeight + "px";
}

function newSession() {
  segments = [];
  segCount = 0;
  addSegment();
  stopAudio();
  document.getElementById("btn-save").style.display = "none";
  document.getElementById("timeline").innerHTML = "";
}

/* ── 음성 생성 ────────────────────────────────────────── */
async function generate() {
  const segs = segments.filter(s => s.text.trim());
  if (!segs.length) { toast("텍스트를 입력해주세요."); return; }

  const model  = document.getElementById("model-select").value;
  const cfg    = parseFloat(document.getElementById("cfg-slider").value);

  showOverlay("음성 생성 중...");
  document.getElementById("btn-generate").disabled = true;

  try {
    const res = await fetch("/api/tts/generate", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        segments: segs.map(s => ({
          speaker:      SPEAKER_NAMES[s.spk],
          text:         s.text.trim(),
          voice_preset: s.voice || "",
        })),
        model,
        cfg,
        output_format: "wav",
      }),
    });

    if (!res.ok) {
      const err = await safeJson(res);
      throw new Error((err && err.detail) || `서버 오류 (${res.status})`);
    }

    const data = await safeJson(res);
    if (!data) throw new Error("응답을 파싱할 수 없습니다.");
    toast(`완료! ${data.duration.toFixed(1)}s 생성`);

    // 플레이어 로드
    loadAudio(data.audio_url, segs, data.duration);

    // 히스토리 갱신
    await loadHistory();

  } catch (e) {
    toast("오류: " + e.message, true);
  } finally {
    hideOverlay();
    document.getElementById("btn-generate").disabled = false;
  }
}

/* ── 오디오 플레이어 ──────────────────────────────────── */
function loadAudio(url, segs, duration) {
  stopAudio();
  currentUrl = url;
  saveUrl    = url;

  audio = new Audio(url);
  audio.ontimeupdate = updateSeekBar;
  audio.onended      = () => {
    document.getElementById("btn-play").textContent = "▶";
  };
  audio.onloadedmetadata = () => {
    document.getElementById("seek-bar").max = Math.floor(audio.duration);
    updateSeekBar();
  };

  // 타임라인 렌더
  if (segs && duration > 0) renderTimeline(segs, duration);

  document.getElementById("btn-save").style.display = "";
  audio.play();
  document.getElementById("btn-play").textContent = "⏸";
}

function stopAudio() {
  if (audio) { audio.pause(); audio = null; }
  document.getElementById("btn-play").textContent = "▶";
  document.getElementById("seek-bar").value = 0;
  document.getElementById("time-label").textContent = "00:00 / 00:00";
}

function togglePlay() {
  if (!audio) return;
  if (audio.paused) {
    audio.play();
    document.getElementById("btn-play").textContent = "⏸";
  } else {
    audio.pause();
    document.getElementById("btn-play").textContent = "▶";
  }
}

function seekRel(sec) {
  if (!audio) return;
  audio.currentTime = Math.max(0, Math.min(audio.duration, audio.currentTime + sec));
}

function seekTo(val) {
  if (!audio) return;
  audio.currentTime = val;
}

function setSpeed(val) {
  if (audio) audio.playbackRate = parseFloat(val);
}

function updateSeekBar() {
  if (!audio) return;
  const cur  = audio.currentTime;
  const dur  = audio.duration || 0;
  document.getElementById("seek-bar").value = Math.floor(cur);
  document.getElementById("time-label").textContent =
    `${fmt(cur)} / ${fmt(dur)}`;
}

function fmt(sec) {
  const m = Math.floor(sec / 60);
  const s = Math.floor(sec % 60);
  return `${String(m).padStart(2,"0")}:${String(s).padStart(2,"0")}`;
}

function saveAudio() {
  if (!saveUrl) return;
  const a = document.createElement("a");
  a.href = saveUrl;
  a.download = `voicestudio_${Date.now()}.wav`;
  a.click();
}

/* ── 타임라인 (화자 색상 바) ─────────────────────────── */
function renderTimeline(segs, totalDur) {
  const tl = document.getElementById("timeline");
  tl.innerHTML = "";
  // 균등 분배 (실제 구간 정보가 없으므로 텍스트 길이 비례)
  const lengths = segs.map(s => Math.max(s.text.length, 1));
  const total   = lengths.reduce((a, b) => a + b, 0);
  segs.forEach((s, i) => {
    const div = document.createElement("div");
    div.className = "tl-seg";
    div.style.width  = `${(lengths[i] / total) * 100}%`;
    div.style.background = SPEAKER_COLORS[s.spk];
    div.title = `${SPEAKER_NAMES[s.spk]} — ${s.text.slice(0, 30)}`;
    tl.appendChild(div);
  });
}

/* ── 히스토리 ─────────────────────────────────────────── */
async function loadHistory() {
  try {
    const res = await fetch("/api/history");
    const data = await safeJson(res);
    if (Array.isArray(data)) {
      history = data;
      renderHistory();
    }
  } catch { /* 서버 미실행 시 무시 */ }
}

function renderHistory() {
  const list = document.getElementById("history-list");
  if (!history.length) {
    list.innerHTML = '<div class="empty-history">생성 이력이 없습니다.</div>';
    return;
  }
  list.innerHTML = "";
  history.forEach(item => {
    const div = document.createElement("div");
    div.className = "history-item";
    div.onclick = () => playHistoryItem(item);

    const segs    = item.segments || [];
    const preview = segs.map(s => s.text).join(" ").slice(0, 40);
    const dt      = new Date(item.created_at);
    const dtStr   = `${dt.getMonth()+1}/${dt.getDate()} ${dt.getHours()}:${String(dt.getMinutes()).padStart(2,"0")}`;

    const btnDel = document.createElement("button");
    btnDel.className = "h-del";
    btnDel.textContent = "×";
    btnDel.title = "삭제";
    btnDel.onclick = async (e) => {
      e.stopPropagation();
      await deleteHistory(item.job_id);
    };

    div.innerHTML = `
      <div class="h-title">${preview || "(텍스트 없음)"}</div>
      <div class="h-meta">
        <span>${dtStr}</span>
        <span>${item.duration.toFixed(1)}s</span>
        <span>${segs.length}개 발화</span>
      </div>`;
    div.appendChild(btnDel);
    list.appendChild(div);
  });
}

function playHistoryItem(item) {
  document.querySelectorAll(".history-item").forEach(el => el.classList.remove("active"));
  event.currentTarget.classList.add("active");

  const segs = item.segments || [];
  loadAudio(item.audio_url, segs.map(s => ({
    spk:  SPEAKER_NAMES.indexOf(s.speaker) >= 0 ? SPEAKER_NAMES.indexOf(s.speaker) : 0,
    text: s.text,
  })), item.duration);

  // 세그먼트 에디터에 복원
  segments = segs.map((s, i) => ({
    id:    ++segCount,
    spk:   SPEAKER_NAMES.indexOf(s.speaker) >= 0 ? SPEAKER_NAMES.indexOf(s.speaker) : 0,
    text:  s.text,
    voice: s.voice_preset || "",
  }));
  renderSegments();
}

async function deleteHistory(jobId) {
  try {
    await fetch(`/api/history/${jobId}`, { method: "DELETE" });
    await loadHistory();
  } catch (e) {
    toast("삭제 실패: " + e.message, true);
  }
}

/* ── 유틸 ─────────────────────────────────────────────── */
function showOverlay(msg = "처리 중...") {
  document.getElementById("overlay-msg").textContent = msg;
  document.getElementById("overlay").classList.add("show");
}
function hideOverlay() {
  document.getElementById("overlay").classList.remove("show");
}

let toastTimer = null;
function toast(msg, isError = false) {
  const el = document.getElementById("toast");
  el.textContent = msg;
  el.style.background = isError ? "#4a1a1a" : "#222";
  el.classList.add("show");
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => el.classList.remove("show"), 3000);
}
