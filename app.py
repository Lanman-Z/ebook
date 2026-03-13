import html
import re
import shutil
import sqlite3
import uuid
import zipfile
from datetime import datetime
from pathlib import Path
from typing import Iterable

from bs4 import BeautifulSoup
from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pypdf import PdfReader


def ensure_directories() -> None:
    UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    TEMPLATES_DIR.mkdir(parents=True, exist_ok=True)
    STATIC_DIR.mkdir(parents=True, exist_ok=True)


BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"
UPLOAD_DIR = DATA_DIR / "uploads"
DB_PATH = DATA_DIR / "ebook.db"
TEMPLATES_DIR = BASE_DIR / "templates"
STATIC_DIR = BASE_DIR / "static"
ALLOWED_SUFFIXES = {".pdf", ".epub"}


ensure_directories()

app = FastAPI(title="电子书阅读应用")
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


def get_connection() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
    with get_connection() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS books (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                title TEXT NOT NULL,
                filename TEXT NOT NULL,
                original_name TEXT NOT NULL,
                file_type TEXT NOT NULL,
                content TEXT NOT NULL,
                created_at TEXT NOT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS reviews (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                book_id INTEGER NOT NULL,
                reviewer TEXT NOT NULL,
                selected_text TEXT NOT NULL,
                paragraph_index INTEGER NOT NULL DEFAULT 0,
                start_offset INTEGER NOT NULL DEFAULT 0,
                end_offset INTEGER NOT NULL DEFAULT 0,
                comment TEXT NOT NULL,
                created_at TEXT NOT NULL,
                FOREIGN KEY(book_id) REFERENCES books(id)
            )
            """
        )
        review_columns = {
            row["name"] for row in conn.execute("PRAGMA table_info(reviews)").fetchall()
        }
        if "paragraph_index" not in review_columns:
            conn.execute(
                "ALTER TABLE reviews ADD COLUMN paragraph_index INTEGER NOT NULL DEFAULT 0"
            )
        if "start_offset" not in review_columns:
            conn.execute(
                "ALTER TABLE reviews ADD COLUMN start_offset INTEGER NOT NULL DEFAULT 0"
            )
        if "end_offset" not in review_columns:
            conn.execute(
                "ALTER TABLE reviews ADD COLUMN end_offset INTEGER NOT NULL DEFAULT 0"
            )


def normalize_text(text: str) -> str:
    normalized = html.unescape(text)
    normalized = normalized.replace("\r", "\n")
    normalized = re.sub(r"\n{3,}", "\n\n", normalized)
    normalized = re.sub(r"[ \t]+", " ", normalized)
    return normalized.strip()


def split_into_paragraphs(text: str) -> list[str]:
    paragraphs = []
    for chunk in re.split(r"\n{2,}", text):
        clean = normalize_text(chunk)
        if clean:
            if len(clean) > 260 and "\n" in chunk:
                for line in chunk.splitlines():
                    line_clean = normalize_text(line)
                    if line_clean:
                        paragraphs.append(line_clean)
            else:
                paragraphs.append(clean)

    if len(paragraphs) <= 1:
        paragraphs = split_long_text(text)

    return paragraphs


def split_long_text(text: str, target: int = 180) -> list[str]:
    clean = normalize_text(text)
    if not clean:
        return []

    sentence_parts = re.split(r"(?<=[。！？!?；;])\s+", clean)
    paragraphs: list[str] = []
    current = ""

    for sentence in sentence_parts:
        sentence = sentence.strip()
        if not sentence:
            continue
        if len(current) + len(sentence) + 1 <= target:
            current = f"{current} {sentence}".strip()
        else:
            if current:
                paragraphs.append(current)
            current = sentence

    if current:
        paragraphs.append(current)

    return paragraphs or [clean]


def extract_pdf_text(file_path: Path) -> str:
    reader = PdfReader(str(file_path))
    parts = []
    for page in reader.pages:
        parts.append(page.extract_text() or "")
    return normalize_text("\n\n".join(parts))


def extract_epub_text(file_path: Path) -> str:
    text_parts: list[str] = []
    with zipfile.ZipFile(file_path) as archive:
        html_candidates = sorted(
            name
            for name in archive.namelist()
            if name.lower().endswith((".xhtml", ".html", ".htm"))
        )
        for member_name in html_candidates:
            with archive.open(member_name) as content_file:
                raw = content_file.read()
            soup = BeautifulSoup(raw, "html.parser")
            for tag in soup(["script", "style", "nav"]):
                tag.decompose()
            text = soup.get_text("\n", strip=True)
            cleaned = normalize_text(text)
            if cleaned:
                text_parts.append(cleaned)
    return normalize_text("\n\n".join(text_parts))


def extract_book_text(file_path: Path) -> str:
    suffix = file_path.suffix.lower()
    if suffix == ".pdf":
        return extract_pdf_text(file_path)
    if suffix == ".epub":
        return extract_epub_text(file_path)
    raise HTTPException(status_code=400, detail="仅支持上传 PDF 和 EPUB 文件。")


def book_row_to_dict(row: sqlite3.Row) -> dict:
    paragraphs = split_into_paragraphs(row["content"])
    return {
        "id": row["id"],
        "title": row["title"],
        "filename": row["filename"],
        "original_name": row["original_name"],
        "file_type": row["file_type"],
        "created_at": row["created_at"],
        "content": row["content"],
        "paragraphs": paragraphs,
    }


def review_row_to_dict(row: sqlite3.Row) -> dict:
    return {
        "id": row["id"],
        "book_id": row["book_id"],
        "reviewer": row["reviewer"],
        "selected_text": row["selected_text"],
        "paragraph_index": row["paragraph_index"],
        "start_offset": row["start_offset"],
        "end_offset": row["end_offset"],
        "comment": row["comment"],
        "created_at": row["created_at"],
    }


def book_summary_row_to_dict(row: sqlite3.Row) -> dict:
    return {
        "id": row["id"],
        "title": row["title"],
        "filename": row["filename"],
        "original_name": row["original_name"],
        "file_type": row["file_type"],
        "created_at": row["created_at"],
    }


def delete_book_files(filename: str) -> None:
    file_path = UPLOAD_DIR / filename
    file_path.unlink(missing_ok=True)


@app.on_event("startup")
def on_startup() -> None:
    ensure_directories()
    init_db()


@app.get("/", response_class=HTMLResponse)
async def index(request: Request) -> HTMLResponse:
    return templates.TemplateResponse("index.html", {"request": request})


@app.get("/api/books")
async def list_books() -> dict:
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT * FROM books ORDER BY created_at DESC, id DESC"
        ).fetchall()
    return {"books": [book_summary_row_to_dict(row) for row in rows]}


@app.post("/api/books")
async def upload_book(
    file: UploadFile = File(...),
    title: str = Form(default=""),
) -> dict:
    original_name = file.filename or ""
    suffix = Path(original_name).suffix.lower()
    if suffix not in ALLOWED_SUFFIXES:
        raise HTTPException(status_code=400, detail="请上传 PDF 或 EPUB 文件。")

    book_title = title.strip() or Path(original_name).stem
    stored_name = f"{uuid.uuid4().hex}{suffix}"
    save_path = UPLOAD_DIR / stored_name

    with save_path.open("wb") as output:
        shutil.copyfileobj(file.file, output)

    try:
        content = extract_book_text(save_path)
    except Exception as exc:
        save_path.unlink(missing_ok=True)
        raise HTTPException(status_code=400, detail=f"电子书解析失败：{exc}") from exc

    if not content.strip():
        save_path.unlink(missing_ok=True)
        raise HTTPException(status_code=400, detail="上传的电子书里没有可读取的文字内容。")

    created_at = datetime.now().isoformat(timespec="seconds")
    with get_connection() as conn:
        cursor = conn.execute(
            """
            INSERT INTO books (title, filename, original_name, file_type, content, created_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (book_title, stored_name, original_name, suffix.lstrip("."), content, created_at),
        )
        book_id = cursor.lastrowid
        row = conn.execute("SELECT * FROM books WHERE id = ?", (book_id,)).fetchone()

    return {"book": book_row_to_dict(row)}


@app.get("/api/books/{book_id}")
async def get_book(book_id: int) -> dict:
    with get_connection() as conn:
        row = conn.execute("SELECT * FROM books WHERE id = ?", (book_id,)).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="未找到这本书。")
    return {"book": book_row_to_dict(row)}


@app.delete("/api/books/{book_id}")
async def delete_book(book_id: int) -> dict:
    with get_connection() as conn:
        row = conn.execute("SELECT * FROM books WHERE id = ?", (book_id,)).fetchone()
        if row is None:
            raise HTTPException(status_code=404, detail="未找到这本书。")
        conn.execute("DELETE FROM reviews WHERE book_id = ?", (book_id,))
        conn.execute("DELETE FROM books WHERE id = ?", (book_id,))

    delete_book_files(row["filename"])
    return {"message": "书籍已删除。", "book_id": book_id}


@app.get("/api/books/{book_id}/reviews")
async def list_reviews(book_id: int) -> dict:
    with get_connection() as conn:
        exists = conn.execute("SELECT 1 FROM books WHERE id = ?", (book_id,)).fetchone()
        if exists is None:
            raise HTTPException(status_code=404, detail="未找到这本书。")
        rows = conn.execute(
            """
            SELECT * FROM reviews
            WHERE book_id = ?
            ORDER BY created_at DESC, id DESC
            """,
            (book_id,),
        ).fetchall()
    return {"reviews": [review_row_to_dict(row) for row in rows]}


@app.post("/api/books/{book_id}/reviews")
async def create_review(
    book_id: int,
    reviewer: str = Form(default=""),
    selected_text: str = Form(...),
    paragraph_index: int = Form(...),
    start_offset: int = Form(...),
    end_offset: int = Form(...),
    comment: str = Form(...),
) -> dict:
    reviewer_name = reviewer.strip() or "匿名读者"
    selected = normalize_text(selected_text)
    review_comment = comment.strip()

    if not selected:
        raise HTTPException(status_code=400, detail="请先选中一句话或一段文字。")
    if paragraph_index < 0:
        raise HTTPException(status_code=400, detail="评论位置无效。")
    if start_offset < 0 or end_offset <= start_offset:
        raise HTTPException(status_code=400, detail="选中文本的位置无效。")
    if not review_comment:
        raise HTTPException(status_code=400, detail="请输入点评内容。")

    created_at = datetime.now().isoformat(timespec="seconds")
    with get_connection() as conn:
        exists = conn.execute("SELECT 1 FROM books WHERE id = ?", (book_id,)).fetchone()
        if exists is None:
            raise HTTPException(status_code=404, detail="未找到这本书。")
        cursor = conn.execute(
            """
            INSERT INTO reviews (
                book_id,
                reviewer,
                selected_text,
                paragraph_index,
                start_offset,
                end_offset,
                comment,
                created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                book_id,
                reviewer_name,
                selected,
                paragraph_index,
                start_offset,
                end_offset,
                review_comment,
                created_at,
            ),
        )
        review_id = cursor.lastrowid
        row = conn.execute("SELECT * FROM reviews WHERE id = ?", (review_id,)).fetchone()

    return {"review": review_row_to_dict(row)}


def _preview_lines(items: Iterable[str], limit: int = 5) -> list[str]:
    preview = []
    for item in items:
        if item.strip():
            preview.append(item.strip())
        if len(preview) >= limit:
            break
    return preview


@app.get("/health")
async def health() -> dict:
    with get_connection() as conn:
        book_count = conn.execute("SELECT COUNT(*) FROM books").fetchone()[0]
        review_count = conn.execute("SELECT COUNT(*) FROM reviews").fetchone()[0]
    return {
        "status": "正常",
        "books": book_count,
        "reviews": review_count,
        "sample": _preview_lines(["fastapi", "ebook", "ready"]),
    }
