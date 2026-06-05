from __future__ import annotations

import html
import hashlib
import json
import mimetypes
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from pypdf import PdfReader, PdfWriter


ROOT = Path(__file__).resolve().parent
STATIC = ROOT / "static"
CACHE = ROOT / ".cache"
PORT = 8765
MAX_RANGE_PAGES = 40
OPENAI_API_URL = "https://api.openai.com/v1/responses"
DEFAULT_MODEL = "gpt-5-mini"


def load_dotenv() -> None:
    env_path = ROOT / ".env"
    if not env_path.exists():
        return
    for raw_line in env_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip().lstrip("\ufeff")
        value = value.strip().strip('"').strip("'")
        os.environ.setdefault(key, value)


def project_pdfs() -> list[dict[str, object]]:
    pdfs = []
    for path in sorted(ROOT.glob("*.pdf")):
        try:
            size_mb = round(path.stat().st_size / (1024 * 1024), 1)
        except OSError:
            size_mb = 0
        pdfs.append({"name": path.name, "size_mb": size_mb})
    return pdfs


def pdf_page_count(path: Path) -> int:
    reader = PdfReader(str(path))
    return len(reader.pages)


def extract_pages(path: Path, start_page: int, end_page: int) -> dict[str, object]:
    reader = PdfReader(str(path))
    total_pages = len(reader.pages)

    if start_page < 1 or end_page < 1:
        raise ValueError("Las paginas empiezan en 1.")
    if start_page > end_page:
        raise ValueError("La pagina inicial no puede ser mayor que la final.")
    if end_page > total_pages:
        raise ValueError(f"El documento solo tiene {total_pages} paginas.")
    if end_page - start_page + 1 > MAX_RANGE_PAGES:
        raise ValueError(f"Por ahora el rango maximo es de {MAX_RANGE_PAGES} paginas.")

    pages = []
    total_chars = 0
    range_pdf = ensure_range_pdf(path, reader, start_page, end_page)
    for page_number in range(start_page, end_page + 1):
        text = (reader.pages[page_number - 1].extract_text() or "").strip()
        total_chars += len(text)
        pages.append(
            {
                "page": page_number,
                "original": text,
                "translation": "",
                "chars": len(text),
                "has_text": bool(text),
            }
        )

    return {
        "file": path.name,
        "start_page": start_page,
        "end_page": end_page,
        "total_pages": total_pages,
        "total_chars": total_chars,
        "range_pdf": range_pdf.name,
        "pages": pages,
    }


def json_response(handler: BaseHTTPRequestHandler, status: int, payload: object) -> None:
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    handler.send_response(status)
    handler.send_header("Content-Type", "application/json; charset=utf-8")
    handler.send_header("Content-Length", str(len(body)))
    handler.end_headers()
    handler.wfile.write(body)


def static_response(handler: BaseHTTPRequestHandler, path: Path) -> None:
    if not path.exists() or not path.is_file():
        handler.send_error(404)
        return
    body = path.read_bytes()
    content_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
    handler.send_response(200)
    handler.send_header("Content-Type", content_type)
    handler.send_header("Content-Length", str(len(body)))
    handler.end_headers()
    handler.wfile.write(body)


class AppHandler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path == "/":
            static_response(self, STATIC / "index.html")
            return
        if parsed.path == "/api/pdfs":
            json_response(self, 200, {"pdfs": project_pdfs()})
            return
        if parsed.path == "/api/pdf-info":
            self.handle_pdf_info(parsed.query)
            return
        if parsed.path == "/api/pdf-file":
            self.handle_pdf_file(parsed.query)
            return
        if parsed.path == "/api/range-pdf":
            self.handle_range_pdf(parsed.query)
            return
        if parsed.path == "/api/extract":
            self.handle_extract(parsed.query)
            return
        if parsed.path.startswith("/static/"):
            safe_name = parsed.path.removeprefix("/static/").replace("/", "")
            static_response(self, STATIC / safe_name)
            return
        self.send_error(404)

    def do_POST(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path == "/api/translate":
            self.handle_translate()
            return
        self.send_error(404)

    def handle_pdf_info(self, query: str) -> None:
        params = parse_qs(query)
        name = params.get("file", [""])[0]
        path = (ROOT / name).resolve()
        if not is_safe_pdf(path):
            json_response(self, 400, {"error": "PDF no valido."})
            return
        try:
            json_response(self, 200, {"file": path.name, "total_pages": pdf_page_count(path)})
        except Exception as exc:
            json_response(self, 500, {"error": str(exc)})

    def handle_extract(self, query: str) -> None:
        params = parse_qs(query)
        name = params.get("file", [""])[0]
        path = (ROOT / name).resolve()
        if not is_safe_pdf(path):
            json_response(self, 400, {"error": "PDF no valido."})
            return
        try:
            start_page = int(params.get("start", ["1"])[0])
            end_page = int(params.get("end", ["1"])[0])
            result = extract_pages(path, start_page, end_page)
            json_response(self, 200, result)
        except ValueError as exc:
            json_response(self, 400, {"error": str(exc)})
        except Exception as exc:
            json_response(self, 500, {"error": str(exc)})

    def handle_pdf_file(self, query: str) -> None:
        params = parse_qs(query)
        name = params.get("file", [""])[0]
        path = (ROOT / name).resolve()
        if not is_safe_pdf(path):
            self.send_error(404)
            return
        stream_pdf_response(self, path)

    def handle_range_pdf(self, query: str) -> None:
        params = parse_qs(query)
        name = params.get("name", [""])[0]
        path = (CACHE / name).resolve()
        if not is_safe_cached_pdf(path):
            self.send_error(404)
            return
        stream_pdf_response(self, path)

    def handle_translate(self) -> None:
        try:
            payload = read_json_body(self)
            name = str(payload.get("file", ""))
            path = (ROOT / name).resolve()
            if not is_safe_pdf(path):
                json_response(self, 400, {"error": "PDF no valido."})
                return
            start_page = int(payload.get("start_page", 1))
            end_page = int(payload.get("end_page", 1))
            profile = str(payload.get("profile", "it-legal-es"))
            result = translate_range(path, start_page, end_page, profile)
            json_response(self, 200, result)
        except ValueError as exc:
            json_response(self, 400, {"error": str(exc)})
        except Exception as exc:
            json_response(self, 500, {"error": str(exc)})

    def log_message(self, format: str, *args: object) -> None:
        return


def is_safe_pdf(path: Path) -> bool:
    try:
        return path.parent == ROOT and path.suffix.lower() == ".pdf" and path.exists()
    except OSError:
        return False


def is_safe_cached_pdf(path: Path) -> bool:
    try:
        return path.parent == CACHE and path.suffix.lower() == ".pdf" and path.exists()
    except OSError:
        return False


def read_json_body(handler: BaseHTTPRequestHandler) -> dict[str, object]:
    length = int(handler.headers.get("Content-Length", "0"))
    if length <= 0:
        return {}
    if length > 1024 * 1024:
        raise ValueError("La solicitud es demasiado grande.")
    return json.loads(handler.rfile.read(length).decode("utf-8"))


def ensure_range_pdf(source: Path, reader: PdfReader, start_page: int, end_page: int) -> Path:
    CACHE.mkdir(exist_ok=True)
    stat = source.stat()
    digest_input = f"{source.name}:{stat.st_size}:{stat.st_mtime_ns}:{start_page}:{end_page}"
    digest = hashlib.sha256(digest_input.encode("utf-8")).hexdigest()[:16]
    output = CACHE / f"{source.stem}-{start_page}-{end_page}-{digest}.pdf"
    if output.exists():
        return output

    writer = PdfWriter()
    for page_number in range(start_page, end_page + 1):
        writer.add_page(reader.pages[page_number - 1])

    with output.open("wb") as handle:
        writer.write(handle)
    return output


def translate_range(path: Path, start_page: int, end_page: int, profile: str) -> dict[str, object]:
    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        raise ValueError("Falta OPENAI_API_KEY. Agregala en un archivo .env o como variable de entorno.")

    extracted = extract_pages(path, start_page, end_page)
    pages = extracted["pages"]
    instructions = translation_instructions(profile)
    source_text = "\n\n".join(
        f"--- PAGINA {page['page']} ---\n{page['original']}" for page in pages
    )
    prompt = (
        "Traduce las paginas siguientes y devuelve solamente JSON valido con esta forma: "
        '{"pages":[{"page":10,"translation":"texto traducido"}]}. '
        "No agregues comentarios fuera del JSON.\n\n"
        f"{source_text}"
    )

    model = os.environ.get("OPENAI_MODEL", DEFAULT_MODEL)
    request_payload = {
        "model": model,
        "instructions": instructions,
        "input": prompt,
        "max_output_tokens": output_token_budget(source_text),
    }
    if model.startswith("gpt-5"):
        request_payload["reasoning"] = {"effort": "minimal"}

    response = call_openai(request_payload, api_key)
    content = response_text(response)
    translated_pages = parse_translation_json(content)
    by_page = {int(item["page"]): str(item["translation"]).strip() for item in translated_pages}

    return {
        "file": path.name,
        "start_page": start_page,
        "end_page": end_page,
        "model": model,
        "pages": [
            {
                "page": page["page"],
                "translation": by_page.get(int(page["page"]), ""),
            }
            for page in pages
        ],
    }


def translation_instructions(profile: str) -> str:
    if profile == "en-es":
        return (
            "Eres traductor profesional de ingles a espanol. Traduce de forma clara, fiel y natural. "
            "Conserva citas, numeracion, nombres propios, referencias y terminos tecnicos cuando corresponda. "
            "No resumas ni expliques."
        )
    return (
        "Eres traductor juridico profesional de italiano a espanol. Traduce con precision doctrinal y procesal. "
        "Conserva citas, numeracion, nombres propios, referencias normativas y terminos latinos. "
        "Cuando exista un tecnicismo juridico italiano sin equivalente exacto, usa la traduccion espanola mas fiel "
        "sin agregar notas explicativas. No resumas ni expliques."
    )


def output_token_budget(text: str) -> int:
    estimated = max(2000, int(len(text) / 2.2))
    return min(60000, estimated)


def call_openai(payload: dict[str, object], api_key: str) -> dict[str, object]:
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    request = Request(
        OPENAI_API_URL,
        data=body,
        method="POST",
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
    )
    try:
        with urlopen(request, timeout=180) as response:
            return json.loads(response.read().decode("utf-8"))
    except HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise ValueError(f"OpenAI respondio con error {exc.code}: {detail}") from exc
    except URLError as exc:
        raise ValueError(f"No se pudo conectar con OpenAI: {exc.reason}") from exc


def response_text(response: dict[str, object]) -> str:
    if isinstance(response.get("output_text"), str):
        return str(response["output_text"])

    fragments: list[str] = []
    for item in response.get("output", []):
        if not isinstance(item, dict):
            continue
        for content in item.get("content", []):
            if not isinstance(content, dict):
                continue
            text = content.get("text")
            if isinstance(text, str):
                fragments.append(text)
    if fragments:
        return "\n".join(fragments)
    raise ValueError("La respuesta de OpenAI no trajo texto traducido.")


def parse_translation_json(content: str) -> list[dict[str, object]]:
    cleaned = content.strip()
    if cleaned.startswith("```"):
        cleaned = cleaned.strip("`")
        if cleaned.lower().startswith("json"):
            cleaned = cleaned[4:].strip()
    data = json.loads(cleaned)
    pages = data.get("pages")
    if not isinstance(pages, list):
        raise ValueError("La traduccion no tuvo el formato esperado.")
    return pages


def stream_pdf_response(handler: BaseHTTPRequestHandler, path: Path) -> None:
    file_size = path.stat().st_size
    range_header = handler.headers.get("Range")
    start = 0
    end = file_size - 1
    status = 200

    if range_header and range_header.startswith("bytes="):
        requested = range_header.removeprefix("bytes=").split(",", 1)[0]
        start_text, _, end_text = requested.partition("-")
        try:
            if start_text:
                start = int(start_text)
            if end_text:
                end = int(end_text)
            end = min(end, file_size - 1)
            if start > end or start >= file_size:
                handler.send_response(416)
                handler.send_header("Content-Range", f"bytes */{file_size}")
                handler.end_headers()
                return
            status = 206
        except ValueError:
            start = 0
            end = file_size - 1
            status = 200

    content_length = end - start + 1
    handler.send_response(status)
    handler.send_header("Content-Type", "application/pdf")
    handler.send_header("Content-Disposition", f'inline; filename="{html.escape(path.name)}"')
    handler.send_header("Accept-Ranges", "bytes")
    handler.send_header("Content-Length", str(content_length))
    if status == 206:
        handler.send_header("Content-Range", f"bytes {start}-{end}/{file_size}")
    handler.end_headers()

    with path.open("rb") as pdf:
        pdf.seek(start)
        remaining = content_length
        while remaining > 0:
            chunk = pdf.read(min(1024 * 512, remaining))
            if not chunk:
                break
            handler.wfile.write(chunk)
            remaining -= len(chunk)


if __name__ == "__main__":
    load_dotenv()
    print(f"Traductor de PDFs listo en http://localhost:{PORT}")
    ThreadingHTTPServer(("127.0.0.1", PORT), AppHandler).serve_forever()
