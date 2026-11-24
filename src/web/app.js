"use strict";

const el = id => document.getElementById(id);
const value = id => el(id).value;
const set = (id, val) => { el(id).value = val ?? ""; };
const text = (id, str) => { el(id).textContent = str; };
const labels = ["A", "B", "C", "D"];
const speakerKeys = ["database", "device", "mode", "duration", "countdown", "gain", "threshold", "margin"];
const arrayKeys = ["count", "mode", "rate", "duration", "countdown", "cal_start", "cal_end",
  "test_start", "test_end", "extrapolation", "expected"];
let token, job, source, direction, drag, selectedId, rosterDatabase;
let busy = false;
let samples = [];
let tracks = [null, null, null, null];

async function api(path, data, file) {
  const options = {};
  if (data !== undefined || file) {
    options.method = "POST";
    options.headers = { "X-Workbench-Token": token, "Content-Type": "application/json" };
    options.body = JSON.stringify(data);
    if (file) {
      options.headers["Content-Type"] = "application/octet-stream";
      options.headers["X-File-Name"] = encodeURIComponent(file.name);
      options.body = file;
    }
  }
  const response = await fetch("/api/" + path, options);
  const result = await response.json();
  if (!response.ok) throw new Error(result.error || "無法完成操作");
  return result;
}

function status(message, { error = false, progress = null, stop = false } = {}) {
  el("status").hidden = false;
  el("status").classList.toggle("error", error);
  text("status-message", message);
  el("progress").hidden = progress === null;
  if (progress !== null) el("progress").value = progress;
  el("stop").hidden = !stop;
}

function setBusy(next) {
  busy = next;
  document.querySelectorAll("[data-action]").forEach(control => { control.disabled = next; });
}

async function action(run) {
  if (busy) return;
  setBusy(true);
  try {
    await run();
  } catch (error) {
    status(error.message, { error: true });
  } finally {
    setBusy(false);
  }
}

function click(id, run) {
  el(id).addEventListener("click", () => action(run));
}

function tab(page) {
  el("speaker").hidden = page !== "speaker";
  el("array").hidden = page !== "array";
  const speaker = page === "speaker";
  text("title", speaker ? "聲紋辨識" : "聲源定位");
  text("description", speaker ? "註冊講者、修改 ID，或用一段新錄音驗證辨識結果。"
    : "校準麥克風的時間差，再估算聲音來自哪個方向。");
  document.querySelectorAll("[data-page]").forEach(button => {
    const active = button.dataset.page === page;
    button.classList.toggle("active", active);
    if (active) button.setAttribute("aria-current", "page");
    else button.removeAttribute("aria-current");
  });
  redraw();
}

function settings() {
  const speaker = Object.fromEntries(speakerKeys.map(key => [key, value("s-" + key)]));
  const array = Object.fromEntries(arrayKeys.map(key => [key, value("a-" + key)]));
  array.measured = el("a-measured").checked;
  array.devices = labels.map(label => value("mic-" + label));
  array.positions = labels.map(label => [value("x-" + label), value("y-" + label)]);
  array.distances = labels.map(label => value("distance-" + label));
  return { speaker, array };
}

function applySettings(speaker, array) {
  speakerKeys.filter(key => key !== "device").forEach(key => set("s-" + key, speaker[key]));
  arrayKeys.forEach(key => set("a-" + key, array[key]));
  el("a-measured").checked = array.measured === true;
  labels.forEach((label, i) => {
    set("x-" + label, array.positions?.[i]?.[0]);
    set("y-" + label, array.positions?.[i]?.[1]);
    set("distance-" + label, array.distances?.[i]);
  });
  countChanged();
}

function createMicRows() {
  for (const label of labels) {
    const row = el("mics").insertRow();
    row.id = "mic-row-" + label;
    cell(row, label);
    const select = document.createElement("select");
    select.id = "mic-" + label;
    select.setAttribute("aria-label", label + " 麥克風");
    row.insertCell().append(select);
    for (const [key, type] of [["x", "X 座標"], ["y", "Y 座標"], ["distance", "校準聲程"]]) {
      const input = document.createElement("input");
      input.type = "number";
      input.step = "0.001";
      input.id = key + "-" + label;
      input.setAttribute("aria-label", label + " " + type);
      row.insertCell().append(input);
    }
  }
}

function countChanged() {
  el("mic-row-D").hidden = value("a-count") !== "4";
  renderTracks();
}

async function refreshDevices(refresh, preferred) {
  const previous = preferred || settings();
  try {
    const devices = await api("devices", { refresh });
    const resolve = saved => {
      const match = devices.find(device => String(device.index) === String(saved)
        || device.index + " · " + device.name === String(saved));
      return match ? String(match.index) : "";
    };
    const setSelect = (id, selected) => {
      const select = el(id);
      select.replaceChildren(new Option("選擇麥克風", ""));
      devices.forEach(device => select.add(new Option(device.index + " · " + device.name, device.index)));
      select.value = selected;
    };
    const usb = devices.find(device => /usb/i.test(device.name));
    setSelect("s-device", resolve(previous.speaker.device) || (usb ? String(usb.index) : ""));
    labels.forEach((label, i) => setSelect("mic-" + label, refresh ? "" : resolve(previous.array.devices?.[i])));
    text("device-count", devices.length + " 個 WASAPI 輸入");
    return devices.length;
  } catch (error) {
    text("device-count", "無法讀取麥克風");
    throw error;
  }
}

async function runJob(name, form) {
  const started = await api("start", { action: name, settings: form });
  job = started.id;
  try {
    for (;;) {
      const state = await api("status", { id: job });
      status(state.message, { progress: state.progress, stop: state.can_stop, error: state.status === "error" });
      if (state.status === "done") return state.result;
      if (state.status === "error") throw new Error(state.message);
      if (state.status === "stopped") return null;
      await new Promise(resolve => setTimeout(resolve, state.can_stop ? 180 : 250));
    }
  } finally {
    job = null;
    el("stop").hidden = true;
  }
}

function fit(canvas) {
  const { width, height } = canvas.getBoundingClientRect();
  if (!width || !height) return null;
  const ratio = window.devicePixelRatio || 1;
  canvas.width = width * ratio;
  canvas.height = height * ratio;
  const context = canvas.getContext("2d");
  context.setTransform(ratio, 0, 0, ratio, 0, 0);
  return { context, width, height };
}

function setSource(item, needsReview = false) {
  source = item;
  el("playback").src = item.url;
  el("playback").hidden = false;
  text("source-info", item.name + " · " + item.duration.toFixed(2)
    + " 秒 · 拖曳波形選取發話區間" + (needsReview ? " · 請檢查錄音品質" : ""));
  set("s-start", "0");
  set("s-end", "");
  drawWave();
}

function drawWave() {
  const canvas = fit(el("waveform"));
  if (!canvas) return;
  const { context: ctx, width, height } = canvas;
  ctx.strokeStyle = "#8ca99c";
  ctx.beginPath();
  ctx.moveTo(0, height / 2);
  ctx.lineTo(width, height / 2);
  ctx.stroke();
  if (!source) return;
  const start = Math.max(0, Math.min(source.duration, Number(value("s-start"))));
  const end = Math.max(0, Math.min(source.duration, Number(value("s-end") || source.duration)));
  ctx.fillStyle = "#b6dacb";
  if (end > start) ctx.fillRect(start / source.duration * width, 0,
    (end - start) / source.duration * width, height);
  const max = Math.max(.001, ...source.peaks);
  ctx.strokeStyle = "#17664e";
  ctx.beginPath();
  source.peaks.forEach((peak, index) => {
    const x = (index + .5) / source.peaks.length * width;
    const amplitude = peak / max * (height / 2 - 3);
    ctx.moveTo(x, height / 2 - amplitude);
    ctx.lineTo(x, height / 2 + amplitude);
  });
  ctx.stroke();
}

function drawCompass() {
  const canvas = fit(el("compass"));
  if (!canvas) return;
  const { context: ctx, width, height } = canvas;
  const x = width / 2, y = height / 2, r = Math.max(15, Math.min(height / 2 - 34, width / 2 - 53));
  ctx.strokeStyle = "#b4cbc1";
  ctx.lineWidth = 1.5;
  ctx.beginPath();
  ctx.arc(x, y, r, 0, Math.PI * 2);
  ctx.stroke();
  ctx.setLineDash([3, 4]);
  ctx.beginPath();
  ctx.moveTo(x - r, y);
  ctx.lineTo(x + r, y);
  ctx.moveTo(x, y - r);
  ctx.lineTo(x, y + r);
  ctx.stroke();
  ctx.setLineDash([]);
  ctx.textAlign = "center";
  ctx.textBaseline = "middle";
  ctx.font = "13px Segoe UI, Microsoft JhengHei, sans-serif";
  ctx.fillStyle = "#476259";
  ctx.fillText("前 0°", x, y - r - 15);
  ctx.fillText("後 180°", x, y + r + 16);
  ctx.fillText("左 +90°", x - r - 27, y);
  ctx.fillText("右 −90°", x + r + 27, y);
  ctx.fillStyle = "#125c48";
  ctx.beginPath();
  ctx.arc(x, y, 3, 0, Math.PI * 2);
  ctx.fill();
  if (direction == null) return;
  ctx.save();
  ctx.translate(x, y);
  ctx.rotate(-direction * Math.PI / 180);
  ctx.strokeStyle = "#125c48";
  ctx.lineWidth = 4;
  ctx.lineCap = "round";
  ctx.beginPath();
  ctx.moveTo(0, 0);
  ctx.lineTo(0, -r + 7);
  ctx.stroke();
  ctx.beginPath();
  ctx.moveTo(0, -r);
  ctx.lineTo(-7, -r + 16);
  ctx.lineTo(7, -r + 16);
  ctx.fill();
  ctx.restore();
}

function redraw() {
  drawWave();
  drawCompass();
}

function clip() {
  if (!source) throw new Error("請先錄音或匯入 WAV");
  return { id: source.id, start: value("s-start"), end: value("s-end"), gain: value("s-gain") };
}

function cell(row, content) {
  const td = row.insertCell();
  td.textContent = content;
  return td;
}

function showRoster(data) {
  rosterDatabase = data.database;
  const body = el("roster");
  body.replaceChildren();
  if (!data.speakers.length) {
    const td = cell(body.insertRow(), data.exists ? "目前沒有註冊講者" : "找不到資料庫，可建立空白資料庫");
    td.colSpan = 3;
  }
  for (const speaker of data.speakers) {
    const row = body.insertRow();
    cell(row, speaker.id);
    cell(row, speaker.recordings ?? "—");
    const operations = cell(row, "");
    operations.className = "align-right";
    const button = document.createElement("button");
    button.textContent = "修改 ID";
    button.setAttribute("aria-label", "修改 " + speaker.id + " 的 ID");
    button.dataset.action = "";
    button.disabled = busy;
    button.addEventListener("click", () => {
      if (rosterDatabase !== value("s-database")) {
        status("資料庫路徑已變更，請先載入名單", { error: true });
        return;
      }
      selectedId = speaker.id;
      text("old-id", "目前 ID · " + speaker.id);
      set("new-id", speaker.id);
      el("rename-dialog").showModal();
      el("new-id").select();
    });
    operations.append(button);
  }
}

function showSamples() {
  const list = el("samples");
  list.replaceChildren();
  samples.forEach((sample, index) => {
    const row = document.createElement("li");
    const label = document.createElement("span");
    label.textContent = sample.name + " · " + sample.start + "～" + (sample.end || "檔尾")
      + " 秒 · " + sample.gain + " dB";
    const remove = document.createElement("button");
    remove.textContent = "移除";
    remove.dataset.action = "";
    remove.disabled = busy;
    remove.addEventListener("click", () => {
      samples.splice(index, 1);
      showSamples();
    });
    row.append(label, remove);
    list.append(row);
  });
}

function renderTracks() {
  const list = el("track-list");
  list.replaceChildren();
  labels.slice(0, Number(value("a-count"))).forEach((label, i) => {
    const item = tracks[i];
    const row = document.createElement("li");
    const name = document.createElement("span");
    name.textContent = label + " · " + (item?.name || "尚未載入");
    row.append(name);
    if (item) {
      const link = document.createElement("a");
      link.href = item.url;
      link.target = "_blank";
      link.rel = "noopener";
      link.textContent = "聽 " + label + " 軌";
      row.append(link);
    }
    list.append(row);
  });
}

createMicRows();
document.querySelectorAll("[data-page]").forEach(button => button.addEventListener("click", () => tab(button.dataset.page)));
new ResizeObserver(redraw).observe(el("speaker").parentElement);
new ResizeObserver(drawWave).observe(el("waveform").parentElement);
el("a-count").addEventListener("change", countChanged);
["s-start", "s-end"].forEach(id => el(id).addEventListener("input", drawWave));

const wave = el("waveform");
function pointerTime(event) {
  const rect = wave.getBoundingClientRect();
  return Math.max(0, Math.min(1, (event.clientX - rect.left) / rect.width)) * source.duration;
}
wave.addEventListener("pointerdown", event => {
  if (!source || event.button !== 0) return;
  drag = pointerTime(event);
  wave.setPointerCapture(event.pointerId);
});
function selectRange(event) {
  if (drag === undefined) return;
  const second = pointerTime(event);
  if (Math.abs(second - drag) >= .05) {
    set("s-start", Math.min(drag, second).toFixed(2));
    const end = Math.max(drag, second);
    set("s-end", end >= source.duration - .005 ? "" : end.toFixed(2));
    drawWave();
  }
}
wave.addEventListener("pointermove", selectRange);
wave.addEventListener("pointerup", event => { selectRange(event); drag = undefined; });
wave.addEventListener("pointercancel", () => { drag = undefined; });

click("save", async () => {
  const result = await api("settings", settings());
  status("已儲存測試設定 · " + result.path);
});
click("refresh", async () => {
  const count = await refreshDevices(true);
  status("找到 " + count + " 個 WASAPI 輸入。若要定位，請重新確認 A～D 的裝置順序。");
});
el("stop").addEventListener("click", async () => {
  if (!job) return;
  try {
    await api("stop", { id: job });
  } catch (error) {
    status(error.message, { error: true });
  }
});

function recordingParams(options) {
  return { ...options, device: options.device === "" ? null : Number(options.device) };
}

click("s-record", async () => {
  el("playback").pause();
  const result = await runJob("record-speaker", recordingParams(settings().speaker));
  if (!result) return;
  setSource(result.files[0], result.summary.needs_review);
  status("錄音完成，可播放、註冊或辨識。");
});
el("s-import").addEventListener("change", event => {
  const file = event.target.files[0];
  event.target.value = "";
  if (!file) return;
  action(async () => {
    status("正在讀取音檔");
    setSource(await api("import-speaker", undefined, file));
    status("已載入 " + file.name);
  });
});
click("add-sample", () => {
  const part = { ...clip(), name: source.name };
  if (samples.some(sample => sample.id === part.id)) throw new Error("這份錄音已加入，請另錄一段");
  samples.push(part);
  showSamples();
  status("已加入第 " + samples.length + " 段樣本");
});
click("enroll", async () => {
  const name = value("enroll-id").trim();
  if (!name) throw new Error("請填入新的講者 ID");
  const result = await runJob("enroll", { database: value("s-database"), name, samples });
  set("s-database", result.database);
  showRoster(result);
  samples = [];
  showSamples();
  set("enroll-id", "");
  status("已註冊 " + name);
});
click("identify", async () => {
  const data = { ...settings().speaker, clip: clip() };
  const { result, output, threshold, margin } = await runJob("identify", data);
  const labels = { unknown: "未知講者", unusable_audio: "語音不足或音檔不可用" };
  text("recognition", result.status === "recognized" ? result.user_id : labels[result.status] || result.status);
  const reason = result.reason === "Need at least 2 seconds of detected speech" ? "至少需要 2 秒可辨識的語音"
    : result.reason;
  text("recognition-note", reason || "本次門檻 " + threshold.toFixed(2) + " · 最低分差 " + margin.toFixed(2));
  const scores = el("scores");
  scores.replaceChildren();
  for (const [id, score] of Object.entries(result.scores || {}).sort((a, b) => b[1] - a[1])) {
    const row = document.createElement("div");
    row.className = "score";
    const name = document.createElement("span");
    name.textContent = id;
    const number = document.createElement("b");
    number.textContent = score.toFixed(3);
    row.append(name, number);
    scores.append(row);
  }
  const link = el("speaker-download");
  link.href = output.url;
  link.hidden = false;
  status("辨識完成 · " + output.path);
});

async function loadRoster(verify) {
  const result = await api("roster", { database: value("s-database"), verify });
  set("s-database", result.database);
  showRoster(result);
}
click("load-database", async () => {
  await loadRoster(true);
  status("已載入聲紋資料庫");
});
click("create-database", async () => {
  const result = await runJob("create", { database: value("s-database") });
  set("s-database", result.database);
  showRoster(result);
  status("已建立空白聲紋資料庫");
});
el("rename-cancel").addEventListener("click", () => el("rename-dialog").close());
el("rename-form").addEventListener("submit", event => {
  event.preventDefault();
  const updated = value("new-id").trim();
  const current = selectedId;
  el("rename-dialog").close();
  if (current === updated) return;
  action(async () => {
    const result = await runJob("rename", { database: rosterDatabase, current, updated });
    showRoster(result);
    status("已將 " + current + " 改為 " + updated);
  });
});

el("a-geometry").addEventListener("change", event => {
  const file = event.target.files[0];
  event.target.value = "";
  if (!file) return;
  action(async () => {
    const data = JSON.parse(await file.text());
    const positions = data.positions_m;
    if (![3, 4].includes(positions?.length) || positions.some(pair => !Array.isArray(pair)
      || pair.length !== 2 || pair.some(coordinate => !Number.isFinite(coordinate)))) {
      throw new Error("座標 JSON 需要三或四組 positions_m，每組含 X 和 Y");
    }
    set("a-count", positions.length);
    labels.forEach((label, i) => {
      set("x-" + label, positions[i]?.[0]);
      set("y-" + label, positions[i]?.[1]);
    });
    el("a-measured").checked = data.geometry_measured === true;
    set("a-extrapolation", data.max_extrapolation_seconds ?? 30);
    countChanged();
    status("已匯入 " + file.name);
  });
});
click("a-record", async () => {
  el("playback").pause();
  const options = settings().array;
  options.devices = options.devices.map(index => index === "" ? null : Number(index));
  const result = await runJob("record-array", options);
  if (!result) return;
  set("track-folder", result.folder);
  tracks = result.files;
  renderTracks();
  status(result.summary.needs_review ? "多軌錄音完成，請先單獨播放各軌確認品質。" : "多軌錄音完成，可進行定位。");
});
click("load-tracks", async () => {
  tracks = await api("tracks", { folder: value("track-folder"), count: value("a-count") });
  renderTracks();
  status("已載入 " + tracks.length + " 軌錄音");
});
el("import-tracks").addEventListener("change", event => {
  const files = Array.from(event.target.files);
  event.target.value = "";
  if (!files.length) return;
  action(async () => {
    const count = Number(value("a-count"));
    const ordered = labels.slice(0, count).map(label =>
      files.filter(file => file.name.toLowerCase() === label.toLowerCase() + ".wav"));
    if (ordered.some(group => group.length !== 1)) {
      throw new Error("請各選一個 A.wav～" + labels[count - 1] + ".wav");
    }
    const loaded = [];
    for (const [index, [file]] of ordered.entries()) {
      status("正在載入 " + file.name, { progress: index / count });
      loaded.push(await api("import-track", undefined, file));
    }
    tracks = loaded;
    set("track-folder", "");
    renderTracks();
    status("已載入 " + count + " 軌錄音");
  });
});

function signed(angle) {
  const formatted = Number(angle.toFixed(2));
  return (formatted > 0 ? "+" : "") + formatted + "°";
}
click("direction", async () => {
  const options = settings().array;
  const count = Number(options.count);
  const selected = labels.slice(0, count).map((_, index) => tracks[index]?.id);
  if (selected.some(id => !id)) throw new Error("請先多軌錄音、載入資料夾或選取各軌 WAV");
  const data = await runJob("direction", { ...options, tracks: selected });
  const { result, clock } = data;
  direction = result.azimuth_deg;
  drawCompass();
  text("angle", direction != null ? signed(direction) : result.status === "ambiguous" ? "方向不明確" : "沒有可用訊號");
  const details = [];
  if (result.absolute_error_deg != null) {
    details.push("真值 " + signed(result.expected_azimuth_deg) + " · 誤差 " + result.absolute_error_deg.toFixed(2) + "°");
  }
  if (result.peak_margin != null) details.push("峰值分差 " + result.peak_margin.toFixed(3) + " · 音窗 " + result.windows + " 個");
  details.push("校準殘差 P95（ms） " + clock.channels.slice(1).map((channel, index) =>
    labels[index + 1] + " " + (channel.residual_p95_seconds * 1000).toFixed(3)).join(" · "));
  text("direction-note", details.join("\n"));
  el("direction-download").href = data.output.url;
  el("clock-download").href = data.calibration.url;
  el("direction-downloads").hidden = false;
  status("定位完成 · " + data.folder);
});

async function init() {
  setBusy(true);
  try {
    const defaults = await api("bootstrap");
    token = defaults.token;
    applySettings(defaults.speaker, defaults.array);
    await loadRoster(false);
    await refreshDevices(false, defaults);
  } catch (error) {
    status(error.message, { error: true });
  } finally {
    setBusy(false);
  }
}

init();
