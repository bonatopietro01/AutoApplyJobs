"use strict";

const state = { jobs: [], selectedId: null, detail: null, toastTimer: null, searchTimer: null, dirty: false, materialsRevision: 0, detailRequest: 0, lastSearch: "", lastStatus: "", sortOrder: "newest", profileHasCv: false, directoryHandle: null, aiSettings: null, aiAgents: [], aiModels: [], aiKeyConfigured: false, diagnosticEvents: [], diagnosticSince: new Date().toISOString(), knowledgeCount: 0, bulkPlanToken: null, bulkBatchId: null, bulkPollTimer: null };
const byId = (id) => document.getElementById(id);
const jobIdentity = (job) => String(job?.job_id || job?.linkedin_job_id || "");
const DIRECTORY_DB_NAME = "job-letter-studio-settings";
const DIRECTORY_STORE_NAME = "local-folders";
let clientErrorWindowStarted = Date.now();
let clientErrorsReported = 0;

function element(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined && text !== null) node.textContent = String(text);
  return node;
}

async function request(path, options = {}) {
  let response;
  try {
    response = await fetch(path, { cache: "no-store", ...options });
  } catch (error) {
    error.requestId = await reportClientFailure("api_failure", "", error.stack, "", 0, error.name);
    throw error;
  }
  const payload = await response.json().catch(() => ({}));
  if (!response.ok) {
    const error = new Error(payload.error || `Richiesta non riuscita (${response.status}).`);
    error.requestId = response.headers.get("X-Request-ID") || "";
    if (response.status >= 500 && path !== "/api/diagnostics/client-error") {
      error.clientEventId = await reportClientFailure("api_failure", "", error.stack, error.requestId, response.status, error.name);
    }
    throw error;
  }
  return payload;
}

function errorForDisplay(error) {
  const message = error?.message || String(error || "Errore non specificato.");
  return error?.requestId ? `${message} · ID diagnostica: ${error.requestId}` : message;
}

function showToast(message, error = false) {
  const toast = byId("toast");
  toast.textContent = message instanceof Error ? errorForDisplay(message) : String(message);
  toast.classList.toggle("is-error", error);
  toast.classList.add("visible");
  clearTimeout(state.toastTimer);
  state.toastTimer = setTimeout(() => toast.classList.remove("visible"), 3600);
}

async function reportClientFailure(kind, source = "", stack = "", requestId = "", status = 0, errorType = "Error") {
  if (location.protocol !== "http:" || !["127.0.0.1", "localhost"].includes(location.hostname)) return "";
  const now = Date.now();
  if (now - clientErrorWindowStarted >= 60_000) {
    clientErrorWindowStarted = now;
    clientErrorsReported = 0;
  }
  if (clientErrorsReported >= 10 || kind === "") return "";
  clientErrorsReported += 1;
  const safeSource = ["/", "/app.js", "/styles.css"].includes(source) ? source : "";
  const payload = {
    kind,
    message: "Browser diagnostic event.",
    error_type: String(errorType || "Error").slice(0, 40),
    status: Number.isInteger(status) ? status : 0,
    source: safeSource,
    stack: String(stack || "").slice(0, 5_000),
    request_id: /^[a-f0-9]{16}$/.test(requestId) ? requestId : "",
  };
  try {
    const response = await Promise.race([
      fetch("/api/diagnostics/client-error", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
        keepalive: true,
      }),
      new Promise((_, reject) => setTimeout(() => reject(new Error("timeout")), 1_500)),
    ]);
    return response.headers.get("X-Request-ID") || "";
  } catch {
    return "";
  }
}

window.addEventListener("error", (event) => {
  const source = ["/app.js", "/styles.css", "/"].find((candidate) => event.filename?.endsWith(candidate)) || "";
  void reportClientFailure("javascript_error", source, event.error?.stack || "", "", 0, event.error?.name || "Error");
});

window.addEventListener("unhandledrejection", (event) => {
  const reason = event.reason;
  void reportClientFailure(
    "unhandled_rejection",
    "/app.js",
    reason instanceof Error ? reason.stack : "",
    reason?.requestId || "",
    0,
    reason instanceof Error ? reason.name : "Error",
  );
});

function displayStatus(status) {
  return ({ NEW: "Da valutare", PROCESSED: "In lavorazione", IGNORED: "Archiviato" })[status] || status;
}

function displayGeneration(task) {
  if (!task) return "Da preparare";
  const labels = {
    PENDING: task.stage ? `In attesa · ${task.stage}` : "In attesa",
    RUNNING: task.stage ? `In analisi · ${task.stage}` : "In analisi",
    READY: "Cover letter pronta", REVIEW: "Da rivedere", ERROR: "Errore",
    STALE: task.error_code === "auto_age_limit" ? "Saltato · oltre soglia"
      : task.error_code === "age_unverified" ? "Da verificare · data assente" : "Da aggiornare",
    MANUAL: "Risultato manuale",
  };
  return labels[task.state] || "Da preparare";
}

function generationErrorDescription(code) {
  return ({
    authentication: "Chiave API o accesso al modello da verificare",
    usage_limit: "Limite giornaliero raggiunto; i lavori restano in coda e ripartono al rinnovo o dopo aver aggiornato il limite",
    model_unavailable: "Modello non disponibile",
    rate_limit: "Limite API o credito raggiunto",
    network: "Connessione al provider non disponibile",
    invalid_response: "Risposta AI incompleta o non valida",
    input_too_large: "CV, annuncio o istruzioni troppo lunghi",
    local_input: "Controlla CV e profilo candidato",
    internal_error: "Errore interno; consulta Diagnostica e log",
  })[code] || "Controlla Diagnostica e log";
}

function shortDate(value) {
  if (!value) return "";
  const parsed = new Date(value);
  return Number.isNaN(parsed.valueOf()) ? "" : new Intl.DateTimeFormat("it-IT", { day: "numeric", month: "short" }).format(parsed);
}

function savedDate(value) {
  if (!value) return "";
  const parsed = new Date(value);
  return Number.isNaN(parsed.valueOf()) ? "" : new Intl.DateTimeFormat("it-IT", { day: "2-digit", month: "2-digit", year: "numeric" }).format(parsed);
}

function compareSavedJobs(a, b) {
  const dateA = Date.parse(a.first_saved_at || "");
  const dateB = Date.parse(b.first_saved_at || "");
  const validA = Number.isFinite(dateA);
  const validB = Number.isFinite(dateB);
  if (validA !== validB) return validA ? -1 : 1;
  if (!validA) return jobIdentity(a).localeCompare(jobIdentity(b));
  const byDate = state.sortOrder === "oldest" ? dateA - dateB : dateB - dateA;
  return byDate || jobIdentity(a).localeCompare(jobIdentity(b));
}

function initializeJobSorting() {
  const control = byId("job-sort");
  try {
    const saved = localStorage.getItem("job-letter-studio-sort-order");
    if (["newest", "oldest"].includes(saved)) state.sortOrder = saved;
  } catch { /* Local sorting still works when browser storage is unavailable. */ }
  control.value = state.sortOrder;
  control.addEventListener("change", () => {
    state.sortOrder = control.value === "oldest" ? "oldest" : "newest";
    try { localStorage.setItem("job-letter-studio-sort-order", state.sortOrder); } catch { /* Keep this choice for the current page. */ }
    renderJobs();
  });
}

function renderJobs() {
  const list = byId("job-list");
  list.replaceChildren();
  byId("job-count").textContent = String(state.jobs.length);
  byId("result-count").textContent = state.jobs.length ? `${state.jobs.length} risultati` : "";
  if (!state.jobs.length) {
    list.append(element("p", "list-empty", "Nessun annuncio corrisponde alla ricerca. Prova a cambiare filtro."));
    return;
  }

  const sortedJobs = [...state.jobs].sort(compareSavedJobs);
  for (const job of sortedJobs) {
    const jobId = jobIdentity(job);
    const selected = state.selectedId === jobId;
    const item = element("div", `job-row-item${selected ? " selected" : ""}`);
    item.setAttribute("role", "listitem");
    const row = element("button", `job-row${selected ? " selected" : ""}`);
    row.type = "button";
    row.setAttribute("aria-current", selected ? "true" : "false");
    const top = element("span", "job-row-top");
    top.append(element("h3", "", job.title || "Ruolo non disponibile"));
    const badge = element("span", "status-badge", displayStatus(job.status));
    badge.dataset.status = job.status;
    top.append(badge);
    row.append(top);
    row.append(element("p", "job-row-company", job.company || "Azienda non indicata"));
    if (job.origin === "chatgpt") row.append(element("span", "job-source-badge", "Preparata in ChatGPT"));
    else if (job.source === "manual") row.append(element("span", "job-source-badge", "Inserita manualmente"));
    const generation = element("span", "job-generation", displayGeneration(job.generation));
    generation.dataset.state = job.generation?.state || "PENDING";
    row.append(generation);
    const bottom = element("span", "job-row-bottom");
    bottom.append(element("span", "job-row-location", job.location || "Sede non indicata"));
    const date = savedDate(job.first_saved_at);
    const meta = element("span", "job-row-meta");
    const dateLabel = job.source === "manual" ? "Aggiunta" : "Salvata";
    meta.append(element("span", "job-row-saved-at", date ? `${dateLabel} il ${date}` : "Data non disponibile"));
    const letterStatus = element("span", "job-row-letter-status",
      job.has_cover_letter ? "✓ Cover letter creata" : "Cover letter da creare");
    letterStatus.dataset.hasCoverLetter = job.has_cover_letter ? "true" : "false";
    meta.append(letterStatus);
    bottom.append(meta);
    row.append(bottom);
    row.addEventListener("click", () => selectJob(jobId));

    const menu = document.createElement("details");
    menu.className = "job-row-menu";
    const trigger = document.createElement("summary");
    trigger.className = "job-row-menu-trigger";
    trigger.textContent = "⋯";
    trigger.setAttribute("aria-label", `Azioni per ${job.title || "annuncio"} presso ${job.company || "azienda"}`);
    trigger.title = "Azioni annuncio";
    const panel = element("div", "job-row-menu-panel");
    const forget = element("button", "job-row-forget", "Rimuovi dalla memoria");
    forget.type = "button";
    forget.addEventListener("click", (event) => {
      event.stopPropagation();
      menu.open = false;
      void forgetJob(job);
    });
    panel.append(forget);
    menu.append(trigger, panel);
    item.append(row, menu);
    list.append(item);
  }
}

async function loadJobs({ preserveSelection = true } = {}) {
  const params = new URLSearchParams();
  const query = byId("search-input").value.trim();
  const status = byId("status-filter").value;
  if (query) params.set("q", query);
  if (status) params.set("status", status);
  byId("job-list").replaceChildren(element("div", "loading-state", "Carico gli annunci salvati…"));
  const payload = await request(`/api/jobs?${params}`);
  const nextJobs = payload.jobs;
  const visibleSelection = nextJobs.some((job) => jobIdentity(job) === state.selectedId);
  if (state.dirty && nextJobs.length && !visibleSelection) {
    if (!window.confirm("Hai modifiche non salvate per l’annuncio selezionato. Scartarle e cambiare annuncio?")) {
      byId("search-input").value = state.lastSearch;
      byId("status-filter").value = state.lastStatus;
      return loadJobs({ preserveSelection: true });
    }
    state.dirty = false;
  }
  if (state.dirty && !nextJobs.length) {
    if (!window.confirm("La ricerca non mostra annunci e hai modifiche non salvate. Scartarle?")) {
      byId("search-input").value = state.lastSearch;
      byId("status-filter").value = state.lastStatus;
      return loadJobs({ preserveSelection: true });
    }
    state.dirty = false;
  }
  state.lastSearch = query;
  state.lastStatus = status;
  state.jobs = nextJobs;
  renderJobs();
  if (!state.jobs.length) {
    state.selectedId = null;
    state.detail = null;
    byId("job-detail").classList.add("hidden");
    byId("empty-selection").classList.remove("hidden");
    return;
  }
  const selectionIsVisible = state.jobs.some((job) => jobIdentity(job) === state.selectedId);
  if (!preserveSelection || !selectionIsVisible) await selectJob(jobIdentity(state.jobs[0]));
  else renderJobs();
}

async function createManualJob(event) {
  event.preventDefault();
  const form = byId("manual-job-form");
  const submit = byId("save-manual-job");
  const feedback = byId("manual-job-feedback");
  const data = new FormData(form);
  const description = String(data.get("description") || "").trim();
  if (!description) {
    feedback.textContent = "Incolla il testo dell’offerta prima di salvarla.";
    feedback.className = "inline-feedback is-error";
    byId("manual-job-description").focus();
    return;
  }
  submit.disabled = true;
  feedback.textContent = "Salvo l’offerta nel database locale…";
  feedback.className = "inline-feedback";
  try {
    const result = await request("/api/jobs/manual", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        description,
        title: String(data.get("title") || "").trim(),
        company: String(data.get("company") || "").trim(),
        location: String(data.get("location") || "").trim(),
      }),
    });
    byId("manual-job-dialog").close();
    form.reset();
    feedback.textContent = "";
    byId("search-input").value = "";
    byId("status-filter").value = "";
    await loadJobs({ preserveSelection: true });
    if (!state.dirty) await selectJob(jobIdentity(result.job));
    showToast(result.created ? "Offerta manuale salvata nell’app." : "Offerta già presente: ho aperto quella esistente.");
  } catch (error) {
    feedback.textContent = error.message || String(error);
    feedback.className = "inline-feedback is-error";
  } finally {
    submit.disabled = false;
  }
}

async function selectJob(jobId) {
  if (state.dirty && state.selectedId !== jobId) {
    if (!window.confirm("Hai modifiche non salvate. Scartarle e aprire un altro annuncio?")) return;
    state.dirty = false;
  }
  state.chatProposal = null;
  state.selectedId = jobId;
  const detailRequest = ++state.detailRequest;
  renderJobs();
  try {
    const detail = await request(`/api/jobs/${encodeURIComponent(jobId)}`);
    if (state.selectedId !== jobId || state.detailRequest !== detailRequest) return;
    state.detail = detail;
    renderDetail(!state.dirty);
  } catch (error) {
    showToast(error, true);
  }
}

async function forgetJob(job) {
  const jobId = jobIdentity(job);
  const selected = state.selectedId === jobId;
  const title = `${job.title || "Annuncio"} — ${job.company || "azienda non indicata"}`;
  const unsavedNote = selected && state.dirty ? "\n\nLe modifiche non salvate di questo annuncio verranno eliminate." : "";
  const message = `Rimuovere “${title}” dalla memoria dell’app?\n\nSaranno eliminati dal database l’annuncio, l’analisi, le ricerche, la chat, le versioni e i relativi dati di generazione. Per evitare che LinkedIn lo importi di nuovo, resterà solo un hash locale dell’ID. I PDF/Word già archiviati in data/cover_letters e i file scaricati non verranno cancellati.${unsavedNote}\n\nL’operazione non è annullabile.`;
  if (!window.confirm(message)) return;

  try {
    await request(`/api/jobs/${encodeURIComponent(jobId)}/memory`, { method: "DELETE" });
    if (selected) {
      state.dirty = false;
      state.selectedId = null;
      state.detail = null;
      state.chatProposal = null;
      byId("job-detail").classList.add("hidden");
      byId("empty-selection").classList.remove("hidden");
    }
    showToast("Annuncio e dati collegati rimossi dalla memoria locale.");
    await loadJobs({ preserveSelection: !selected });
  } catch (error) {
    showToast(error, true);
  }
}

function compactDisclosurePreview(value, maxLength = 88) {
  const text = String(value || "").replace(/\s+/g, " ").trim();
  if (text.length <= maxLength) return text;
  return text.slice(0, maxLength - 1).trimEnd() + "…";
}
function setCopy(nodeId, value, placeholder) {
  const node = byId(nodeId);
  const content = (value || "").trim();
  node.value = content;
  node.placeholder = placeholder;
  node.classList.toggle("is-empty", !content);
}

function renderDetail(scroll = false) {
  if (!state.detail) return;
  const { job, materials, cover_letters: letters, generation } = state.detail;
  byId("empty-selection").classList.add("hidden");
  byId("job-detail").classList.remove("hidden");
  byId("detail-company").textContent = job.company || "Azienda non indicata";
  byId("detail-title").textContent = job.title || "Ruolo non disponibile";
  byId("detail-location").textContent = job.location || "Sede non indicata";
  const status = byId("detail-status");
  status.textContent = displayStatus(job.status);
  status.dataset.status = job.status;
  const generationStatus = byId("generation-detail-status");
  generationStatus.textContent = displayGeneration(generation)
    + (generation?.error_code === "auto_age_limit" && generation?.stage ? ` · ${generation.stage}` : "")
    + (generation?.state === "ERROR" ? ` · ${generationErrorDescription(generation.error_code)}` : "")
    + (generation?.state === "ERROR" && generation.error_request_id
      ? ` · ID diagnostica: ${generation.error_request_id}` : "");
  generationStatus.classList.toggle("is-error", generation?.state === "ERROR");
  const preparedExternally = job.origin === "chatgpt";
  byId("regenerate-job").disabled = preparedExternally || generation?.state === "RUNNING";
  byId("regenerate-job").textContent = preparedExternally ? "Lettera preparata in ChatGPT" : generation?.state === "ERROR" ? "Riprova analisi e lettera" : "Rigenera analisi e lettera";
  byId("generation-gaps").textContent = generation?.gaps ? `Da verificare: ${generation.gaps}` : "";
  byId("generation-gaps").classList.toggle("hidden", !generation?.gaps);
  const link = byId("linkedin-link");
  const applicationUrl = job.application_url || job.linkedin_url;
  if (!applicationUrl) {
    link.removeAttribute("href");
    link.classList.add("hidden");
  } else {
    link.href = applicationUrl;
    link.setAttribute("aria-label", `Apri annuncio: ${job.title}`);
    link.classList.remove("hidden");
  }
  const applicationSent = job.application_status === "APPLIED";
  byId("application-status-label").textContent = `Stato candidatura: ${applicationSent ? "inviata" : job.application_status === "REJECTED" ? "rifiutata" : "non inviata"}`;
  byId("mark-application-sent").disabled = applicationSent;
  byId("mark-application-sent").textContent = applicationSent ? "Candidatura inviata" : "Segna come inviata";
  if (!state.dirty) {
  setCopy("company-analysis", materials?.company_analysis, "L’analisi aziendale apparirà qui quando importerai il risultato dell’agente.");
  setCopy("personalization-notes", materials?.personalization_notes, "Qui vedrai perché la lettera è stata adattata a questa azienda e a questo ruolo.");
  }
  byId("job-description").textContent = job.description || "Descrizione non disponibile nella copia locale.";
  if (!state.dirty) byId("cover-letter-text").value = materials?.cover_letter_text || "";
  byId("add-letter-to-knowledge").disabled = !String(materials?.cover_letter_text || "").trim() || state.knowledgeCount >= 10;
  byId("export-letter-text").disabled = state.dirty || !materials?.cover_letter_text;
  byId("export-letter-word").disabled = state.dirty || !materials?.cover_letter_text;
  if (!state.dirty) byId("agent-source").value = materials?.agent_name || "";
  byId("agent-name").textContent = materials?.agent_name ? `Fonte: ${materials.agent_name}` : "";
  byId("agent-name").classList.toggle("hidden", !materials?.agent_name);
  updateCharacterCount();
  const notes = String(materials?.personalization_notes || "").split(/\n|(?<=[.!?])\s/).filter(Boolean).slice(0, 2).join(" ");
  byId("personalization-summary").textContent = compactDisclosurePreview(notes || "Dettagli di personalizzazione");
  renderFiles(letters || []);
  renderResearch(state.detail.research);
  renderLaya(state.detail.local_decisions);
  renderFeedbackHistory(state.detail.feedback || []);
  if (byId("folder-hint")) byId("folder-hint").textContent = state.directoryHandle
    ? `Archivio interno: data/cover_letters/${jobIdentity(job)}/ · Download: ${state.directoryHandle.name}/[azienda - posizione - ID]/`
    : `Archivio interno: data/cover_letters/${jobIdentity(job)}/ · Per scegliere la cartella di download, apri Impostazioni.`;
  if (!state.dirty) {
  byId("materials-feedback").textContent = materials?.updated_at
    ? `Ultimo salvataggio locale: ${new Intl.DateTimeFormat("it-IT", { dateStyle: "medium", timeStyle: "short" }).format(new Date(materials.updated_at))}`
    : "Le modifiche vengono salvate solo in locale.";
  byId("materials-feedback").className = "inline-feedback";
  }
  if (scroll && !state.dirty) byId("detail-area").scrollTo({ top: 0, behavior: "smooth" });
}

function renderLaya(result) {
  const container = byId("laya-result");
  container.replaceChildren();
  byId("analyze-laya").disabled = result?.state === "RUNNING";
  if (result?.state !== "READY") {
    container.append(element("p", "inline-feedback", result?.message || "Nessuna analisi locale disponibile. Controlla lo stato di Laya nelle Impostazioni."));
    renderGenerationSelection(container, result?.generation_selection);
    return;
  }
  const roles = {"software-data-ai": "Software, dati e AI", "engineering-research": "Ingegneria e ricerca", "healthcare-pharma": "Healthcare e pharma", "operations-business": "Operations e business", "sales-marketing": "Sales e marketing", other: "Altro / classificazione incerta"};
  container.append(element("p", "", `${roles[result.role] || result.role} · seniority: ${result.seniority} · confidenza della categoria: ${Math.round(result.role_confidence * 100)}%`));
  if (result.analysis_level !== "preflight") {
    container.append(element("p", "", `Pertinenza degli estratti CV: ${result.match_score == null ? "non valutabile" : `${result.match_score}/100`} · ${result.elapsed_seconds}s${result.cached ? " · cache" : ""}`));
  }
  container.append(element("p", "settings-footnote", result.disclaimer));
  container.append(element("p", "settings-footnote", "Modello non calibrato sulle tue candidature: i punteggi possono essere errati anche quando la confidenza è alta."));
  renderGenerationSelection(container, result.generation_selection);
  if (result.analysis_level === "preflight") {
    if (result.replaced_agent_stages?.length) {
      const stages = {"job-company-research":"Job & Company Research", "main-coordinator":"Main Coordinator"};
      container.append(element("p", "", `In questa generazione Laya ha sostituito ${result.replaced_agent_stages.map(id => stages[id] || id).join(" e ")}: ${result.replaced_agent_stages.length} chiamate agente risparmiate.`));
    }
    container.append(element("p", "settings-footnote", "La generazione usa il preflight e il ranking locale del contesto; premi “Analizza con Laya” solo per chiedere separatamente i punteggi diagnostici dettagliati."));
    return;
  }
  if (result.replaced_agent_stages?.length) {
    const stages = {"job-company-research":"Job & Company Research", "main-coordinator":"Main Coordinator"};
    container.append(element("p", "", `In questa generazione Laya ha sostituito ${result.replaced_agent_stages.map(id => stages[id] || id).join(" e ")}: ${result.replaced_agent_stages.length} chiamate agente risparmiate.`));
  }
  container.append(element("p", "settings-footnote", `Analisi del ${shortDate(result.updated_at)}. Ricalcola dopo modifiche al CV, agli esempi o all’offerta. Sono stati valutati ${result.coverage.candidate_fragments_scored}/${result.coverage.candidate_fragments_total} estratti del candidato${result.coverage.job_partial ? "; descrizione dell’offerta parziale" : ""}. Il punteggio è la media dei tre estratti più pertinenti, non una verifica di tutti i requisiti.`));
  const evidence = element("details", "");
  evidence.append(element("summary", "", "Estratti prioritari per la scrittura"));
  for (const item of result.evidence) {
    evidence.append(element("p", "", `${item.source} · pertinenza ${item.score}/100 · posizione ${item.offset}`));
    evidence.append(element("pre", "knowledge-preview", item.text));
  }
  container.append(evidence);
  const styles = element("ul", "");
  for (const item of result.knowledge) styles.append(element("li", "", `${item.filename}: pertinenza ${item.score}/100 · qualità stilistica ${item.style_quality_score}/100${item.partial ? " · testo parziale" : ""} · fatti non verificati`));
  container.append(element("p", "", result.knowledge.length ? "Esempi knowledge · punteggi Laya consultivi; la generazione usa il ranking locale solo quando chiaro, altrimenti mantiene la selezione lessicale" : "Nessun esempio knowledge disponibile."), styles);
  container.append(element("p", "", `Selezione mostrata nell'analisi dettagliata: ${(result.selected_style_examples || []).map(item => item.filename).join(", ") || "nessuno"}.`));
  const duplicates = element("ul", "");
  for (const item of result.possible_duplicates) duplicates.append(element("li", "", `${item.title} · ID locale ${item.job_id} · somiglianza ${item.score}/100 — da verificare`));
  container.append(element("p", "", `Possibili duplicati: ${result.possible_duplicates.length}. Gli ID esatti sono gestiti dal database. Nessun annuncio viene rimosso. Sono stati controllati ${result.coverage.duplicate_candidates_checked}/${result.coverage.duplicate_candidates_total} candidati simili della stessa azienda.`), duplicates);
}

function renderGenerationSelection(container, selection) {
  if (!selection) return;
  const elapsed = Number(selection.elapsed_seconds || 0).toFixed(2);
  const prep = Number(selection.preprocessing_seconds || 0).toFixed(2);
  const ranking = Number(selection.ranking_seconds || 0).toFixed(2);
  const inference = Number(selection.laya_inference_seconds || 0).toFixed(2);
  const indexState = selection.candidate_index_cached ? "indice CV in cache" : "indice CV aggiornato";
  const indexBuild = Number(selection.candidate_index_build_seconds || 0).toFixed(2);
  const retrieval = Number(selection.candidate_retrieval_seconds || 0).toFixed(2);
  const worker = selection.laya_worker_startup_ms == null
    ? "worker Laya non avviato in questa selezione" : `avvio worker Laya ${(Number(selection.laya_worker_startup_ms) / 1000).toFixed(2)}s`;
  const status = selection.state === "READY" ? "selezione completata" : "fallback su CV e fonti originali";
  const method = selection.knowledge_selection_method ? ` · ${selection.knowledge_selection_method}` : "";
  const summary = element("p", "", `${status}${method} · contesto ${elapsed}s · ${Number(selection.candidate_excerpts?.length || 0)} prove CV · ${(selection.selected_examples || []).length} esempi`);
  const details = element("details", "generation-selection-details");
  details.append(element("summary", "", "Dettagli della selezione locale"));
  const content = element("div", "generation-selection-detail-content");
  content.append(element("p", "", `Contesto ${elapsed}s totali: preprocessing ${prep}s (include indice e recupero), ranking ${ranking}s, inferenza Laya ${inference}s. ${worker}.`));
  content.append(element("p", "", `CV: ${indexState} (${indexBuild}s, ${Number(selection.candidate_index_chunks || 0)} estratti); recupero ${retrieval}s. Esempi: ${(selection.selected_examples || []).join(", ") || "nessuno"}; fatti aziendali selezionati: ${Number(selection.selected_facts || 0)}.`));
  if (selection.example_scores?.length) {
    content.append(element("p", "settings-footnote", `Ranking esempi: ${selection.example_scores.map((item) => `${item.filename} (${item.relevance}/100 rilevanza, ${item.coherence}/100 coerenza)`).join(" · ")}. Punteggi locali consultivi.`));
  }
  content.append(element("p", "", selection.candidate_reduced
    ? `Estratti CV/contesto passati allo scrittore: ${Number(selection.candidate_writer_chars || 0).toLocaleString("it-IT")}/${Number(selection.candidate_source_chars || 0).toLocaleString("it-IT")} caratteri; il revisore riceve i documenti completi.`
    : `CV e contesto completi allo scrittore (${Number(selection.candidate_source_chars || 0).toLocaleString("it-IT")} caratteri).`));
  if (selection.candidate_reduced) {
    content.append(element("p", "settings-footnote", `Gli estratti selezionati sono testo originale: ${(selection.candidate_excerpts || []).map((item) => `${item.source} · ${item.score}/100 · ${item.chars} caratteri`).join("; ")}. I caratteri mostrano la riduzione del contesto, non una stima esatta dei token.`));
  }
  if (selection.selected_sources?.length) {
    const list = element("ul", "");
    for (const source of selection.selected_sources) {
      if (!/^https:\/\//i.test(String(source.url || ""))) continue;
      const item = element("li", "");
      const link = element("a", "research-source-link", source.title || source.url);
      link.href = source.url;
      link.target = "_blank";
      link.rel = "noopener noreferrer";
      item.append(link, document.createTextNode(` · pertinenza locale ${source.score}/100`));
      list.append(item);
    }
    content.append(element("p", "", "Pagine aziendali selezionate localmente per la scrittura:"), list);
  }
  if (selection.reason) content.append(element("p", "settings-footnote", selection.reason));
  details.append(content);
  container.append(summary, details);
}

function formatUsd(value) {
  const amount = Number(value);
  return Number.isFinite(amount) ? `$${amount.toFixed(Math.abs(amount) < 0.01 ? 6 : 4)}` : "non disponibile";
}

function formatGenerationTimingDelta(totalMs, attributedMs) {
  const deltaMs = Number(totalMs) - Number(attributedMs);
  if (!Number.isFinite(deltaMs)) return "Differenza temporale non disponibile.";
  if (Math.abs(deltaMs) <= 250) return `Scarto tra totale e fasi: ${(deltaMs / 1000).toFixed(2)} s (entro 0,25 s).`;
  if (deltaMs > 0) return `Tempo non attribuito alle fasi: ${(deltaMs / 1000).toFixed(2)} s.`;
  return `Le fasi registrate superano il totale di ${(Math.abs(deltaMs) / 1000).toFixed(2)} s: verificare sovrapposizioni o conteggi duplicati.`;
}

function renderLayaCost(estimate) {
  const container = byId("laya-cost-estimate");
  container.replaceChildren();
  const line = (label, result) => {
    const before = result.before_laya;
    const after = result.after_laya_success;
    const savings = result.api_savings_usd;
    container.append(element("p", "", `${label}: ${formatUsd(before.expected)} senza Laya → ${formatUsd(after.expected)} con preflight riuscito; risparmio ${formatUsd(savings.expected)} (${result.expected_savings_percent}%).`));
    container.append(element("p", "settings-footnote", `Costo basso / centrale / alto: ${formatUsd(before.low)} / ${formatUsd(before.expected)} / ${formatUsd(before.high)} senza Laya → ${formatUsd(after.low)} / ${formatUsd(after.expected)} / ${formatUsd(after.high)} con Laya. Risparmio stimato: ${formatUsd(savings.low)}–${formatUsd(savings.high)}.`));
    container.append(element("p", "settings-footnote", `Chiamate API: ${before.estimated_api_calls} → ${after.estimated_api_calls}; ricerche web: ${before.estimated_web_search_calls} → ${after.estimated_web_search_calls}.`));
    container.append(element("p", "settings-footnote", `Su 100 candidature: ${formatUsd(result.projections["100"].before_laya.expected)} → ${formatUsd(result.projections["100"].after_laya_success.expected)} (risparmio ${formatUsd(result.projections["100"].api_savings_usd.expected)}); su 1.000: ${formatUsd(result.projections["1000"].before_laya.expected)} → ${formatUsd(result.projections["1000"].after_laya_success.expected)} (risparmio ${formatUsd(result.projections["1000"].api_savings_usd.expected)}).`));
    container.append(element("p", "settings-footnote", `Nel caso: costi agenti ${formatUsd(before.agent_cost_usd.expected)} → ${formatUsd(after.agent_cost_usd.expected)}; ricerca web ${formatUsd(before.research_cost_usd.expected)} → ${formatUsd(after.research_cost_usd.expected)}.`));
  };
  line("Ricerca nuova", estimate.fresh_research);
  line("Ricerca già in cache", estimate.research_cache_hit);
  const state = estimate.laya_enabled
    ? "Laya è attiva. Il risparmio dipende da un preflight riuscito; in caso di fallback il costo resta quello senza Laya."
    : "Laya è disattivata. La cifra con Laya è uno scenario potenziale, subordinato a preflight riuscito.";
  container.append(element("p", "settings-footnote", state));
  const runtime = estimate.local_inference || {};
  container.append(element("p", "settings-footnote", runtime.samples
    ? `Preflight locale: media ${runtime.mean_seconds}s, mediana ${runtime.median_seconds}s su ${runtime.samples} analisi. Ranking di esempi/prove/fonti: media ${runtime.selection_mean_seconds ?? "n.d."}s, mediana ${runtime.selection_median_seconds ?? "n.d."}s su ${runtime.selection_samples || 0} candidature registrate. Il costo elettrico non è misurato né incluso.`
    : "Tempo e costo elettrico locale non ancora misurati con preflight reali salvati; l’inferenza non genera addebiti OpenAI."));
  container.append(element("p", "settings-footnote", "Le stime API mostrano il risparmio delle fasi agente sostituite da Laya; non anticipano il risparmio esatto di token della scrittura, che dipende dagli estratti inviati e dall’uso effettivo misurato dal provider."));
  const assumptions = estimate.assumptions || [];
  const retryNote = estimate.notes?.find((note) => note.startsWith("La pipeline")) || "";
  container.append(element("p", "settings-footnote", `Stima in USD da medie aggregate degli ultimi 30 giorni e tariffe standard al ${estimate.price_source_checked}; fasi prive di media propria usano valori condivisi o fallback (${assumptions.length ? assumptions.join(", ") : "nessuna"}). Gli intervalli basso/alto sono scenari 0,7×/1,5×, non intervalli statistici. Non è una fattura e non include sconti dell’account.${retryNote ? ` ${retryNote}` : ""}`));
}

async function loadLayaSettings() {
  try {
    const result = await request("/api/laya/settings");
    byId("laya-status").textContent = result.message;
  } catch (error) { byId("laya-status").textContent = errorForDisplay(error); }
  try {
    renderLayaCost(await request("/api/laya/cost-estimate"));
  } catch (error) {
    byId("laya-cost-estimate").textContent = errorForDisplay(error);
  }
}

async function analyzeLaya() {
  const jobId = state.selectedId;
  if (!jobId) return;
  byId("analyze-laya").disabled = true;
  try {
    const response = await request(`/api/jobs/${jobId}/local-decisions`, {method: "POST"});
    renderLaya(response.local_decisions);
    const poll = async () => {
      if (state.selectedId !== jobId) return;
      try {
        const detail = await request(`/api/jobs/${jobId}`);
        if (state.selectedId !== jobId) return;
        state.detail.local_decisions = detail.local_decisions;
        renderLaya(detail.local_decisions);
        if (detail.local_decisions?.state === "RUNNING") setTimeout(poll, 2000);
      } catch (error) { showToast(error, true); byId("analyze-laya").disabled = false; }
    };
    if (response.local_decisions?.state === "RUNNING") setTimeout(poll, 2000);
  } catch (error) { showToast(error, true); byId("analyze-laya").disabled = false; }
}

function renderFiles(letters) {
  const list = byId("file-list");
  if (!list) return;
  list.replaceChildren();
  byId("file-count").textContent = `${letters.length} file`;
  if (!letters.length) {
    list.append(element("p", "no-files", "Non hai ancora archiviato file per questo annuncio. Puoi caricare qui il PDF o il DOCX prodotto dal tuo agente."));
    return;
  }

  for (const letter of letters) {
    const card = element("article", "file-card");
    const format = String(letter.file_format).toLowerCase();
    const icon = element("span", `file-type ${format === "docx" ? "docx" : ""}`, format.toUpperCase());
    icon.setAttribute("aria-hidden", "true");
    card.append(icon);
    const meta = element("div", "file-meta");
    meta.append(element("strong", "", `Cover letter · v${String(letter.version).padStart(2, "0")}`));
    meta.append(element("span", "", `${letter.original_filename} · ${shortDate(letter.created_at)}`));
    card.append(meta);
    const actions = element("div", "file-actions");
    const baseUrl = `/api/letters/${letter.id}/file`;
    if (format === "pdf") {
      const open = element("a", "file-action", "Apri PDF");
      open.href = baseUrl;
      open.target = "_blank";
      open.rel = "noopener noreferrer";
      actions.append(open);
    } else {
      const open = element("button", "file-action", "Apri Word");
      open.type = "button";
      open.addEventListener("click", async () => {
        if (!window.confirm("Aprire questo documento con l’app predefinita di Windows? Apri solo file di cui ti fidi.")) return;
        try {
          await request(`/api/letters/${letter.id}/open`, { method: "POST" });
          showToast("Documento aperto nell’app predefinita.");
        } catch (error) {
          showToast(error, true);
        }
      });
      actions.append(open);
    }
    const download = element("button", "file-action", state.directoryHandle ? "Salva nella cartella" : "Scarica");
    download.type = "button";
    download.addEventListener("click", () => downloadLetter(letter).catch((error) => showToast(error, true)));
    actions.append(download);
    card.append(actions);
    list.append(card);
  }
}

function renderResearch(research) {
  const sources = byId("research-sources");
  sources.replaceChildren();
  const status = byId("research-status");
  const summary = byId("research-summary");
  const summaryText = String(research?.summary || "").trim();
  summary.textContent = summaryText;
  const records = Array.isArray(research?.sources) ? research.sources : [];
  summary.classList.toggle("hidden", !summaryText);
  const preview = byId("research-disclosure-preview");
  if (preview) {
    preview.textContent = compactDisclosurePreview(
      summaryText || records.length + " fonti"
    );
  }
  status.textContent = research?.state === "READY"
    ? `${research.cache_scope === "company" ? "Dalla cache aziendale" : "Ricerca consultata"}${(research.researched_at || research.updated_at) ? ` il ${formatDiagnosticTime(research.researched_at || research.updated_at)}` : ""}${research.search_calls ? ` · ${research.search_calls} ricerche web` : ""}`
    : research?.state === "UNAVAILABLE" ? "Ricerca web non disponibile o non attivata. Puoi riprovare dopo averla abilitata in Impostazioni." : "La ricerca online si attiva da Impostazioni e conserva le fonti con questo annuncio.";
  if (!records.length) return;
  const facts = Array.isArray(research?.facts) ? research.facts : [];
  if (facts.length) {
    sources.append(element("p", "research-source-heading", "Affermazioni e qualificazione"));
    for (const fact of facts.filter(fact => (fact.source_urls || []).some(url => records.some(source => source.url === url))).slice(0, 3)) {
      const item = element("article", "research-fact");
      item.append(element("strong", "", fact.kind === "documented_fact" ? "Fatto documentato" : "Interpretazione"));
      item.append(element("p", "", fact.claim || ""));
      for (const url of fact.source_urls || []) {
        const source = records.find((entry) => entry.url === url);
        if (!source || !/^https?:\/\//i.test(String(url))) continue;
        const link = element("a", "research-source-link", source.title || url);
        link.href = url;
        link.target = "_blank";
        link.rel = "noopener noreferrer";
        item.append(link);
      }
      sources.append(item);
    }
  }
  sources.append(element("p", "research-source-heading", "Fonti consultate"));
  for (const source of records) {
    if (!/^https?:\/\//i.test(String(source.url || ""))) continue;
    const link = element("a", "research-source-link", source.title || source.url);
    link.href = source.url;
    link.target = "_blank";
    link.rel = "noopener noreferrer";
    sources.append(link);
  }

}

function renderFeedbackHistory(history) {
  const list = byId("letter-feedback-history");
  list.replaceChildren();
  for (const item of history) {
    const row = element("p", "", `${item.rating}/10 · ${item.comment || "Nessun commento"} · ${shortDate(item.created_at)}`);
    list.append(row);
  }
}

async function submitLetterFeedback() {
  if (!state.selectedId) return;
  if (state.dirty) return showToast("Salva prima le modifiche manuali della cover letter.", true);
  const resultNode = byId("feedback-result");
  const button = byId("submit-letter-feedback");
  button.disabled = true;
  resultNode.textContent = "Salvo il commento in locale, senza chiamare l’AI…";
  try {
    const result = await request(`/api/jobs/${encodeURIComponent(state.selectedId)}/feedback`, {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ rating: Number(byId("letter-rating").value), comment: byId("letter-feedback-comment").value.trim() }),
    });
    state.detail.feedback = result.feedback || [];
    renderFeedbackHistory(state.detail.feedback);
    resultNode.textContent = `Commento salvato per la versione ${result.version}. Per richiedere una revisione usa “Rigenera analisi e lettera”.`;
    resultNode.className = "inline-feedback is-success";
  } catch (error) {
    resultNode.textContent = errorForDisplay(error);
    resultNode.className = "inline-feedback is-error";
    showToast(error, true);
  } finally { button.disabled = false; }
}

async function addCurrentLetterToKnowledge() {
  if (!state.selectedId) return;
  if (state.dirty) return showToast("Salva prima le modifiche manuali della cover letter.", true);
  if (state.knowledgeCount >= 10) return showToast("Cover Letter knowledge è piena: rimuovi o sostituisci un esempio.", true);
  if (!window.confirm("Aggiungere la cover letter attuale a Cover Letter knowledge come esempio di stile?")) return;
  const button = byId("add-letter-to-knowledge");
  button.disabled = true;
  byId("feedback-result").textContent = "Aggiungo l’esempio all’archivio locale…";
  try {
    await request(`/api/jobs/${encodeURIComponent(state.selectedId)}/knowledge`, { method: "POST" });
    await loadKnowledge();
    byId("feedback-result").textContent = "Cover letter aggiunta a Cover Letter knowledge. Gli agenti potranno usarla come esempio di stile.";
    byId("feedback-result").className = "inline-feedback is-success";
  } catch (error) {
    byId("feedback-result").textContent = errorForDisplay(error);
    byId("feedback-result").className = "inline-feedback is-error";
    showToast(error, true);
  } finally {
    button.disabled = false;
    if (state.detail) {
      const hasLetter = Boolean(String(state.detail.materials?.cover_letter_text || "").trim());
      byId("add-letter-to-knowledge").disabled = !hasLetter || state.knowledgeCount >= 10;
    }
  }
}

async function refreshCompanyResearch() {
  if (!state.selectedId) return;
  const button = byId("refresh-company-research");
  button.disabled = true;
  byId("research-status").textContent = "Ricerca fonti online…";
  try {
    const result = await request(`/api/jobs/${encodeURIComponent(state.selectedId)}/research`, { method: "POST" });
    state.detail.research = result.research;
    renderResearch(result.research);
    await request(`/api/jobs/${encodeURIComponent(state.selectedId)}/regenerate`, { method: "POST" });
    showToast("Fonti aggiornate; analisi e cover letter rigenerate per questo annuncio.");
    await refreshGenerationStatus();
  } catch (error) {
    byId("research-status").textContent = errorForDisplay(error);
    showToast(error, true);
  } finally { button.disabled = false; }
}

function updateCharacterCount() {
  byId("letter-char-count").textContent = `${byId("cover-letter-text").value.length.toLocaleString("it-IT")} caratteri`;
}

function markMaterialsDirty() {
  state.materialsRevision += 1;
  state.dirty = true;
  byId("export-letter-text").disabled = true;
  byId("export-letter-word").disabled = true;
  byId("materials-feedback").textContent = "Modifiche non salvate.";
  byId("materials-feedback").className = "inline-feedback";
}

async function saveMaterials() {
  if (!state.selectedId) return;
  const jobId = state.selectedId;
  const revision = state.materialsRevision;
  const button = byId("save-materials");
  button.disabled = true;
  const payload = {
    company_analysis: byId("company-analysis").value,
    role_analysis: state.detail?.materials?.role_analysis || "",
    personalization_notes: byId("personalization-notes").value,
    cover_letter_text: byId("cover-letter-text").value,
    agent_name: byId("agent-source").value,
  };
  try {
    await request(`/api/jobs/${jobId}/materials`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    if (state.selectedId !== jobId) return;
    if (state.materialsRevision !== revision) {
      byId("materials-feedback").textContent = "Versione precedente salvata; le nuove modifiche restano da salvare.";
      return;
    }
    state.dirty = false;
    byId("materials-feedback").textContent = "Modifiche salvate sul tuo PC.";
    byId("materials-feedback").className = "inline-feedback is-success";
    showToast("Analisi e cover letter salvate.");
    await selectJob(state.selectedId);
  } catch (error) {
    byId("materials-feedback").textContent = errorForDisplay(error);
    byId("materials-feedback").className = "inline-feedback is-error";
    showToast(error, true);
  } finally {
    button.disabled = false;
  }
}

async function exportEditedLetter(fileFormat = "pdf") {
  if (!state.selectedId || state.dirty) return showToast("Salva prima le modifiche al testo.", true);
  const button = byId(fileFormat === "pdf" ? "export-letter-text" : "export-letter-word");
  button.disabled = true;
  const jobId = state.selectedId;
  try {
    const response = await fetch("/api/jobs/" + encodeURIComponent(jobId) + "/documents", {
      method: "POST", cache: "no-store", headers: {"Content-Type": "application/json"},
      body: JSON.stringify({format: fileFormat}),
    });
    if (!response.ok) {
      const payload = await response.json().catch(() => ({}));
      throw new Error(payload.error || "Esportazione non riuscita; testo conservato.");
    }
    const blob = await response.blob();
    const job = state.detail?.job || state.jobs.find((item) => jobIdentity(item) === jobId) || {};
    const filename = jobDocumentBaseName(job) + "." + fileFormat;
    await deliverDocument(blob, filename, job);
    state.detail = await request("/api/jobs/" + encodeURIComponent(jobId));
    renderFiles(state.detail.cover_letters || []);
  } catch (error) {
    showToast(error, true);
  } finally {
    button.disabled = !state.detail?.materials?.cover_letter_text || state.dirty;
  }
}

async function uploadLetter(event) {
  event.preventDefault();
  const input = byId("letter-file");
  const file = input.files?.[0];
  if (!state.selectedId || !file) return;
  const button = byId("upload-button");
  button.disabled = true;
  button.textContent = "Archivio…";
  const form = new FormData();
  form.append("file", file, file.name);
  try {
    await request(`/api/jobs/${state.selectedId}/letters`, { method: "POST", body: form });
    input.value = "";
    byId("upload-button").textContent = "Archivia file";
    showToast("File archiviato nella cartella dell’annuncio.");
    state.detail = await request(`/api/jobs/${state.selectedId}`);
    renderFiles(state.detail.cover_letters || []);
    await loadJobs();
  } catch (error) {
    showToast(error, true);
  } finally {
    button.disabled = !input.files?.length;
    button.textContent = "Archivia file";
  }
}

async function startLinkedInSync() {
  const confirmed = window.confirm(
    "Controllerò i nuovi annunci recenti usando il profilo Chrome del progetto. La lettura si fermerà appena incontra un annuncio già archiviato. Non verranno inviate candidature e LinkedIn non verrà modificato. Continuare?"
  );
  if (!confirmed) return;
  const button = byId("sync-button");
  button.classList.add("is-busy");
  button.disabled = true;
  try {
    const stateUpdate = await request("/api/sync", { method: "POST", headers: { "Content-Type": "application/json" }, body: "{}" });
    showToast(stateUpdate.message || "Aggiornamento avviato.");
    await pollSyncStatus();
  } catch (error) {
    showToast(error, true);
  } finally {
    button.classList.remove("is-busy");
    button.disabled = false;
  }
}

async function pollSyncStatus() {
  const button = byId("sync-button");
  button.classList.add("is-busy");
  button.disabled = true;
  try {
    let result = await request("/api/sync/status");
    while (result.running) {
      button.textContent = "↻ Lettura in corso…";
      await new Promise((resolve) => setTimeout(resolve, 1500));
      result = await request("/api/sync/status");
    }
    button.textContent = "↻ Solo nuovi annunci";
    if (result.status === "complete") {
      const counts = result.counts || {};
      const inserted = Number(counts.inserted || 0);
      showToast(inserted
        ? `${result.message} Importati ${inserted} annunci nuovi; quelli già archiviati sono stati saltati.`
        : "Controllo completato: nessun annuncio nuovo. Quelli già archiviati sono stati saltati.");
      await loadJobs();
    } else if (result.status === "error") {
      showToast(`${result.message || "Aggiornamento non riuscito."}${result.request_id ? ` · ID diagnostica: ${result.request_id}` : ""}`, true);
    }
  } catch (error) {
    showToast(error, true);
  } finally {
    button.classList.remove("is-busy");
    button.disabled = false;
    button.textContent = "↻ Solo nuovi annunci";
  }
}

function openDirectoryDatabase() {
  return new Promise((resolve, reject) => {
    const opening = indexedDB.open(DIRECTORY_DB_NAME, 1);
    opening.onupgradeneeded = () => {
      if (!opening.result.objectStoreNames.contains(DIRECTORY_STORE_NAME)) {
        opening.result.createObjectStore(DIRECTORY_STORE_NAME);
      }
    };
    opening.onsuccess = () => resolve(opening.result);
    opening.onerror = () => reject(opening.error || new Error("Archivio impostazioni del browser non disponibile."));
  });
}

async function storeDirectoryHandle(handle) {
  const database = await openDirectoryDatabase();
  await new Promise((resolve, reject) => {
    const transaction = database.transaction(DIRECTORY_STORE_NAME, "readwrite");
    transaction.objectStore(DIRECTORY_STORE_NAME).put(handle, "cover-letter-destination");
    transaction.oncomplete = resolve;
    transaction.onerror = () => reject(transaction.error || new Error("Non riesco a salvare la cartella scelta."));
    transaction.onabort = () => reject(transaction.error || new Error("Salvataggio della cartella interrotto."));
  }).finally(() => database.close());
}

async function readDirectoryHandle() {
  const database = await openDirectoryDatabase();
  return new Promise((resolve, reject) => {
    const transaction = database.transaction(DIRECTORY_STORE_NAME, "readonly");
    const reading = transaction.objectStore(DIRECTORY_STORE_NAME).get("cover-letter-destination");
    reading.onsuccess = () => resolve(reading.result || null);
    reading.onerror = () => reject(reading.error || new Error("Non riesco a leggere la cartella salvata."));
    transaction.oncomplete = () => database.close();
    transaction.onerror = () => {
      database.close();
      reject(transaction.error || new Error("Lettura cartella non riuscita."));
    };
  });
}

async function removeDirectoryHandle() {
  const database = await openDirectoryDatabase();
  await new Promise((resolve, reject) => {
    const transaction = database.transaction(DIRECTORY_STORE_NAME, "readwrite");
    transaction.objectStore(DIRECTORY_STORE_NAME).delete("cover-letter-destination");
    transaction.oncomplete = resolve;
    transaction.onerror = () => reject(transaction.error || new Error("Non riesco a rimuovere la cartella."));
    transaction.onabort = () => reject(transaction.error || new Error("Rimozione della cartella interrotta."));
  }).finally(() => database.close());
}

function renderFolderDestination() {
  const status = byId("destination-folder-status");
  const clear = byId("clear-destination-folder");
  if (state.directoryHandle) {
    status.textContent = `Cartella selezionata: ${state.directoryHandle.name}`;
    clear.classList.remove("hidden");
  } else {
    status.textContent = "Nessuna cartella selezionata · i file useranno il download standard del browser.";
    clear.classList.add("hidden");
  }
  if (state.detail?.job) {
    const job = state.detail.job;
    if (byId("folder-hint")) byId("folder-hint").textContent = state.directoryHandle
    ? `Archivio interno: data/cover_letters/${jobIdentity(job)}/ · Download: ${state.directoryHandle.name}/[azienda - posizione - ID]/`
      : `Archivio interno: data/cover_letters/${jobIdentity(job)}/ · Per scegliere la cartella di download, apri Impostazioni.`;
    renderFiles(state.detail.cover_letters || []);
  }
}

async function chooseDestinationFolder() {
  if (!window.showDirectoryPicker) {
    showToast("Questo browser non supporta la selezione diretta della cartella. Prova con Chrome o Edge.", true);
    return;
  }
  try {
    const handle = await window.showDirectoryPicker({ id: "cover-letter-destination", mode: "readwrite" });
    const permission = await handle.requestPermission({ mode: "readwrite" });
    if (permission !== "granted") throw new Error("Accesso alla cartella non autorizzato. Selezionala di nuovo per concedere l’accesso.");
    await storeDirectoryHandle(handle);
    state.directoryHandle = handle;
    renderFolderDestination();
    showToast(`Destinazione impostata: ${handle.name}.`);
  } catch (error) {
    if (error.name !== "AbortError") showToast(error, true);
  }
}

function safeFolderName(value) {
  const cleaned = String(value || "annuncio")
    .normalize("NFKC")
    .replace(/[<>:"/\\|?*\u0000-\u001f]/g, "-")
    .replace(/\s+/g, " ")
    .replace(/[. ]+$/g, "")
    .trim()
    .slice(0, 90) || "annuncio";
  return /^(con|prn|aux|nul|com[1-9]|lpt[1-9])$/i.test(cleaned) ? `_${cleaned}` : cleaned;
}

function jobDocumentBaseName(job) {
  const idSuffix = jobIdentity(job) || "sconosciuto";
  const prefix = safeFolderName((job.company || "Azienda") + " - " + (job.title || "Annuncio"))
    .slice(0, Math.max(1, 88 - idSuffix.length))
    .replace(/[. ]+$/g, "");
  return safeFolderName(prefix + " - " + idSuffix);
}

function triggerBrowserDownload(blob, filename) {
  const url = URL.createObjectURL(blob);
  const anchor = element("a");
  anchor.href = url;
  anchor.download = filename;
  anchor.hidden = true;
  document.body.append(anchor);
  anchor.click();
  anchor.remove();
  setTimeout(() => URL.revokeObjectURL(url), 1_000);
}

async function uniqueFileName(directory, filename) {
  const dot = filename.lastIndexOf(".");
  const base = dot > 0 ? filename.slice(0, dot) : filename;
  const extension = dot > 0 ? filename.slice(dot) : "";
  let candidate = filename;
  let index = 2;
  while (true) {
    try {
      await directory.getFileHandle(candidate, { create: false });
      candidate = base + " (" + index++ + ")" + extension;
    } catch (error) {
      if (error?.name === "NotFoundError") return candidate;
      throw error;
    }
  }
}

async function deliverDocument(blob, filename, job) {
  if (!state.directoryHandle) {
    triggerBrowserDownload(blob, filename);
    showToast("Nessuna cartella diretta selezionata: file scaricato con il browser.");
    return "browser";
  }
  try {
    let permission = await state.directoryHandle.queryPermission({ mode: "readwrite" });
    if (permission !== "granted") permission = await state.directoryHandle.requestPermission({ mode: "readwrite" });
    if (permission !== "granted") throw new Error("Permesso cartella non concesso.");
    const folderName = jobDocumentBaseName(job);
    const jobFolder = await state.directoryHandle.getDirectoryHandle(folderName, { create: true });
    const uniqueName = await uniqueFileName(jobFolder, filename);
    const fileHandle = await jobFolder.getFileHandle(uniqueName, { create: true });
    const writer = await fileHandle.createWritable();
    await writer.write(blob);
    await writer.close();
    showToast("Cover letter salvata in " + state.directoryHandle.name + "/" + folderName + "/" + uniqueName + ".");
    return "directory";
  } catch (error) {
    triggerBrowserDownload(blob, filename);
    showToast("Salvataggio nella cartella selezionata non riuscito; file scaricato con il browser (" +
      (error.message || "errore di accesso") + ").", true);
    return "browser";
  }
}

async function downloadLetter(letter) {
  const response = await fetch("/api/letters/" + letter.id + "/file", { cache: "no-store" });
  if (!response.ok) {
    const payload = await response.json().catch(() => ({}));
    throw new Error(payload.error || "Non riesco a leggere il file cover letter.");
  }
  const blob = await response.blob();
  const format = String(letter.file_format).toLowerCase();
  const job = state.detail?.job || state.jobs.find((item) => jobIdentity(item) === state.selectedId) || {};
  const filename = jobDocumentBaseName(job) + "." + format;
  await deliverDocument(blob, filename, job);
}

function updateAIOverview(result) {
  const counts = result?.summary?.counts || {};
  const waiting = Number(counts.PENDING || 0);
  const running = Number(counts.RUNNING || 0);
  const ready = Number(counts.READY || 0) + Number(counts.REVIEW || 0);
  const errors = Number(counts.ERROR || 0);
  const missingLabels = {
    automazione_disattivata: "automazione disattivata", consenso_mancante: "consenso mancante",
    chiave_api_mancante: "chiave API mancante", cv_mancante: "CV mancante",
    contesto_mancante: "contesto candidato da compilare",
  };
  const missing = (result?.missing || []).map((item) => missingLabels[item] || item);
  const prefix = result?.ready ? "AI attiva" : `AI da configurare: ${missing.join(", ") || "controlla Impostazioni"}`;
  const lastSync = result?.last_linkedin_sync ? new Date(result.last_linkedin_sync) : null;
  const syncText = lastSync && !Number.isNaN(lastSync.valueOf())
    ? new Intl.DateTimeFormat("it-IT", { dateStyle: "short", timeStyle: "short" }).format(lastSync)
    : "mai";
  const usage = result?.summary?.usage || {};
  const daily = usage.today_by_operation || {};
  const apiSeconds = (Number(usage.duration_ms || 0) / 1_000).toLocaleString("it-IT", { maximumFractionDigits: 1 });
  byId("automation-overview").textContent = `${prefix} · ${ready} pronte · ${running} in corso · ${waiting} in attesa · ${errors} errori\nUltimo controllo LinkedIn: ${syncText}\nOggi: ${Number(usage.calls_today || 0)}/${Number(state.aiSettings?.max_daily_calls || 0)} chiamate totali · ${Number(daily.company_research || 0)} ricerche · ${Number(daily.feedback_revision || 0)} revisioni · agenti ${Number(daily.agent || 0)}/${Number(state.aiSettings?.max_daily_agent_calls || 0)}\nTotale: ${Number(usage.calls || 0)} chiamate · ${Number(usage.search_calls || 0)} ricerche web · ${Number(usage.input_tokens || 0).toLocaleString("it-IT")} token in ingresso (${Number(usage.cached_input_tokens || 0).toLocaleString("it-IT")} in cache) · ${Number(usage.output_tokens || 0).toLocaleString("it-IT")} in uscita · ${apiSeconds}s di attesa API cumulata`;
  byId("ai-connection-status").textContent = prefix;
  byId("ai-usage-today").textContent = `Uso oggi: totale ${usage.calls_today || 0}/${state.aiSettings?.max_daily_calls || 0} · ricerche ${daily.company_research || 0}/${state.aiSettings?.max_daily_research_calls || 0} · revisioni ${daily.feedback_revision || 0}/${state.aiSettings?.max_daily_revision_calls || 0} · agenti ${daily.agent || 0}/${state.aiSettings?.max_daily_agent_calls || 0}.`;
}

function formatUSD(value) {
  return new Intl.NumberFormat("en-US", { style: "currency", currency: "USD", minimumFractionDigits: 2, maximumFractionDigits: 4 }).format(Number(value || 0));
}

function formatEstimatedDuration(seconds) {
  const value = Math.max(0, Number(seconds || 0));
  if (value < 60) return "meno di 1 min";
  const minutes = Math.ceil(value / 60);
  if (minutes < 60) return `circa ${minutes} min`;
  const hours = Math.floor(minutes / 60);
  const remaining = minutes % 60;
  return remaining ? `circa ${hours} h ${remaining} min` : `circa ${hours} h`;
}

function renderBulkPreview(result) {
  const preview = result.preview;
  const estimate = result.estimate;
  const box = byId("bulk-regeneration-result");
  box.replaceChildren();
  box.classList.remove("hidden");
  const targetDescription = preview.only_new ? "annunci senza cover letter già salvata" : "annunci con cover letter già salvata";
  box.append(element("p", "bulk-result-main", `${preview.eligible_count} candidati selezionabili su ${preview.scope_candidate_count ?? preview.scope_cover_letter_count} ${targetDescription} nell’intervallo scelto. Archivio: ${preview.stored_cover_letter_count} cover letter già presenti.`));
  box.append(element("p", "", `Stima indicativa: ${formatUSD(estimate.low)}–${formatUSD(estimate.high)} (valore centrale ${formatUSD(estimate.expected)}), circa ${estimate.estimated_api_calls.toLocaleString("it-IT")} richieste AI${estimate.research_enabled ? ` + ${estimate.estimated_web_search_calls.toLocaleString("it-IT")} ricerche web` : ""}.`));
  const duration = estimate.duration_seconds || {};
  box.append(element("p", "", `Tempo indicativo per le chiamate AI: ${formatEstimatedDuration(duration.low)}–${formatEstimatedDuration(duration.high)} (valore centrale ${formatEstimatedDuration(duration.expected)}). ${duration.note || "Escluse le attese della coda."}`));
  const pricingNote = element("p", "settings-footnote", `Listino API standard consultato il ${estimate.price_source_checked}: `);
  const pricingLink = element("a", "", "tariffe OpenAI");
  pricingLink.href = "https://developers.openai.com/api/docs/pricing";
  pricingLink.target = "_blank";
  pricingLink.rel = "noopener noreferrer";
  pricingNote.append(pricingLink);
  box.append(pricingNote);
  const excluded = Object.entries(preview.excluded || {}).filter(([, count]) => Number(count) > 0)
    .map(([reason, count]) => `${count} ${reason.replaceAll("_", " ")}`);
  box.append(element("p", "", excluded.length ? `Esclusi: ${excluded.join(" · ")}.` : "Nessun annuncio escluso dal filtro."));
  box.append(element("p", "settings-footnote", `Stimate ${estimate.enabled_agents} fasi agente per annuncio${estimate.research_enabled ? ` e ${estimate.research_jobs} ricerche aziendali non già in cache` : ""}. Oggi: ${result.daily_limits.agents_used}/${result.daily_limits.agents} chiamate agenti · ${result.daily_limits.total_used}/${result.daily_limits.total} chiamate totali. La coda può impiegare più giorni e rispetta questi limiti.`));
  if (estimate.assumptions?.length) box.append(element("p", "settings-footnote", "Per alcune chiamate mancano medie storiche: la stima usa un’ipotesi conservativa."));
  if (!result.ready_for_ai) {
    const missingNames = {
      consenso_mancante: "consenso AI", chiave_api_mancante: "chiave API", cv_mancante: "CV",
      agents_md_da_correggere: "AGENTS.md valido", migrazione_agenti_richiesta: "migrazione agenti",
    };
    box.append(element("p", "inline-feedback is-error", `Prima della conferma completa: ${(result.missing || []).map((item) => missingNames[item] || item).join(", ")}.`));
  }
  byId("bulk-regeneration-actions").classList.remove("hidden");
  byId("confirm-bulk-regeneration").classList.toggle("hidden", !result.plan_token);
  byId("confirm-bulk-regeneration").disabled = !result.ready_for_ai || !result.plan_token || preview.eligible_count < 1;
  byId("cancel-bulk-regeneration").classList.add("hidden");
  byId("bulk-regeneration-feedback").textContent = result.plan_token
    ? `Stima valida fino alle ${new Date(result.expires_at).toLocaleTimeString("it-IT", { hour: "2-digit", minute: "2-digit" })}. Nessuna chiamata è stata avviata.`
    : "Non ci sono cover letter da mettere in coda.";
}

async function previewBulkRegeneration() {
  const button = byId("preview-bulk-regeneration");
  const feedback = byId("bulk-regeneration-feedback");
  button.disabled = true;
  feedback.textContent = "Calcolo una stima locale; non verranno chiamate API AI…";
  try {
    const rawScope = byId("bulk-regeneration-scope").value;
    const onlyNew = byId("bulk-regeneration-mode").value === "only_new";
    const result = await request("/api/ai/bulk/preview", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ scope_days: rawScope === "all" ? null : Number(rawScope), only_new: onlyNew }),
    });
    state.bulkPlanToken = result.plan_token;
    state.bulkBatchId = null;
    renderBulkPreview(result);
  } catch (error) {
    feedback.textContent = errorForDisplay(error);
    feedback.className = "inline-feedback is-error";
    showToast(error, true);
  } finally { button.disabled = false; }
}

function formatSavedSyncTime(value) {
  const date = new Date(value);
  if (Number.isNaN(date.valueOf())) return value || "Orario non disponibile";
  return new Intl.DateTimeFormat("it-IT", {
    day: "2-digit", month: "2-digit", year: "numeric",
    hour: "2-digit", minute: "2-digit",
  }).format(date);
}

function renderSavedSyncReport(report, lastSuccessfulAt = null) {
  const panel = byId("saved-sync-report");
  if (!panel) return;
  if (!report || typeof report !== "object") {
    panel.classList.add("hidden");
    return;
  }
  panel.classList.remove("hidden");
  const counts = report.counts || {};
  const events = Array.isArray(report.events) ? report.events : (Array.isArray(counts.events) ? counts.events : []);
  const countType = (type) => events.filter((item) => item?.type === type).length;
  const added = countType("added");
  const updated = countType("updated");
  const maintained = countType("maintained");
  const removed = countType("removed_from_saved");
  const notImported = countType("not_imported") + countType("suppressed_forgotten");
  const unreadable = Number(counts.unreadable_descriptions ?? report.unreadable?.length ?? 0);
  const missingTitle = Number(counts.skipped_missing_title ??
    report.skipped_missing_title?.length ?? 0);
  const withLetters = Number(counts.with_letters ?? counts.preserved_with_letter ?? 0);
  const files = Number(counts.preserved_files ?? 0);
  const start = formatSavedSyncTime(report.started_at);
  const end = formatSavedSyncTime(report.ended_at);
  const duration = report.duration_ms != null && Number.isFinite(Number(report.duration_ms))
    ? `${(Number(report.duration_ms) / 1000).toFixed(1)} s` : "in corso";
  byId("saved-sync-report-title").textContent =
    `${report.status === "complete" ? "Ultimo controllo completato" : report.status === "running" ? "Controllo in corso" : "Ultimo controllo parziale o non riuscito"} · ${start}${report.ended_at ? ` – ${end}` : ""} · ${duration}`;
  const hasChanges = added + updated + removed > 0;
  const unchanged = report.status === "complete" && !hasChanges;
  const prefix = unchanged ? `Nessuna modifica · controllo completato il ${end}` :
    `${report.status === "partial" ? "Confronto parziale" : report.status === "error" ? "Controllo non riuscito" : "Esito"}`;
  const noHideNote = report.membership_verified === false
    ? " · elenco non verificato, nessun annuncio nascosto" : "";
  byId("saved-sync-report-summary").textContent =
    `${prefix} · aggiunti ${added} · aggiornati ${updated} · mantenuti ${maintained} · rimossi dalla lista ${removed} · con cover letter ${withLetters} · file conservati ${files} · descrizioni non leggibili ${unreadable} · titoli non recuperati ${missingTitle} · non importati ${notImported}${noHideNote}${report.status === "complete" ? "" : report.message ? ` · ${report.message}` : ""}`;
  byId("saved-sync-report-last-success").textContent = lastSuccessfulAt
    ? `Ultima sincronizzazione completa riuscita: ${formatSavedSyncTime(lastSuccessfulAt)}`
    : "Nessuna sincronizzazione completa riuscita registrata.";

  const readDetails = byId("saved-sync-read-details");
  const readList = byId("saved-sync-read-events");
  const readOutcomes = Array.isArray(report.read_outcomes) ? report.read_outcomes
    : Array.isArray(counts.read_outcomes) ? counts.read_outcomes : [];
  readList.replaceChildren();
  readDetails.classList.toggle("hidden", !readOutcomes.length);
  byId("saved-sync-read-details-title").textContent = "Esito e tempo per annuncio (" + readOutcomes.length + ")";
  for (const outcome of readOutcomes) {
    const label = outcome.status === "read" ? "Dettaglio letto"
      : outcome.status === "skipped_missing_title" ? "Titolo non recuperato" : "Dettaglio non letto";
    const title = outcome.title || ("ID " + (outcome.linkedin_job_id || "sconosciuto"));
    const elapsed = Number.isFinite(Number(outcome.duration_ms))
      ? " · " + (Number(outcome.duration_ms) / 1000).toFixed(2) + " s" : "";
    const attempts = Number(outcome.attempts || 0);
    const attemptLabel = attempts ? " · " + attempts + " tentativi" : "";
    const opened = (outcome.page_opened ? "pagina aperta" : "pagina non aperta") +
      (outcome.detail_opened ? ", dettaglio aperto" : "");
    const descriptionStatus = outcome.description_read_status === "complete" ? "descrizione completa"
      : outcome.description_read_status === "partial" ? "descrizione parziale" : "descrizione fallita";
    const imported = outcome.import_status ? " · importazione: " + outcome.import_status : "";
    const reason = outcome.reason ? " · " + String(outcome.reason).slice(0, 100) : "";
    readList.append(element("li", "", label + ": " + title + " · " + opened + " · " +
      descriptionStatus + imported + elapsed + attemptLabel + reason));
  }
  const details = byId("saved-sync-report-details");
  const list = byId("saved-sync-report-events");
  const changes = events.filter((item) => ["added", "updated", "removed_from_saved", "not_imported", "suppressed_forgotten"].includes(item?.type));
  const existingUnreadableIds = new Set(events
    .filter((item) => item?.type === "maintained" && item.details_readable === false)
    .map((item) => String(item.linkedin_job_id)));
  for (const item of (Array.isArray(report.unreadable) ? report.unreadable : [])) {
    if (existingUnreadableIds.has(String(item.linkedin_job_id))) changes.push({ ...item, type: "unreadable" });
  }
  list.replaceChildren();
  if (!changes.length) {
    details.classList.add("hidden");
    return;
  }
  details.classList.remove("hidden");
  byId("saved-sync-report-details-title").textContent = `Dettaglio modifiche (${changes.length})`;
  for (const item of changes) {
    const label = {
      added: "Aggiunto", updated: "Aggiornato",
      removed_from_saved: "Non più salvato", not_imported: "Non importato",
      suppressed_forgotten: "Escluso dall’importazione",
      unreadable: "Dettaglio non letto",
    }[item.type];
    const identity = [item.title || `ID ${item.linkedin_job_id || "sconosciuto"}`, item.company]
      .filter(Boolean).join(" — ");
    const fields = Array.isArray(item.changed_fields) && item.changed_fields.length
      ? ` · ${item.changed_fields.join(", ")}` : "";
    const reason = item.type === "not_imported" && item.reason ? ` · ${String(item.reason).slice(0, 120)}` : "";
    list.append(element("li", "", `${label}: ${identity}${fields}${reason} · ${formatSavedSyncTime(item.timestamp)}`));
  }
}

async function loadSavedSyncReport() {
  try {
    const result = await request("/api/sync/status");
    renderSavedSyncReport(result.last_report || result.report, result.last_successful_full_sync_at);
  } catch (_) {
    // A report read failure should not prevent the dashboard from loading.
  }
}

async function recoverSavedJobs() {
  const button = byId("recover-saved-jobs");
  const feedback = byId("saved-recovery-feedback");
  const confirmed = window.confirm(
    "Scansiono l’elenco Salvati completo (fino a 100 posizioni). Le nuove posizioni verranno importate senza avviare l’AI; quelle non più salvate saranno nascoste, conservando cover letter e analisi già presenti. Se il contatore LinkedIn non coincide, ripeterò la scansione: userò gli ID solo se due letture complete coincidono; se il risultato resta incerto, conserverò gli annunci locali. Continuare?"
  );
  if (!confirmed) return;
  button.disabled = true;
  feedback.className = "inline-feedback";
  feedback.textContent = "Lettura completa dell’elenco Salvati in corso…";
  try {
    let result = await request("/api/sync/recover", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({}),
    });
    while (result.running) {
      feedback.textContent = "Sto verificando l’elenco Salvati completo; l’AI non verrà avviata…";
      await new Promise((resolve) => setTimeout(resolve, 1500));
      result = await request("/api/sync/status");
    }
    renderSavedSyncReport(result.last_report || result.report, result.last_successful_full_sync_at);
    feedback.textContent = result.message || "Confronto terminato. Consulta il report qui sotto.";
    feedback.className = result.status === "complete"
      ? "inline-feedback is-success"
      : result.status === "partial" ? "inline-feedback is-warning" : "inline-feedback is-error";
    if (result.status !== "complete" && result.request_id) feedback.textContent += ` · ID diagnostica: ${result.request_id}`;
    if (result.status === "complete") {
      await loadJobs();
    } else if (result.status === "partial") {
      const counts = result.counts || {};
      if (Number.isInteger(counts.discovered)) await loadJobs();
    }
  } catch (error) {
    feedback.textContent = errorForDisplay(error);
    feedback.className = "inline-feedback is-error";
    showToast(error, true);
  } finally {
    button.disabled = false;
  }
}

async function confirmBulkRegeneration() {
  if (!state.bulkPlanToken) return;
  const estimateText = byId("bulk-regeneration-result").querySelector(".bulk-result-main")?.textContent || "";
  if (!window.confirm(`${estimateText}\n\nLa stima è indicativa e non garantisce l’addebito effettivo. Sei sicuro di voler procedere?`)) return;
  const button = byId("confirm-bulk-regeneration");
  button.disabled = true;
  byId("bulk-regeneration-feedback").textContent = "Inserisco le cover letter selezionate nella coda…";
  try {
    const batch = await request("/api/ai/bulk/confirm", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ plan_token: state.bulkPlanToken }),
    });
    state.bulkPlanToken = null;
    state.bulkBatchId = batch.batch_id;
    byId("confirm-bulk-regeneration").classList.add("hidden");
    byId("cancel-bulk-regeneration").classList.remove("hidden");
    await refreshBulkRegeneration();
    clearInterval(state.bulkPollTimer);
    state.bulkPollTimer = window.setInterval(() => refreshBulkRegeneration().catch(() => {}), 2500);
    showToast("Rigenerazione confermata: la coda rispetterà i limiti giornalieri.");
  } catch (error) {
    button.disabled = false;
    byId("bulk-regeneration-feedback").textContent = errorForDisplay(error);
    byId("bulk-regeneration-feedback").className = "inline-feedback is-error";
    showToast(error, true);
  }
}

async function refreshBulkRegeneration() {
  if (!state.bulkBatchId) return;
  const batch = await request(`/api/ai/bulk/${encodeURIComponent(state.bulkBatchId)}`);
  const counts = batch.counts || {};
  const totalDone = Number(counts.completed || 0) + Number(counts.failed || 0) + Number(counts.cancelled || 0);
  byId("bulk-regeneration-feedback").textContent = batch.complete
    ? `Completato: ${counts.completed || 0} pronte · ${counts.failed || 0} errori · ${counts.cancelled || 0} annullate.`
    : `In corso: ${totalDone}/${batch.count} concluse · ${counts.running || 0} in elaborazione · ${counts.queued || 0} in coda.`;
  byId("cancel-bulk-regeneration").classList.toggle("hidden", batch.complete || !counts.queued);
  byId("cancel-bulk-regeneration").disabled = batch.complete || !counts.queued;
  if (batch.complete) clearInterval(state.bulkPollTimer);
}

async function cancelBulkRegeneration() {
  if (!state.bulkBatchId || !window.confirm("Annullare le lettere non ancora avviate? Quelle già in elaborazione termineranno.")) return;
  const button = byId("cancel-bulk-regeneration");
  button.disabled = true;
  try {
    await request(`/api/ai/bulk/${encodeURIComponent(state.bulkBatchId)}/cancel`, { method: "POST" });
    await refreshBulkRegeneration();
  } catch (error) {
    button.disabled = false;
    byId("bulk-regeneration-feedback").textContent = errorForDisplay(error);
    byId("bulk-regeneration-feedback").className = "inline-feedback is-error";
  }
}

function renderAgentEditors() {
  const list = byId("ai-agent-list");
  list.replaceChildren();
  if (!state.aiAgents.length) return;
  state.aiAgents.forEach((agent, index) => {
    const card = element("article", "agent-editor-card");
    card.append(element("p", "eyebrow", `AGENTE ${index + 1} · ${agent.enabled ? "ATTIVO" : "DISATTIVATO"}`));
    const fields = element("div", "agent-editor-fields");
    for (const [field, labelText, limit] of [["name", "Nome", 80], ["phase", "Fase", 48]]) {
      const label = element("label", "", labelText);
      const input = element("input", "text-input");
      input.type = "text";
      input.maxLength = limit;
      input.value = agent[field];
      input.addEventListener("input", () => { agent[field] = input.value; });
      label.append(input);
      fields.append(label);
    }
    const modelFieldLabel = element("label", "", "Modello per questo agente");
    const modelSelect = element("select", "text-input");
    const inheritModel = element("option", "", "Usa il modello principale (Impostazioni AI)");
    inheritModel.value = "";
    modelSelect.append(inheritModel);
    for (const model of state.aiModels) {
      const option = element("option", "", modelLabel(model));
      option.value = model;
      modelSelect.append(option);
    }
    modelSelect.value = agent.model || "";
    modelSelect.addEventListener("change", () => { agent.model = modelSelect.value || null; });
    modelFieldLabel.append(modelSelect);
    fields.append(modelFieldLabel);
    card.append(fields);
    card.append(element("p", "agent-summary", agent.summary || "La sintesi deriva automaticamente da AGENTS.md."));
    const details = element("details", "agent-instructions-details");
    const summary = element("summary", "", "Visualizza e modifica istruzioni complete");
    details.append(summary);
    const instructionsLabel = element("label", "field-label", "Istruzioni dell’agente in AGENTS.md");
    const instructions = element("textarea");
    instructions.maxLength = 30000;
    instructions.value = agent.instructions;
    instructions.addEventListener("input", () => { agent.instructions = instructions.value; });
    instructionsLabel.append(instructions);
    details.append(instructionsLabel);
    card.append(details);
    const controls = element("div", "agent-editor-controls");
    const enabledLabel = element("label", "ai-check", "Attivo");
    const enabled = element("input");
    enabled.type = "checkbox";
    enabled.checked = Boolean(agent.enabled);
    enabled.addEventListener("change", () => { agent.enabled = enabled.checked; renderAgentEditors(); });
    enabledLabel.prepend(enabled);
    controls.append(enabledLabel);
    for (const [title, action, blocked] of [
      ["↑", () => swapAgents(index, index - 1), index === 0],
      ["↓", () => swapAgents(index, index + 1), index === state.aiAgents.length - 1],
      ["Rimuovi", () => { if (state.aiAgents.length <= 1) return showToast("Deve restare almeno un agente.", true); state.aiAgents.splice(index, 1); renderAgentEditors(); }, false],
    ]) {
      const button = element("button", "button button-quiet", title);
      button.type = "button";
      button.disabled = blocked;
      button.addEventListener("click", action);
      controls.append(button);
    }
    card.append(controls);
    list.append(card);
  });
}

function swapAgents(from, to) {
  const agents = state.aiAgents;
  [agents[from], agents[to]] = [agents[to], agents[from]];
  renderAgentEditors();
}

function renderMigration(migration) {
  const panel = byId("agent-migration");
  panel.classList.toggle("hidden", !migration?.required);
  for (const id of ["save-ai-settings", "save-ai-agents", "save-agent-markdown"])
    byId(id).disabled = Boolean(migration?.required);
  const summary = byId("migration-summary");
  summary.replaceChildren();
  if (!migration?.required) return;
  summary.append(element("p", "migration-conflict", migration.conflict
    ? "Rilevate differenze tra le due versioni: scegli quale mantenere. Entrambe verranno copiate prima della migrazione."
    : "Le definizioni coincidono: scegli quale conservare come base prima di rimuovere la duplicazione."));
  if (migration.legacy_error) summary.append(element("p", "inline-feedback is-error",
    `La vecchia lista non è leggibile (${migration.legacy_error}); puoi mantenere AGENTS.md e recuperare la copia di sicurezza.`));
  if (!migration.markdown_valid) summary.append(element("p", "inline-feedback is-error",
    "AGENTS.md non è ancora analizzabile. Dopo la migrazione correggi il file: l’automazione resterà ferma finché non sarà valido."));
  byId("use-saved-agents").disabled = Boolean(migration.legacy_error);
  for (const [label, agents] of [["AGENTS.md", migration.markdown_agents], ["Impostazioni precedenti", migration.legacy_agents]]) {
    const row = element("p", "", `${label}: ${(agents || []).map((agent) => agent.name).join(" · ") || "nessun agente leggibile"}`);
    summary.append(row);
  }
}

function renderLearning(history) {
  const list = byId("agent-learning-history");
  list.replaceChildren();
  if (!history?.length) return;
  list.append(element("h4", "", "Regole apprese (annullabili)"));
  for (const item of history) {
    const row = element("div", "learning-rule-row");
    row.append(element("p", "", `${item.agent_name}: ${item.rule}`));
    const undo = element("button", "button button-quiet", "Annulla regola");
    undo.type = "button";
    undo.addEventListener("click", async () => {
      if (!window.confirm("Rimuovere questa regola da AGENTS.md? La copia precedente resta nell’archivio locale.")) return;
      try { await request(`/api/agents/learning/${item.id}/undo`, { method: "DELETE" }); await loadAISettings(); await loadAgentInstructions(); }
      catch (error) { showToast(error, true); }
    });
    row.append(undo);
    list.append(row);
  }
}

async function loadAISettings() {
  try {
    const result = await request("/api/ai/settings");
    state.aiSettings = result.settings;
    state.aiAgents = result.agents || [];
    state.aiModels = result.models || [];
    state.aiKeyConfigured = result.key_configured;
    const modelSelect = byId("ai-model");
    modelSelect.replaceChildren();
    for (const model of result.models) {
      const option = element("option", "", modelLabel(model));
      option.value = model;
      modelSelect.append(option);
    }
    modelSelect.value = result.settings.model;
    byId("ai-max-per-cycle").value = result.settings.max_per_cycle;
    byId("ai-max-auto-job-age-days").value = result.settings.max_auto_job_age_days;
    byId("ai-max-daily-calls").value = result.settings.max_daily_calls;
    byId("ai-max-daily-agent-calls").value = result.settings.max_daily_agent_calls;
    byId("ai-max-daily-research-calls").value = result.settings.max_daily_research_calls;
    byId("ai-max-daily-revision-calls").value = result.settings.max_daily_revision_calls;
    byId("ai-consent").checked = result.settings.consent;
    byId("ai-enabled").checked = result.settings.enabled;
    byId("ai-web-research").checked = result.settings.web_research_enabled;
    byId("ai-key-feedback").textContent = result.key_configured
      ? `Chiave presente (${result.key_source === "ambiente" ? "variabile d’ambiente" : "credenziali di sistema"}). Non viene mostrata nella pagina.`
      : "Nessuna chiave configurata. Inseriscila sopra o usa la variabile d’ambiente indicata nel README.";
    renderAgentEditors();
    renderMigration(result.agent_migration);
    renderLearning(result.learning);
    updateAIOverview(result.status);
  } catch (error) {
    byId("ai-connection-status").textContent = errorForDisplay(error);
    showToast(error, true);
  }
}

function modelLabel(model) {
  const labels = {"gpt-5.6-luna": "GPT-5.6 Luna", "gpt-4.1-nano": "GPT-4.1 nano",
    "gpt-4o-mini": "GPT-4o mini", "gpt-4.1-mini": "GPT-4.1 mini", "gpt-5-mini": "GPT-5 mini"};
  return labels[model] || model;
}

async function saveAISettings() {
  if (!state.aiSettings) return;
  const next = {
    provider: "openai", model: byId("ai-model").value,
    enabled: byId("ai-enabled").checked, consent: byId("ai-consent").checked,
    max_per_cycle: Number(byId("ai-max-per-cycle").value),
    max_auto_job_age_days: Number(byId("ai-max-auto-job-age-days").value),
    web_research_enabled: byId("ai-web-research").checked,
    max_daily_calls: Number(byId("ai-max-daily-calls").value),
    max_daily_agent_calls: Number(byId("ai-max-daily-agent-calls").value),
    max_daily_research_calls: Number(byId("ai-max-daily-research-calls").value),
    max_daily_revision_calls: Number(byId("ai-max-daily-revision-calls").value),
  };
  const button = byId("save-ai-settings");
  button.disabled = true;
  try {
    const result = await request("/api/ai/settings", {
      method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(next),
    });
    state.aiSettings = result.settings;
    updateAIOverview(result.status);
    byId("ai-settings-feedback").textContent = "Impostazioni operative salvate su questo PC.";
    byId("ai-settings-feedback").className = "inline-feedback is-success";
    showToast("Impostazioni AI salvate.");
    await refreshGenerationStatus();
  } catch (error) {
    byId("ai-settings-feedback").textContent = errorForDisplay(error);
    byId("ai-settings-feedback").className = "inline-feedback is-error";
    showToast(error, true);
  } finally {
    button.disabled = false;
  }
}

async function saveAIKey() {
  const input = byId("ai-api-key");
  const key = input.value.trim();
  input.value = "";
  if (!key) return showToast("Inserisci la chiave API.", true);
  try {
    await request("/api/ai/key", { method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ api_key: key }) });
    showToast("Chiave salvata nelle credenziali di sistema.");
    await loadAISettings();
  } catch (error) {
    showToast(error, true);
  }
}

async function verifyAIKey() {
  try {
    await request("/api/ai/key/verify", { method: "POST" });
    byId("ai-key-feedback").textContent = "Connessione verificata per il modello selezionato e salvato.";
    showToast("Connessione AI verificata.");
  } catch (error) {
    byId("ai-key-feedback").textContent = errorForDisplay(error);
    showToast(error, true);
  }
}

async function deleteAIKey() {
  if (!window.confirm("Rimuovere la chiave API salvata su questo PC e disattivare l’automazione?")) return;
  try {
    await request("/api/ai/key", { method: "DELETE" });
    showToast("Chiave rimossa e automazione disattivata.");
    await loadAISettings();
  } catch (error) {
    showToast(error, true);
  }
}

async function regenerateJob() {
  const jobId = state.selectedId;
  if (!jobId) return;
  if (state.dirty) return showToast("Salva o scarta prima le modifiche a questa lettera.", true);
  const button = byId("regenerate-job");
  button.disabled = true;
  try {
    await request(`/api/jobs/${encodeURIComponent(jobId)}/regenerate`, { method: "POST" });
    showToast("Rigenerazione avviata solo per questa posizione.");
    await refreshGenerationStatus();
  } catch (error) {
    showToast(error, true);
    if (error?.message?.includes("Configura")) byId("settings-dialog").showModal();
  } finally {
    if (state.selectedId === jobId && state.detail?.generation?.state !== "RUNNING") button.disabled = false;
  }
}

let lastGenerationSignature = "";
async function refreshGenerationStatus() {
  const result = await request("/api/ai/status");
  updateAIOverview(result);
  const signature = JSON.stringify(result.summary);
  if (signature === lastGenerationSignature && !Number(result.summary?.counts?.RUNNING || 0)) return;
  lastGenerationSignature = signature;
  const params = new URLSearchParams();
  const query = byId("search-input").value.trim();
  const status = byId("status-filter").value;
  if (query) params.set("q", query);
  if (status) params.set("status", status);
  const jobs = await request(`/api/jobs?${params}`);
  state.jobs = jobs.jobs;
  renderJobs();
  if (!state.selectedId) return;
  const selected = state.jobs.find((job) => jobIdentity(job) === state.selectedId);
  if (!selected) return;
  if (state.dirty) {
    state.detail.generation = selected.generation;
    byId("generation-detail-status").textContent = displayGeneration(selected.generation);
  } else {
    const jobId = state.selectedId;
    const detailRequest = state.detailRequest;
    const detail = await request(`/api/jobs/${encodeURIComponent(jobId)}`);
    if (state.selectedId !== jobId || state.detailRequest !== detailRequest) return;
    state.detail = detail;
    renderDetail(false);
  }
}

async function loadAgentInstructions() {
  const button = byId("save-agent-markdown");
  button.disabled = true;
  try {
    const result = await request("/api/settings/agent-instructions");
    byId("agent-instructions-markdown").value = result.instructions || "";
    byId("agent-markdown-feedback").textContent = `File locale: ${result.path} · ${result.instructions.length.toLocaleString("it-IT")} caratteri.`;
    byId("agent-markdown-feedback").className = "inline-feedback";
  } catch (error) {
    byId("agent-markdown-feedback").textContent = errorForDisplay(error);
    byId("agent-markdown-feedback").className = "inline-feedback is-error";
    showToast(error, true);
  } finally {
    button.disabled = false;
  }
}

async function loadCandidateContext() {
  const button = byId("save-candidate-context");
  button.disabled = true;
  try {
    const result = await request("/api/settings/candidate-context");
    byId("candidate-context-markdown").value = result.context || "";
    byId("candidate-context-feedback").textContent = `File locale: ${result.path} · ${result.context.length.toLocaleString("it-IT")} caratteri. Non registrato nei log.`;
    byId("candidate-context-feedback").className = "inline-feedback";
  } catch (error) {
    byId("candidate-context-feedback").textContent = errorForDisplay(error);
    byId("candidate-context-feedback").className = "inline-feedback is-error";
    showToast(error, true);
  } finally {
    button.disabled = false;
  }
}

async function saveAgentInstructions() {
  const button = byId("save-agent-markdown");
  button.disabled = true;
  try {
    const saved = await request("/api/settings/agent-instructions", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ instructions: byId("agent-instructions-markdown").value }),
    });
    byId("agent-instructions-markdown").value = saved.instructions;
    state.aiAgents = saved.agents || state.aiAgents;
    renderAgentEditors();
    byId("agent-markdown-feedback").textContent = `Salvato in ${saved.path}. Le lettere esistenti sono contrassegnate come da aggiornare.`;
    byId("agent-markdown-feedback").className = "inline-feedback is-success";
    showToast("File Markdown degli agenti salvato.");
  } catch (error) {
    byId("agent-markdown-feedback").textContent = errorForDisplay(error);
    byId("agent-markdown-feedback").className = "inline-feedback is-error";
    showToast(error, true);
  } finally {
    button.disabled = false;
  }
}

async function saveAgents() {
  const button = byId("save-ai-agents");
  button.disabled = true;
  try {
    const saved = await request("/api/settings/agents", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ agents: state.aiAgents }),
    });
    state.aiAgents = saved.agents || [];
    byId("agent-instructions-markdown").value = saved.instructions;
    renderAgentEditors();
    byId("agent-markdown-feedback").textContent = "Agenti salvati in AGENTS.md; le candidature non ancora aggiornate verranno rigenerate.";
    byId("agent-markdown-feedback").className = "inline-feedback is-success";
    showToast("Agenti aggiornati in AGENTS.md.");
    await refreshGenerationStatus();
  } catch (error) {
    byId("agent-markdown-feedback").textContent = errorForDisplay(error);
    byId("agent-markdown-feedback").className = "inline-feedback is-error";
    showToast(error, true);
  } finally { button.disabled = false; }
}

async function saveCandidateContext() {
  const button = byId("save-candidate-context");
  button.disabled = true;
  try {
    const saved = await request("/api/settings/candidate-context", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ context: byId("candidate-context-markdown").value }),
    });
    byId("candidate-context-markdown").value = saved.context;
    byId("candidate-context-feedback").textContent = `Salvato in ${saved.path}. Verrà incluso nei prossimi prompt.`;
    byId("candidate-context-feedback").className = "inline-feedback is-success";
    showToast("Contesto personale salvato sul PC.");
  } catch (error) {
    byId("candidate-context-feedback").textContent = errorForDisplay(error);
    byId("candidate-context-feedback").className = "inline-feedback is-error";
    showToast(error, true);
  } finally {
    button.disabled = false;
  }
}

async function generateCandidateContextDraft() {
  const button = byId("generate-context-draft");
  button.disabled = true;
  byId("candidate-context-feedback").textContent = "Creo una bozza dal CV; il file esistente non verrà sovrascritto.";
  try {
    const result = await request("/api/profile/context/draft", { method: "POST" });
    byId("candidate-context-draft").value = result.context_markdown;
    byId("candidate-context-diff").textContent = contextDiff(
      byId("candidate-context-markdown").value, result.context_markdown,
    );
    byId("candidate-context-diff").classList.remove("hidden");
    const questionFields = byId("candidate-context-question-fields");
    questionFields.replaceChildren();
    for (const question of (result.open_questions || []).slice(0, 8)) {
      const label = element("label", "field-label", question);
      const input = element("input", "text-input");
      input.maxLength = 300;
      input.placeholder = "Facoltativo: puoi lasciare vuoto";
      input.dataset.contextAnswer = question;
      label.append(input);
      questionFields.append(label);
    }
    for (const [id, items] of [["candidate-context-facts", result.cv_facts],
                                ["candidate-context-inferences", result.inferences]]) {
      const list = byId(id);
      list.replaceChildren();
      for (const item of (items || []).slice(0, 30)) list.append(element("li", "", item));
      if (!items?.length) list.append(element("li", "", "Nessuna voce rilevata."));
    }
    byId("candidate-context-questions").textContent = (result.evidence_notes || []).join(" · ")
      || "Verifica nomi, date, responsabilità e informazioni personali. Le domande qui sotto sono facoltative.";
    byId("context-draft-panel").classList.remove("hidden");
    byId("candidate-context-feedback").textContent = "Bozza pronta: verifica e scegli tu se copiarla nel contesto.";
  } catch (error) {
    byId("candidate-context-feedback").textContent = errorForDisplay(error);
    byId("candidate-context-feedback").className = "inline-feedback is-error";
    showToast(error, true);
  } finally { button.disabled = false; }
}

function contextDiff(before, after) {
  const oldLines = String(before || "").split(/\r?\n/);
  const newLines = String(after || "").split(/\r?\n/);
  const oldSet = new Set(oldLines);
  const newSet = new Set(newLines);
  const removed = oldLines.filter((line) => line.trim() && !newSet.has(line));
  const added = newLines.filter((line) => line.trim() && !oldSet.has(line));
  if (!removed.length && !added.length) return "Nessuna differenza testuale rispetto al contesto salvato.";
  return ["RIMOSSO (controlla prima di accettare):", ...removed.map((line) => `− ${line}`),
    "AGGIUNTO:", ...added.map((line) => `+ ${line}`)].join("\n");
}

async function showCVTextPreview() {
  const target = byId("cv-text-preview");
  const button = byId("load-cv-preview");
  button.disabled = true;
  try {
    const result = await request("/api/profile/cv/text");
    target.textContent = result.text || "Nessun testo estratto.";
  } catch (error) { target.textContent = errorForDisplay(error); }
  finally { button.disabled = false; }
}

async function importCandidateContext() {
  const file = byId("context-file").files?.[0];
  if (!file) return;
  const form = new FormData();
  form.append("file", file, file.name);
  try {
    const result = await request("/api/profile/context/import", { method: "POST", body: form });
    byId("candidate-context-draft").value = result.context;
    byId("candidate-context-questions").textContent = `Importato da ${result.filename}. Rivedi il contenuto prima di copiarlo nel contesto salvato.`;
    byId("context-draft-panel").classList.remove("hidden");
    byId("context-file").value = "";
  } catch (error) { showToast(error, true); }
}

async function loadKnowledge() {
  try {
    const result = await request("/api/knowledge");
    const maxFiles = Number(result.max_files || 10);
    state.knowledgeCount = result.files.length;
    const list = byId("knowledge-list");
    list.replaceChildren();
    byId("upload-knowledge").disabled = !byId("knowledge-file").files?.length || result.files.length >= maxFiles;
    byId("knowledge-feedback").textContent = `${result.files.length}/${maxFiles} esempi archiviati localmente.`;
    byId("knowledge-archive-summary").textContent = `${result.files.length}/${maxFiles} esempi archiviati`;
    for (const item of result.files) {
      const card = element("details", "knowledge-item");
      const summary = element("summary", "", `${item.filename} · ${String(item.file_format).toUpperCase()}`);
      card.append(summary);
      const actions = element("div", "knowledge-actions");
      const replaceInput = element("input", "sr-only");
      replaceInput.type = "file";
      replaceInput.accept = ".pdf,.docx,.txt,.md,application/pdf,application/vnd.openxmlformats-officedocument.wordprocessingml.document,text/plain,text/markdown";
      const replace = element("button", "button button-quiet", "Sostituisci file");
      const replaceFilename = element("span", "file-selection-status", "Nessun file selezionato.");
      replace.type = "button";
      replace.addEventListener("click", () => replaceInput.click());
      replaceInput.addEventListener("change", () => {
        const file = replaceInput.files?.[0];
        replaceFilename.textContent = file ? `File selezionato: ${file.name}` : "Nessun file selezionato.";
        if (file) uploadKnowledge(item.id, file);
      });
      const preview = element("button", "button button-quiet", "Leggi testo estratto");
      preview.type = "button";
      preview.addEventListener("click", async () => {
        try {
          let text = card.querySelector("pre");
          if (!text) {
            const detail = await request(`/api/knowledge/${item.id}`);
            text = element("pre", "knowledge-preview", detail.extracted_text || "Nessun testo leggibile.");
            card.append(text);
          }
        } catch (error) { showToast(error, true); }
      });
      const remove = element("button", "button button-quiet", "Rimuovi");
      remove.type = "button";
      remove.addEventListener("click", async () => {
        if (!window.confirm(`Rimuovere l’esempio “${item.filename}”?`)) return;
        try { await request(`/api/knowledge/${item.id}`, { method: "DELETE" }); await loadKnowledge(); }
        catch (error) { showToast(error, true); }
      });
      actions.append(replace, replaceInput, replaceFilename, preview, remove);
      card.append(actions);
      list.append(card);
    }
    if (state.detail?.materials) {
      const hasLetter = Boolean(String(state.detail.materials.cover_letter_text || "").trim());
      byId("add-letter-to-knowledge").disabled = !hasLetter || state.knowledgeCount >= maxFiles;
    }
  } catch (error) { byId("knowledge-feedback").textContent = errorForDisplay(error); }
}

async function uploadKnowledge(replacementId = null, replacementFile = null) {
  const file = replacementFile || byId("knowledge-file").files?.[0];
  if (!file) return;
  const button = byId("upload-knowledge");
  button.disabled = true;
  const form = new FormData();
  form.append("file", file, file.name);
  try {
    await request(replacementId ? `/api/knowledge/${replacementId}/replace` : "/api/knowledge", {
      method: "POST", body: form,
    });
    if (!replacementId) byId("knowledge-file").value = "";
    updateKnowledgeSelection();
    await loadKnowledge();
    showToast(replacementId ? "Esempio sostituito in Cover Letter knowledge." : "Esempio aggiunto a Cover Letter knowledge.");
  } catch (error) { byId("knowledge-feedback").textContent = errorForDisplay(error); showToast(error, true); }
  finally { button.disabled = !byId("knowledge-file").files?.length || state.knowledgeCount >= 10; }
}

function updateKnowledgeSelection() {
  const file = byId("knowledge-file").files?.[0];
  byId("knowledge-file-name").textContent = file ? `File selezionato: ${file.name}` : "Nessun file selezionato.";
  byId("upload-knowledge").disabled = !file || state.knowledgeCount >= 10;
}

async function resolveAgentMigration(choice) {
  const feedback = byId("migration-feedback");
  feedback.textContent = "Creo copie di sicurezza e completo la migrazione…";
  try {
    const result = await request("/api/ai/agents/migration", {
      method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ choice }),
    });
    feedback.textContent = `Migrazione completata. Copie conservate in ${result.backups.settings_backup} e ${result.backups.agents_backup}.`;
    await loadAISettings();
    await loadAgentInstructions();
  } catch (error) { feedback.textContent = errorForDisplay(error); feedback.className = "inline-feedback is-error"; }
}

function renderCandidateProfile(profile) {
  state.profileHasCv = Boolean(profile.has_cv);
  const status = byId("cv-status");
  status.classList.remove("is-error");
  if (profile.has_cv) {
    const size = `${(profile.size_bytes / (1024 * 1024)).toLocaleString("it-IT", { maximumFractionDigits: 1 })} MB`;
    const filename = profile.filename || `cv.${profile.format}`;
    const legacy = profile.original_filename_available === false ? " · nome originale non disponibile; ricarica il CV per salvarlo" : "";
    status.textContent = `CV caricato: ${filename} · ${String(profile.format).toUpperCase()} · ${size}${legacy}`;
  } else {
    status.textContent = "Nessun CV caricato. Scegli il curriculum da usare per le lettere.";
  }
  if (!profile.pdf_extraction_available) status.textContent += " · Per leggere i PDF aggiorna le dipendenze dell’app.";
  byId("delete-cv").disabled = !state.profileHasCv;
}

async function loadCandidateProfile() {
  try {
    renderCandidateProfile(await request("/api/profile/cv"));
  } catch (error) {
    state.profileHasCv = false;
    byId("cv-status").textContent = errorForDisplay(error);
    byId("cv-status").classList.add("is-error");
  }
}

async function uploadCandidateCV() {
  const file = byId("cv-file").files?.[0];
  if (!file) return;
  const button = byId("upload-cv");
  const replacing = state.profileHasCv;
  button.disabled = true;
  byId("cv-status").textContent = "Salvo e verifico il CV sul PC…";
  try {
    const form = new FormData();
    form.append("file", file, file.name);
    const result = await request("/api/profile/cv", { method: "POST", body: form });
    renderCandidateProfile(result.cv);
    byId("cv-file").value = "";
    if (replacing) {
      byId("candidate-context-feedback").textContent = "CV sostituito; il contesto precedente è intatto. Puoi generare una nuova bozza e controllare le differenze prima di salvarla.";
      byId("candidate-context-feedback").className = "inline-feedback";
    }
    showToast(replacing ? "CV sostituito. Il contesto salvato non è stato modificato." : "CV salvato nella cartella locale del profilo.");
  } catch (error) {
    byId("cv-status").textContent = errorForDisplay(error);
    byId("cv-status").classList.add("is-error");
    showToast(error, true);
  } finally {
    button.disabled = !byId("cv-file").files?.length;
  }
}

async function deleteCandidateCV() {
  if (!state.profileHasCv || !window.confirm("Rimuovere il CV salvato da questo PC?")) return;
  const button = byId("delete-cv");
  button.disabled = true;
  try {
    const result = await request("/api/profile/cv", { method: "DELETE" });
    renderCandidateProfile(result.cv);
    showToast("CV rimosso dalla cartella locale.");
  } catch (error) {
    byId("cv-status").textContent = errorForDisplay(error);
    byId("cv-status").classList.add("is-error");
    showToast(error, true);
  }
}

let diagnosticsSearchTimer = null;

function formatDiagnosticTime(value) {
  const date = new Date(value);
  if (Number.isNaN(date.valueOf())) return value || "Orario non disponibile";
  return new Intl.DateTimeFormat("it-IT", {
    dateStyle: "medium",
    timeStyle: "medium",
  }).format(date);
}

function renderDiagnosticEvents(events) {
  state.diagnosticEvents = events.slice(0, 100);
  const list = byId("diagnostic-log-list");
  list.replaceChildren();
  if (!events.length) {
    list.append(element("p", "diagnostic-empty", "Nessun evento corrisponde ai filtri. Gli eventi più vecchi possono essere stati eliminati dalla rotazione."));
    return;
  }

  for (const record of events) {
    const level = String(record.level || "INFO").toLowerCase();
    const row = element("details", `diagnostic-event level-${level}`);
    const summary = element("summary");
    summary.append(element("span", `log-level-pill level-${level}`, record.level || "INFO"));
    summary.append(element("span", "log-component", record.component || "app"));
    const copy = element("span", "diagnostic-summary-copy");
    copy.append(element("strong", "", record.message || record.event || "Evento diagnostico"));
    copy.append(element("span", "", `${formatDiagnosticTime(record.timestamp)} · ${record.event || "log"}`));
    summary.append(copy);
    row.append(summary);

    const content = element("div", "diagnostic-event-content");
    if (record.request_id) {
      const requestReference = element("p");
      requestReference.append(document.createTextNode("ID richiesta: "));
      requestReference.append(element("code", "", record.request_id));
      content.append(requestReference);
    }
    if (record.context && Object.keys(record.context).length) {
      const context = element("p");
      context.append(document.createTextNode(`Contesto: ${JSON.stringify(record.context)}`));
      content.append(context);
    }
    if (record.details?.client_message) content.append(element("p", "", `Dettaglio browser: ${record.details.client_message}`));
    if (record.exception) content.append(element("pre", "", record.exception));
    if (record.details?.client_stack) content.append(element("pre", "", record.details.client_stack));
    row.append(content);
    list.append(row);
  }
}

function updateDiagnosticComponents(events) {
  const select = byId("log-component-filter");
  const selected = select.value;
  const components = [...new Set(events.map((record) => record.component).filter(Boolean))].sort();
  select.replaceChildren(new Option("Tutti", ""));
  for (const component of components) select.add(new Option(component, component));
  if (components.includes(selected)) select.value = selected;
}

async function loadDiagnosticEvents() {
  const feedback = byId("diagnostics-feedback");
  feedback.textContent = "Leggo i log locali…";
  feedback.className = "inline-feedback";
  const params = new URLSearchParams({ limit: "100" });
  params.set("since", state.diagnosticSince);
  if (byId("log-level-filter").value) params.set("level", byId("log-level-filter").value);
  if (byId("log-component-filter").value) params.set("component", byId("log-component-filter").value);
  if (byId("log-search").value.trim()) params.set("q", byId("log-search").value.trim());
  try {
    const result = await request(`/api/diagnostics/logs?${params}`);
    updateDiagnosticComponents(result.events || []);
    renderDiagnosticEvents(result.events || []);
    byId("copy-filtered-logs").disabled = !result.events?.length;
    byId("download-filtered-logs").disabled = !result.events?.length;
    feedback.textContent = `${result.logging_available ? `${result.count} eventi in questa connessione` : "Registrazione automatica non attiva"}`;
    feedback.className = result.logging_available ? "inline-feedback" : "inline-feedback is-error";
  } catch (error) {
    renderDiagnosticEvents([]);
    byId("copy-filtered-logs").disabled = true;
    byId("download-filtered-logs").disabled = true;
    feedback.textContent = errorForDisplay(error);
    feedback.className = "inline-feedback is-error";
    showToast(error, true);
  }
  await loadDiagnosticTimings();
}

async function loadDiagnosticTimings() {
  const container = byId("diagnostic-stage-timings");
  if (!container) return;
  container.replaceChildren(element("p", "loading-state", "Carico i tempi…"));
  try {
    const [usage, recent] = await Promise.all([
      request("/api/ai/usage-profile?days=30"),
      request(`/api/diagnostics/logs?limit=100&since=${encodeURIComponent(state.diagnosticSince)}`),
    ]);
    container.replaceChildren();
    const grouped = new Map();
    for (const row of usage.stages || []) {
      const key = `${row.operation}:${row.stage || row.operation}`;
      const current = grouped.get(key) || {operation: row.operation, stage: row.stage || row.operation, calls: 0, weightedMs: 0};
      current.calls += Number(row.calls || 0);
      current.weightedMs += Number(row.avg_duration_ms || 0) * Number(row.calls || 0);
      grouped.set(key, current);
    }
    const averages = [...grouped.values()].filter((row) => row.calls > 0)
      .map((row) => ({...row, avgMs: row.weightedMs / row.calls}))
      .sort((a, b) => b.avgMs - a.avgMs);
    if (!averages.length) {
      container.append(element("p", "settings-footnote", "Non ci sono ancora tempi API da confrontare."));
    } else {
      const table = element("table", "diagnostic-timing-table");
      const header = element("tr");
      for (const title of ["Fase", "Media", "Chiamate"]) header.append(element("th", "", title));
      const head = element("thead"); head.append(header); table.append(head);
      const body = element("tbody");
      for (const row of averages.slice(0, 10)) {
        const tr = element("tr");
        tr.append(element("td", "", `${row.stage} · ${row.operation}`));
        tr.append(element("td", "", `${(row.avgMs / 1000).toFixed(1)} s`));
        tr.append(element("td", "", String(row.calls)));
        body.append(tr);
      }
      table.append(body); container.append(table);
    }
    const starts = (recent.events || []).filter(record => record.event === "generation.started");
    const current = starts[starts.length - 1];
    const currentEvents = current ? (recent.events || []).filter(record => record.request_id === current.request_id) : [];
    const local = currentEvents.filter(record => record.event === "generation.stage_timing");
    if (current) {
      const terminalEvents = new Set([
        "generation.completed", "generation.failed", "generation.superseded",
        "generation.waiting_for_daily_limit", "generation.deferred",
      ]);
      const finish = currentEvents.find(record => terminalEvents.has(record.event));
      const sequential = local.filter(record => !record.context?.overlap);
      const slowest = sequential.reduce((best, record) => Number(record.context?.duration_ms || 0) > Number(best?.context?.duration_ms || 0) ? record : best, null);
      const startedAt = Date.parse(current.timestamp || "");
      const totalMs = finish
        ? Number(finish.context?.duration_ms || 0)
        : Number.isFinite(startedAt) ? Math.max(0, Date.now() - startedAt) : 0;
      const attributedMs = sequential.reduce((sum, record) => sum + Math.max(0, Number(record.context?.duration_ms || 0)), 0);
      const timingDelta = formatGenerationTimingDelta(totalMs, attributedMs);
      const outcome = finish?.context?.outcome || ({
        "generation.completed": "completata", "generation.failed": "fallita",
        "generation.superseded": "superata da una modifica", "generation.waiting_for_daily_limit": "in coda per limite giornaliero",
        "generation.deferred": "rimessa in coda",
      }[finish?.event]) || "in corso";
      container.append(element("p", "diagnostic-timing-heading", `Candidatura corrente · ${outcome} · attesa ${((current.context?.queue_ms || 0)/1000).toFixed(1)} s · totale ${finish ? (totalMs/1000).toFixed(1)+" s" : `${(totalMs/1000).toFixed(1)} s, in corso`} · fase più lenta ${slowest?.context?.stage || "in attesa"}`));
      container.append(element("p", "settings-footnote", `${timingDelta} Le fasi interne a Laya e all'indice CV sono incluse nella preparazione e non si sommano due volte.`));
    }
    container.append(element("p", "diagnostic-timing-heading", `Tempi recenti delle fasi · questa connessione (${local.length})`));
    if (!local.length) {
      container.append(element("p", "settings-footnote", "Avvia una generazione per vedere qui i tempi locali."));
    } else {
      const list = element("ul", "diagnostic-timing-recent");
      for (const record of local.slice(-12).reverse()) {
        const context = record.context || {};
        const duration = Number(context.duration_ms || 0);
        const outcome = context.outcome ? ` · ${context.outcome}` : "";
        list.append(element("li", "", `${context.stage || "Fase"}: ${(duration / 1000).toFixed(2)} s${outcome} · ${formatDiagnosticTime(record.timestamp)}`));
      }
      container.append(list);
    }
  } catch (error) {
    container.replaceChildren(element("p", "inline-feedback is-error", errorForDisplay(error)));
  }
}

function filteredDiagnosticsJSONL() {
  return state.diagnosticEvents.map((record) => JSON.stringify(record)).join("\n");
}

async function copyFilteredDiagnostics() {
  const text = filteredDiagnosticsJSONL();
  if (!text) return;
  try {
    await navigator.clipboard.writeText(text);
  } catch {
    const temporary = document.createElement("textarea");
    temporary.value = text;
    temporary.setAttribute("readonly", "");
    temporary.style.position = "fixed";
    temporary.style.opacity = "0";
    document.body.append(temporary);
    temporary.select();
    document.execCommand("copy");
    temporary.remove();
  }
  byId("diagnostics-feedback").textContent = `Copiati ${state.diagnosticEvents.length} eventi filtrati negli appunti.`;
  byId("diagnostics-feedback").className = "inline-feedback is-success";
}

function downloadFilteredDiagnostics() {
  const text = filteredDiagnosticsJSONL();
  if (!text) return;
  const blob = new Blob([text], { type: "application/x-ndjson;charset=utf-8" });
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = `job-letter-logs-${new Date().toISOString().replaceAll(":", "-")}.jsonl`;
  document.body.append(link);
  link.click();
  link.remove();
  URL.revokeObjectURL(url);
}

function initializeSettingsAccordions() {
  const bulkOperations = document.querySelector(".bulk-operations-setting");
  const aiSettings = document.querySelector(".ai-setting");
  if (bulkOperations && aiSettings) bulkOperations.after(aiSettings);

  const context = document.querySelector(".candidate-context-setting");
  const profile = document.querySelector(".candidate-profile-setting");
  if (context && profile) {
    const block = element("div", "candidate-cv-block");
    const heading = element("div", "settings-subheading");
    heading.append(element("p", "eyebrow", "CURRICULUM"), element("h4", "", "CV usato dagli agenti"));
    block.append(heading);
    for (const child of [...profile.children]) {
      if (!child.classList.contains("settings-section-heading")) block.append(child);
    }
    const contextEditor = context.querySelector('[for="candidate-context-markdown"]');
    contextEditor ? context.insertBefore(block, contextEditor) : context.append(block);
    profile.remove();
  }

  document.querySelectorAll("#settings-dialog .settings-section").forEach((section) => {
    const oldHeading = section.querySelector(":scope > .settings-section-heading");
    if (!oldHeading) return;
    const accordion = document.createElement("details");
    accordion.className = section.className;
    for (const attribute of [...section.attributes]) {
      if (attribute.name !== "class") accordion.setAttribute(attribute.name, attribute.value);
    }
    accordion.open = false;
    const summary = document.createElement("summary");
    summary.className = "settings-section-heading";
    while (oldHeading.firstChild) summary.append(oldHeading.firstChild);
    const content = element("div", "settings-section-content");
    for (const child of [...section.children]) {
      if (child !== oldHeading) content.append(child);
    }
    accordion.append(summary, content);
    section.replaceWith(accordion);
  });
}

byId("sync-button").addEventListener("click", startLinkedInSync);
byId("add-manual-job").addEventListener("click", () => {
  byId("manual-job-feedback").textContent = "";
  byId("manual-job-feedback").className = "inline-feedback manual-job-feedback";
  byId("manual-job-dialog").showModal();
  byId("manual-job-description").focus();
});
byId("close-manual-job").addEventListener("click", () => byId("manual-job-dialog").close());
byId("cancel-manual-job").addEventListener("click", () => byId("manual-job-dialog").close());
byId("manual-job-dialog").addEventListener("click", (event) => {
  if (event.target === byId("manual-job-dialog")) byId("manual-job-dialog").close();
});
byId("manual-job-form").addEventListener("submit", createManualJob);
byId("settings-button").addEventListener("click", () => {
  byId("settings-dialog").showModal();
  loadAISettings();
  loadAgentInstructions();
  loadCandidateContext();
  loadCandidateProfile();
  loadDiagnosticEvents();
  loadKnowledge();
  loadLayaSettings();
});
byId("analyze-laya").addEventListener("click", analyzeLaya);
byId("close-settings").addEventListener("click", () => byId("settings-dialog").close());
byId("settings-dialog").addEventListener("click", (event) => {
  if (event.target === byId("settings-dialog")) byId("settings-dialog").close();
});
byId("choose-destination-folder").addEventListener("click", chooseDestinationFolder);
byId("clear-destination-folder").addEventListener("click", async () => {
  try {
    await removeDirectoryHandle();
    state.directoryHandle = null;
    renderFolderDestination();
    showToast("Cartella di destinazione rimossa.");
  } catch (error) {
    showToast(error, true);
  }
});
byId("save-agent-markdown").addEventListener("click", saveAgentInstructions);
byId("save-ai-settings").addEventListener("click", saveAISettings);
byId("save-ai-agents").addEventListener("click", saveAgents);
byId("save-ai-key").addEventListener("click", saveAIKey);
byId("verify-ai-key").addEventListener("click", verifyAIKey);
byId("delete-ai-key").addEventListener("click", deleteAIKey);
byId("add-ai-agent").addEventListener("click", () => {
  if (state.aiAgents.length >= 12) return showToast("Massimo 12 agenti.", true);
  state.aiAgents.push({
    id: `custom_${crypto.randomUUID().replaceAll("-", "").slice(0, 16)}`,
    name: "Nuovo agente", phase: "Personalizzata", enabled: true,
    instructions: "Descrivi qui cosa deve fare questo agente.", model: null,
  });
  renderAgentEditors();
});
byId("keep-markdown-agents").addEventListener("click", () => resolveAgentMigration("keep_markdown"));
byId("use-saved-agents").addEventListener("click", () => resolveAgentMigration("use_saved_agents"));
byId("save-candidate-context").addEventListener("click", saveCandidateContext);
byId("generate-context-draft").addEventListener("click", generateCandidateContextDraft);
byId("load-cv-preview").addEventListener("click", showCVTextPreview);
byId("use-context-draft").addEventListener("click", () => {
  const answers = [...byId("candidate-context-question-fields").querySelectorAll("[data-context-answer]")]
    .map((input) => [input.dataset.contextAnswer, input.value.trim()]).filter(([, answer]) => answer);
  let draft = byId("candidate-context-draft").value.trim();
  if (answers.length) draft += `\n\n## Informazioni confermate dall’utente\n\n${answers.map(([question, answer]) => `- ${question}: ${answer}`).join("\n")}`;
  byId("candidate-context-markdown").value = `${draft}\n`;
  byId("candidate-context-markdown").focus();
  byId("candidate-context-feedback").textContent = "Bozza copiata nell’editor, non ancora salvata. Rivedila e premi Salva contesto.";
  byId("candidate-context-feedback").className = "inline-feedback";
});
byId("context-file").addEventListener("change", importCandidateContext);
byId("knowledge-file").addEventListener("change", () => {
  updateKnowledgeSelection();
});
byId("upload-knowledge").addEventListener("click", () => uploadKnowledge());
byId("submit-letter-feedback").addEventListener("click", submitLetterFeedback);
byId("add-letter-to-knowledge").addEventListener("click", addCurrentLetterToKnowledge);
byId("preview-bulk-regeneration").addEventListener("click", previewBulkRegeneration);
function invalidateBulkPreview() {
  state.bulkPlanToken = null;
  byId("bulk-regeneration-result").classList.add("hidden");
  byId("bulk-regeneration-actions").classList.add("hidden");
  byId("bulk-regeneration-feedback").textContent = "La selezione è cambiata: calcola una nuova stima.";
}
byId("bulk-regeneration-mode").addEventListener("change", invalidateBulkPreview);
byId("bulk-regeneration-scope").addEventListener("change", invalidateBulkPreview);
byId("recover-saved-jobs").addEventListener("click", recoverSavedJobs);
byId("confirm-bulk-regeneration").addEventListener("click", confirmBulkRegeneration);
byId("cancel-bulk-regeneration").addEventListener("click", cancelBulkRegeneration);
byId("refresh-company-research").addEventListener("click", refreshCompanyResearch);
byId("upload-cv").addEventListener("click", uploadCandidateCV);
byId("cv-file").addEventListener("change", () => {
  byId("upload-cv").disabled = !byId("cv-file").files?.length;
});
byId("delete-cv").addEventListener("click", deleteCandidateCV);
byId("regenerate-job").addEventListener("click", regenerateJob);
byId("mark-application-sent").addEventListener("click", async () => {
  if (!state.selectedId || !state.detail?.job) return;
  try {
    const result = await request(`/api/jobs/${encodeURIComponent(state.selectedId)}/application-status`, {
      method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ status: "APPLIED" }),
    });
    state.detail.job = result.job;
    renderDetail();
    await loadJobs({ preserveSelection: true });
    showToast("Candidatura registrata come inviata.");
  } catch (error) { showToast(error, true); }
});
byId("refresh-logs").addEventListener("click", loadDiagnosticEvents);
byId("refresh-stage-timings").addEventListener("click", loadDiagnosticTimings);
byId("copy-filtered-logs").addEventListener("click", copyFilteredDiagnostics);
byId("download-filtered-logs").addEventListener("click", downloadFilteredDiagnostics);
byId("log-level-filter").addEventListener("change", loadDiagnosticEvents);
byId("log-component-filter").addEventListener("change", loadDiagnosticEvents);
byId("log-search").addEventListener("input", () => {
  clearTimeout(diagnosticsSearchTimer);
  diagnosticsSearchTimer = setTimeout(loadDiagnosticEvents, 220);
});
byId("save-materials").addEventListener("click", saveMaterials);
byId("export-letter-text").addEventListener("click", () => exportEditedLetter("pdf"));
byId("export-letter-word").addEventListener("click", () => exportEditedLetter("docx"));
byId("save-agent-notes").addEventListener("click", saveMaterials);
byId("upload-form")?.addEventListener("submit", uploadLetter);
byId("letter-file")?.addEventListener("change", (event) => {
  const file = event.target.files?.[0];
  byId("upload-button").disabled = !file || !state.selectedId;
  if (file) byId("upload-button").textContent = `Archivia ${file.name.length > 22 ? `${file.name.slice(0, 19)}…` : file.name}`;
});
byId("cover-letter-text").addEventListener("input", updateCharacterCount);
for (const id of ["company-analysis", "personalization-notes", "agent-source"]) {
  byId(id).addEventListener("input", markMaterialsDirty);
}
byId("cover-letter-text").addEventListener("input", markMaterialsDirty);
byId("search-input").addEventListener("input", () => {
  clearTimeout(state.searchTimer);
  state.searchTimer = setTimeout(() => loadJobs().catch((error) => showToast(error, true)), 180);
});
byId("status-filter").addEventListener("change", () => loadJobs().catch((error) => showToast(error, true)));

async function initializeDashboard() {
  initializeSettingsAccordions();
  initializeJobSorting();
  try {
    state.directoryHandle = await readDirectoryHandle();
  } catch (error) {
    showToast(`Cartella di destinazione non disponibile: ${error.message}`, true);
  }
  renderFolderDestination();
  await loadCandidateProfile();
  await loadAISettings();
  try {
    await loadJobs({ preserveSelection: false });
  } catch (error) {
    byId("job-list").replaceChildren(element("p", "list-empty", `Non riesco a caricare gli annunci: ${error.message}`));
    showToast(error, true);
  }
  await loadSavedSyncReport();
  await refreshGenerationStatus().catch(() => {});
  window.setInterval(() => refreshGenerationStatus().catch(() => {}), 5000);
}

initializeDashboard();
