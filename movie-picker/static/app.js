const state = {
  user: null,
  services: new Set(), genres: new Set(),
  quizId: null, history: [], pair: null,
  displayed: [],
  seen: new Map(),          // movie_id -> title, loaded from the server per profile
};
let serviceList = null;     // [{key, name, logo}], loaded once
let genreList = null;       // [{id, name}], loaded once
const $ = (id) => document.getElementById(id);

function show(screen) {
  for (const s of document.querySelectorAll(".screen")) s.hidden = s.id !== screen;
  const order = ["services", "genres", "quiz", "results"];
  $("steps").hidden = !order.includes(screen);
  for (const li of document.querySelectorAll(".steps li")) {
    const i = order.indexOf(li.dataset.step), cur = order.indexOf(screen);
    li.classList.toggle("active", i === cur);
    li.classList.toggle("done", i < cur);
  }
  setError("");
  window.scrollTo({ top: 0 });
}

function setLoading(on) { $("loading").hidden = !on; }
function setError(msg) { $("error").hidden = !msg; $("error").textContent = msg || ""; }

async function api(path, { method, body } = {}) {
  const res = await fetch(path, {
    method: method || (body ? "POST" : "GET"),
    headers: body ? { "Content-Type": "application/json" } : undefined,
    body: body ? JSON.stringify(body) : undefined,
  });
  const data = res.status === 204 ? {} : await res.json().catch(() => ({}));
  if (res.status === 401 && path !== "/api/me") {
    showProfiles();
  }
  if (!res.ok) throw new Error(typeof data.detail === "string" ? data.detail : "Something went wrong.");
  return data;
}

function chip(label, selected, onToggle, logo) {
  const b = document.createElement("button");
  b.type = "button";
  b.className = "chip";
  b.setAttribute("aria-pressed", selected);
  if (logo) {
    const img = document.createElement("img");
    img.src = logo; img.alt = "";
    b.append(img);
  }
  b.append(label);
  b.addEventListener("click", () => {
    const on = b.getAttribute("aria-pressed") !== "true";
    b.setAttribute("aria-pressed", on);
    onToggle(on);
  });
  return b;
}

// ---------- Profiles ----------
async function showProfiles() {
  state.user = null;
  $("me").hidden = true;
  show("profiles");
  $("new-profile").hidden = true;
  $("profile-error").hidden = true;
  try {
    const { users, icons } = await api("/api/users");
    $("profile-list").replaceChildren(...users.map((u) => {
      const b = document.createElement("button");
      b.type = "button";
      b.className = "profile";
      b.innerHTML = `<span class="avatar"></span><span class="name"></span>`;
      b.querySelector(".avatar").textContent = u.icon;
      b.querySelector(".name").textContent = u.name;
      b.addEventListener("click", async () => enter(await api("/api/login", { body: { user_id: u.id } })));
      return b;
    }), addProfileButton());
    renderIconPicker(icons);
    if (!users.length) openNewProfile();
  } catch (e) { setError(e.message); }
}

function addProfileButton() {
  const b = document.createElement("button");
  b.type = "button";
  b.className = "profile add";
  b.innerHTML = `<span class="avatar">＋</span><span class="name">New profile</span>`;
  b.addEventListener("click", openNewProfile);
  return b;
}

function openNewProfile() {
  $("new-profile").hidden = false;
  $("profile-name").focus();
}

function renderIconPicker(icons) {
  const picker = $("icon-picker");
  picker.replaceChildren(...icons.map((icon, i) => {
    const b = document.createElement("button");
    b.type = "button";
    b.className = "icon-choice";
    b.textContent = icon;
    b.setAttribute("aria-label", `Icon ${icon}`);
    b.setAttribute("aria-pressed", i === 0);
    b.addEventListener("click", () => {
      for (const other of picker.children) other.setAttribute("aria-pressed", other === b);
    });
    return b;
  }));
}

$("new-profile").addEventListener("submit", async (e) => {
  e.preventDefault();
  const icon = $("icon-picker").querySelector("[aria-pressed=true]")?.textContent;
  try {
    enter(await api("/api/users", { body: { name: $("profile-name").value, icon } }));
    $("profile-name").value = "";
  } catch (err) {
    $("profile-error").hidden = false;
    $("profile-error").textContent = err.message;
  }
});

async function enter(user) {
  state.user = user;
  $("me-icon").textContent = user.icon;
  $("me-name").textContent = user.name;
  $("me").hidden = false;
  await loadSeen();
  await showServices();
}

$("switch-profile").addEventListener("click", async () => {
  await api("/api/logout", { method: "POST" });
  showProfiles();
});

// ---------- Seen list (stored per profile on the server) ----------
async function loadSeen() {
  // One-time move of a seen list from before profiles existed (kept in this browser).
  try {
    const local = JSON.parse(localStorage.getItem("seen") || "{}");
    const movies = Object.entries(local).map(([id, title]) => ({ movie_id: Number(id), title }));
    if (movies.length) await api("/api/me/seen", { body: movies });
    localStorage.removeItem("seen");
    localStorage.removeItem("services");
  } catch { /* storage unavailable: nothing to move */ }
  const rows = await api("/api/me/seen");
  state.seen = new Map(rows.map((r) => [r.movie_id, r.title]));
}

function updateSeenNote() {
  $("seen-note").hidden = state.seen.size === 0;
  $("seen-count").textContent = state.seen.size === 1 ? "1 movie" : `${state.seen.size} movies`;
}

$("clear-seen").addEventListener("click", async () => {
  await api("/api/me/seen", { method: "DELETE" });
  state.seen.clear();
  updateSeenNote();
});

// ---------- Screen 1: services ----------
async function showServices() {
  show("services");
  // Nothing clickable until the profile is refreshed, so Next never uses stale counts.
  $("service-list").replaceChildren();
  $("services-next").disabled = true;
  setLoading(true);
  try {
    state.user = await api("/api/me");  // refresh: services and past-answer count change after each quiz
    serviceList ??= await api("/api/services");
    state.services = new Set(state.user.services.filter((k) => serviceList.some((s) => s.key === k)));
    $("service-list").replaceChildren(...serviceList.map((s) => chip(s.name, state.services.has(s.key), (on) => {
      on ? state.services.add(s.key) : state.services.delete(s.key);
      $("services-next").disabled = state.services.size === 0;
    }, s.logo)));
    $("services-next").disabled = state.services.size === 0;
  } catch (e) { setError(e.message); }
  setLoading(false);
}

$("services-next").addEventListener("click", async () => {
  show("genres");
  const past = state.user.past_answers || 0;
  $("use-history-row").hidden = past === 0;
  $("past-count").textContent = `(from ${past} past answer${past === 1 ? "" : "s"})`;
  if (genreList) return;
  setLoading(true);
  try {
    genreList = await api("/api/genres");
    $("genre-list").replaceChildren(...genreList.map((g) => chip(g.name, false, (on) => {
      on ? state.genres.add(g.id) : state.genres.delete(g.id);
    })));
  } catch (e) { setError(e.message); }
  setLoading(false);
});

// ---------- Screen 2: genres ----------
$("genres-next").addEventListener("click", async () => {
  try {
    const { quiz_id } = await api("/api/quizzes", {
      body: { services: [...state.services], genres: [...state.genres],
              use_history: $("use-history-row").hidden || $("use-history").checked },
    });
    state.quizId = quiz_id;
    state.user.services = [...state.services];
  } catch (e) { return setError(e.message); }
  state.history = [];
  show("quiz");
  step();
});
for (const b of document.querySelectorAll("[data-back]")) {
  b.addEventListener("click", () => (b.dataset.back === "services" ? showServices() : show(b.dataset.back)));
}

// ---------- Screen 3: this-or-that ----------
function quizBody() {
  return { services: [...state.services], genres: [...state.genres], history: state.history, quiz_id: state.quizId };
}

async function step(finish = false) {
  setError("");
  setLoading(true);
  state.pair = null;  // ignore clicks until the next pair arrives
  $("pair").replaceChildren();
  try {
    const data = await api("/api/next", { body: { ...quizBody(), finish } });
    if (data.done) return showResults(data);
    state.pair = data.pair;
    $("round-label").textContent = `Question ${data.round} of ${data.rounds}`;
    $("progress-bar").style.width = `${((data.round - 1) / data.rounds) * 100}%`;
    $("finish").hidden = data.round <= 5 || data.round >= data.rounds;
    const learned = data.learned || { quizzes: 0 };
    $("learned-note").hidden = !learned.quizzes;
    $("learned-note").textContent = learned.quizzes
      ? `Starting from what you taught us in ${learned.quizzes} past quiz${learned.quizzes === 1 ? "" : "zes"}` +
        (data.rounds < 10 ? `, so just ${data.rounds} questions this time.` : ".")
      : "";
    $("pair").append(option(data.pair[0], "a"), option(data.pair[1], "b"));
  } catch (e) {
    setError(e.message);
    if (!state.history.length && state.user) show("genres");
  } finally { setLoading(false); }
}

function option(movie, choice) {
  const b = document.createElement("button");
  b.type = "button";
  b.className = "option";
  b.innerHTML = `
    <img class="poster" alt="" loading="lazy">
    <div class="body"><h3></h3><div class="meta"></div><p></p></div>`;
  if (movie.poster) b.querySelector("img").src = movie.poster;
  b.querySelector("h3").textContent = movie.title;
  b.querySelector(".meta").textContent = [movie.year, movie.rating ? `★ ${movie.rating.toFixed(1)}` : null].filter(Boolean).join(" · ");
  b.querySelector("p").textContent = movie.overview;
  b.addEventListener("click", () => answer(choice));
  return b;
}

function answer(choice) {
  if (!state.pair) return;
  const [a, b] = state.pair;
  state.history.push({ a: a.id, b: b.id, choice });
  step();
}
$("tie").addEventListener("click", () => answer("tie"));
$("skip").addEventListener("click", () => answer("skip"));
$("finish").addEventListener("click", () => step(true));

// ---------- Screen 4: results ----------
function moreBody(limit) {
  return { ...quizBody(), finish: true, displayed: state.displayed, limit };
}

function showResults({ recommendations: recs, taste, more }) {
  show("results");
  $("taste").hidden = !taste?.length;
  if (taste?.length) {
    const strong = document.createElement("strong");
    strong.textContent = "Your taste: ";
    $("taste").replaceChildren(strong, taste.join(" · "));
  }
  state.displayed = [];
  $("result-list").replaceChildren();
  $("more-end").hidden = true;
  updateSeenNote();
  if (!recs.length) {
    $("result-list").textContent = "We couldn't confirm any matches on your services right now. Try again with more services or genres.";
    $("more").hidden = true;
    return;
  }
  appendCards(recs);
  setMore(more);
}

function setMore(more) {
  $("more").hidden = !more;
  $("more-end").hidden = more;
}

function appendCards(recs) {
  for (const m of recs) {
    if (state.displayed.includes(m.id)) continue;
    state.displayed.push(m.id);
    $("result-list").append(card(m));
  }
}

function card(m) {
  const el = document.createElement("article");
  el.className = "card";
  el.innerHTML = `
    <img class="poster" alt="" loading="lazy">
    <div class="body">
      <h3><a target="_blank" rel="noopener"></a></h3>
      <div class="match"></div>
      <div class="meta"></div>
      <div class="badges"></div>
      <p class="overview"></p>
      <p class="why"></p>
      <button type="button" class="seen-btn">✓ Already seen</button>
    </div>`;
  if (m.poster) el.querySelector("img").src = m.poster;
  const link = el.querySelector("h3 a");
  link.textContent = m.title;
  link.href = m.tmdb_url;
  el.querySelector(".meta").textContent = [m.year, m.rating ? `★ ${m.rating.toFixed(1)}` : null].filter(Boolean).join(" · ");
  const match = el.querySelector(".match");
  if (m.match != null) match.textContent = `${m.match}% match`; else match.remove();
  for (const s of m.services) {
    const span = document.createElement("span");
    span.className = "badge";
    span.textContent = s;
    el.querySelector(".badges").append(span);
  }
  el.querySelector(".overview").textContent = m.overview;
  el.querySelector(".why").textContent = m.why;
  el.querySelector(".seen-btn").addEventListener("click", (e) => markSeen(m, el, e.currentTarget));
  return el;
}

// Seen-marking and replacements run one at a time, so each replacement request
// already knows about the previous one and two quick clicks don't fetch the same movie.
let replacements = Promise.resolve();

function markSeen(movie, el, button) {
  button.disabled = true;
  state.seen.set(movie.id, movie.title);
  updateSeenNote();
  // Its series can come back now: having seen one Godzilla doesn't rule out another.
  state.displayed = state.displayed.filter((id) => id !== movie.id);
  el.classList.add("leaving");
  setTimeout(() => el.remove(), 250);
  replacements = replacements.then(async () => {
    try {
      await api("/api/me/seen", { body: [{ movie_id: movie.id, title: movie.title, poster: movie.poster }] });
      const data = await api("/api/next", { body: moreBody(1) });
      appendCards(data.recommendations);
    } catch (e) { setError(e.message); }
  });
}

$("more").addEventListener("click", async () => {
  const button = $("more");
  button.disabled = true;
  button.textContent = "Loading…";
  setError("");
  try {
    const data = await api("/api/next", { body: moreBody(10) });
    appendCards(data.recommendations);
    setMore(data.more && data.recommendations.length > 0);
  } catch (e) { setError(e.message); }
  button.disabled = false;
  button.textContent = "Show more";
});

$("restart").addEventListener("click", () => {
  state.history = [];
  state.quizId = null;
  showServices();
});

// ---------- History ----------
const CHOICE_LABEL = { a: "picked", b: "picked", tie: "tie", skip: "neither" };

$("open-history").addEventListener("click", async () => {
  show("history");
  setLoading(true);
  try {
    serviceList ??= await api("/api/services");
    renderHistory(await api("/api/me/history"));
  } catch (e) { setError(e.message); }
  setLoading(false);
});
$("history-back").addEventListener("click", () => showServices());

function renderHistory(quizzes) {
  const list = $("history-list");
  if (!quizzes.length) {
    list.textContent = "No quizzes yet. Your answers and picks will show up here.";
    return;
  }
  const serviceName = (k) => serviceList.find((s) => s.key === k)?.name || k;
  list.replaceChildren(...quizzes.map((q) => {
    const el = document.createElement("details");
    el.className = "quiz-entry";
    const when = new Date(q.started_at.replace(" ", "T") + "Z");
    const summary = document.createElement("summary");
    summary.innerHTML = `<span class="when"></span><span class="what"></span><span class="count"></span>`;
    summary.querySelector(".when").textContent = when.toLocaleString(undefined, { dateStyle: "medium", timeStyle: "short" });
    summary.querySelector(".what").textContent = [
      q.services.map(serviceName).join(", "),
      q.genres.length ? q.genres.map((g) => g.name).join(", ") : "any genre",
    ].join(" · ");
    summary.querySelector(".count").textContent =
      `${q.answers.length} answers · ${q.recommendations.length} picks${q.finished_at ? "" : " · unfinished"}`;
    el.append(summary);

    if (q.taste.length) {
      const taste = document.createElement("p");
      taste.className = "taste";
      taste.textContent = `Your taste: ${q.taste.join(" · ")}`;
      el.append(taste);
    }
    if (q.answers.length) {
      const h = document.createElement("h4");
      h.textContent = "Your answers";
      const ol = document.createElement("ol");
      ol.className = "answers";
      for (const a of q.answers) {
        const li = document.createElement("li");
        const left = document.createElement("span"), right = document.createElement("span");
        left.textContent = a.title_a; right.textContent = a.title_b;
        if (a.choice === "a" || a.choice === "tie") left.className = "chosen";
        if (a.choice === "b" || a.choice === "tie") right.className = "chosen";
        const tag = document.createElement("em");
        tag.textContent = CHOICE_LABEL[a.choice];
        li.append(left, " vs ", right, " ", tag);
        ol.append(li);
      }
      el.append(h, ol);
    }
    if (q.recommendations.length) {
      const h = document.createElement("h4");
      h.textContent = "Recommended";
      const row = document.createElement("div");
      row.className = "mini-grid";
      for (const r of q.recommendations) {
        const item = document.createElement("a");
        item.className = "mini";
        item.href = `https://www.themoviedb.org/movie/${r.movie_id}`;
        item.target = "_blank";
        item.rel = "noopener";
        item.innerHTML = `<img alt="" loading="lazy"><span class="t"></span><span class="m"></span>`;
        if (r.poster) item.querySelector("img").src = r.poster;
        item.querySelector(".t").textContent = r.title;
        item.querySelector(".m").textContent = [r.match != null ? `${r.match}% match` : null,
          state.seen.has(r.movie_id) ? "seen" : null].filter(Boolean).join(" · ");
        row.append(item);
      }
      el.append(h, row);
    }
    return el;
  }));
  list.firstElementChild.open = true;
}

document.addEventListener("keydown", (e) => {
  if ($("quiz").hidden || !state.pair || !$("loading").hidden) return;
  if (e.key === "ArrowLeft") answer("a");
  if (e.key === "ArrowRight") answer("b");
  if (e.key === "ArrowUp") { e.preventDefault(); answer("tie"); }
  if (e.key === "ArrowDown") { e.preventDefault(); answer("skip"); }
});

(async function boot() {
  try { await enter(await api("/api/me")); } catch { showProfiles(); }
})();
