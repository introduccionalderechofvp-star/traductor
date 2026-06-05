const fileSelect = document.querySelector("#fileSelect");
const startPage = document.querySelector("#startPage");
const endPage = document.querySelector("#endPage");
const extractButton = document.querySelector("#extractButton");
const translateButton = document.querySelector("#translateButton");
const downloadButton = document.querySelector("#downloadButton");
const profileSelect = document.querySelector("#profileSelect");
const progressBox = document.querySelector("#progressBox");
const progressLabel = document.querySelector("#progressLabel");
const progressDetail = document.querySelector("#progressDetail");
const documentInfo = document.querySelector("#documentInfo");
const statusBox = document.querySelector("#status");
const themeToggle = document.querySelector("#themeToggle");
const originalPane = document.querySelector("#originalPane");
const translationPane = document.querySelector("#translationPane");
const translationMeta = document.querySelector("#translationMeta");
const prevPage = document.querySelector("#prevPage");
const nextPage = document.querySelector("#nextPage");
const activePageInput = document.querySelector("#activePage");

let syncing = false;
let currentResult = null;
let activePageNumber = 1;

function applyTheme(theme) {
  document.documentElement.dataset.theme = theme;
  themeToggle.textContent = theme === "dark" ? "Modo claro" : "Modo oscuro";
  localStorage.setItem("pdfTranslatorTheme", theme);
}

function initialTheme() {
  const saved = localStorage.getItem("pdfTranslatorTheme");
  if (saved === "dark" || saved === "light") return saved;
  return window.matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light";
}

function setStatus(text) {
  statusBox.textContent = text;
}

function showProgress(label, detail = "") {
  progressLabel.textContent = label;
  progressDetail.textContent = detail;
  progressBox.hidden = false;
}

function hideProgress() {
  progressBox.hidden = true;
  progressDetail.textContent = "";
}

function escapeHtml(value) {
  return value
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

async function getJson(url) {
  const response = await fetch(url);
  const payload = await response.json();
  if (!response.ok) {
    throw new Error(payload.error || "No se pudo completar la solicitud.");
  }
  return payload;
}

async function loadPdfs() {
  setStatus("Buscando PDFs");
  const payload = await getJson("/api/pdfs");
  fileSelect.innerHTML = "";

  for (const pdf of payload.pdfs) {
    const option = document.createElement("option");
    option.value = pdf.name;
    option.textContent = `${pdf.name} (${pdf.size_mb} MB)`;
    fileSelect.appendChild(option);
  }

  if (!payload.pdfs.length) {
    documentInfo.textContent = "No hay PDFs en la carpeta del proyecto.";
    extractButton.disabled = true;
    setStatus("Sin PDFs");
    return;
  }

  await loadPdfInfo();
  setStatus("Listo");
}

async function loadPdfInfo() {
  const file = encodeURIComponent(fileSelect.value);
  const info = await getJson(`/api/pdf-info?file=${file}`);
  startPage.max = info.total_pages;
  endPage.max = info.total_pages;
  activePageInput.max = info.total_pages;
  endPage.value = Math.min(Number(endPage.value) || 1, info.total_pages);
  documentInfo.textContent = `${info.file}: ${info.total_pages} paginas. Rango maximo por prueba: 40 paginas.`;
}

function renderPages(result) {
  currentResult = result;
  activePageInput.min = result.start_page;
  activePageInput.max = result.end_page;
  currentResult.pages = currentResult.pages.map((page) => ({
    ...page,
    translation: "Pendiente de traduccion. Usa el boton Traducir rango.",
    translated: false,
  }));
  setActivePage(result.start_page);
}

function renderBlock(pageNumber, text, chars) {
  const content = text
    ? `<pre>${escapeHtml(text)}</pre>`
    : `<pre class="empty">Sin texto extraible.</pre>`;
  return `
    <article class="page-block">
      <div class="page-label">Pagina ${pageNumber} - ${chars.toLocaleString("es-CO")} caracteres</div>
      ${content}
    </article>
  `;
}

async function extractRange() {
  const file = encodeURIComponent(fileSelect.value);
  const start = Number(startPage.value);
  const end = Number(endPage.value);

  extractButton.disabled = true;
  translateButton.disabled = true;
  downloadButton.disabled = true;
  setStatus("Extrayendo rango");
  showProgress("Extrayendo rango", `${start}-${end}`);
  originalPane.innerHTML = "";
  translationPane.innerHTML = "";

  try {
    const result = await getJson(`/api/extract?file=${file}&start=${start}&end=${end}`);
    renderPages(result);
    translateButton.disabled = false;
    setStatus(`Paginas ${result.start_page}-${result.end_page}`);
  } catch (error) {
    setStatus("Error");
    originalPane.innerHTML = `<pre class="empty">${escapeHtml(error.message)}</pre>`;
  } finally {
    extractButton.disabled = false;
    hideProgress();
  }
}

async function translateRange() {
  if (!currentResult) return;

  setStatus("Traduciendo rango");
  showProgress("Traduccion en curso", `${currentResult.start_page}-${currentResult.end_page}`);
  translationPane.innerHTML = `<pre class="empty">Traduciendo paginas ${currentResult.start_page}-${currentResult.end_page}. Esto puede tardar unos segundos.</pre>`;
  translateButton.disabled = true;
  extractButton.disabled = true;
  downloadButton.disabled = true;

  try {
    const response = await fetch("/api/translate", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        file: currentResult.file,
        start_page: currentResult.start_page,
        end_page: currentResult.end_page,
        profile: profileSelect.value,
      }),
    });
    const payload = await response.json();
    if (!response.ok) {
      throw new Error(payload.error || "No se pudo traducir el rango.");
    }

    const translationsByPage = new Map(payload.pages.map((page) => [page.page, page.translation]));
    currentResult.pages = currentResult.pages.map((page) => ({
      ...page,
      translation: translationsByPage.get(page.page) || "",
      translated: true,
    }));
    setActivePage(activePageNumber);
    downloadButton.disabled = false;
    setStatus(`Traduccion lista con ${payload.model}`);
  } catch (error) {
    setStatus("Error al traducir");
    translationPane.innerHTML = `<pre class="empty">${escapeHtml(error.message)}</pre>`;
    translateButton.disabled = false;
  } finally {
    extractButton.disabled = false;
    hideProgress();
  }
}

function downloadTranslation() {
  if (!currentResult) return;

  const lines = [
    `Archivo: ${currentResult.file}`,
    `Paginas: ${currentResult.start_page}-${currentResult.end_page}`,
    "",
    ...currentResult.pages.flatMap((page) => [
      `Pagina ${page.page}`,
      "",
      page.translation,
      "",
      "----------------------------------------",
      "",
    ]),
  ];

  const blob = new Blob([lines.join("\n")], { type: "text/plain;charset=utf-8" });
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  const baseName = currentResult.file.replace(/\.pdf$/i, "");
  link.href = url;
  link.download = `${baseName}-traduccion-p${currentResult.start_page}-${currentResult.end_page}.txt`;
  document.body.appendChild(link);
  link.click();
  link.remove();
  URL.revokeObjectURL(url);
}

function setActivePage(pageNumber) {
  if (!currentResult) return;
  const clamped = Math.max(currentResult.start_page, Math.min(currentResult.end_page, pageNumber));
  const page = currentResult.pages.find((item) => item.page === clamped);
  if (!page) return;

  activePageNumber = clamped;
  activePageInput.value = String(clamped);
  prevPage.disabled = clamped <= currentResult.start_page;
  nextPage.disabled = clamped >= currentResult.end_page;

  const rangePdf = encodeURIComponent(currentResult.range_pdf);
  const relativePage = clamped - currentResult.start_page + 1;
  originalPane.innerHTML = `
    <iframe
      class="pdf-viewer"
      title="PDF original pagina ${clamped}"
      src="/api/range-pdf?name=${rangePdf}#page=${relativePage}&zoom=page-width"
    ></iframe>
  `;
  translationPane.innerHTML = renderBlock(page.page, page.translation, page.translation.length);
  translationMeta.textContent = `${page.chars.toLocaleString("es-CO")} caracteres originales`;
}

fileSelect.addEventListener("change", loadPdfInfo);
extractButton.addEventListener("click", extractRange);
translateButton.addEventListener("click", translateRange);
downloadButton.addEventListener("click", downloadTranslation);
themeToggle.addEventListener("click", () => {
  const current = document.documentElement.dataset.theme === "dark" ? "dark" : "light";
  applyTheme(current === "dark" ? "light" : "dark");
});
prevPage.addEventListener("click", () => setActivePage(activePageNumber - 1));
nextPage.addEventListener("click", () => setActivePage(activePageNumber + 1));
activePageInput.addEventListener("change", () => setActivePage(Number(activePageInput.value)));

applyTheme(initialTheme());
loadPdfs().catch((error) => {
  setStatus("Error");
  documentInfo.textContent = error.message;
});
