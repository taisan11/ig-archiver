"""Minimal, read-only browser for already-archived Instagram media."""

from __future__ import annotations

import html
import re
import sqlite3
from contextlib import closing
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlencode, urlsplit

KINDS = ("post", "story", "instant")
MEDIA_TYPES = {
    ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png",
    ".webp": "image/webp", ".gif": "image/gif", ".mp4": "video/mp4",
    ".mov": "video/quicktime", ".m4v": "video/x-m4v", ".webm": "video/webm",
}
PAGE_SIZE = 48
RANGE = re.compile(r"bytes=(\d*)-(\d*)\Z")


def _escape(value: object) -> str:
    return html.escape(str(value if value is not None else ""), quote=True)


def _safe_media(path_value: str | None, root: Path) -> Path | None:
    """Reject external paths, symlinks escaping the archive and non-media files."""
    if not path_value:
        return None
    path = Path(path_value).resolve()
    if path.suffix.lower() not in MEDIA_TYPES or not path.is_relative_to(root) or not path.is_file():
        return None
    return path


def _db_connection(path: Path) -> sqlite3.Connection:
    # The UI cannot create or write SQLite files.
    con = sqlite3.connect(f"{path.as_uri()}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    return con


def create_handler(database: Path, archive_root: Path) -> type[BaseHTTPRequestHandler]:
    database, root = database.resolve(), archive_root.resolve()

    class ArchiveHandler(BaseHTTPRequestHandler):
        server_version = "ig-archiver-viewer"

        def _send_headers(self, status: int, content_type: str, length: int, **headers: str) -> None:
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(length))
            self.send_header("Cache-Control", "private, no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("X-Frame-Options", "DENY")
            self.send_header("Referrer-Policy", "no-referrer")
            self.send_header(
                "Content-Security-Policy",
                "default-src 'none'; img-src 'self'; media-src 'self'; "
                "form-action 'self'; base-uri 'none'; frame-ancestors 'none'",
            )
            for key, value in headers.items():
                self.send_header(key.replace("_", "-"), value)
            self.end_headers()

        def _text(self, status: int, message: str, head: bool) -> None:
            content = message.encode("utf-8")
            self._send_headers(status, "text/plain; charset=utf-8", len(content))
            if not head:
                self.wfile.write(content)

        def do_GET(self) -> None:
            self._dispatch(head=False)

        def do_HEAD(self) -> None:
            self._dispatch(head=True)

        def _dispatch(self, head: bool) -> None:
            # Prevent DNS rebinding when bound to localhost.
            if self.server.server_address[0] in {"127.0.0.1", "::1", "localhost"}:
                host = self.headers.get("Host", "").partition(":")[0].lower()
                if host not in {"127.0.0.1", "localhost", "[::1]"}:
                    self._text(403, "Forbidden host", head)
                    return

            url = urlsplit(self.path)
            try:
                params = parse_qs(url.query, max_num_fields=10)
            except ValueError:
                self._text(400, "Invalid query", head)
                return
            if url.path == "/":
                self._index(params, head)
            elif url.path == "/media":
                self._media(params, head)
            else:
                self._text(404, "Not found", head)

        def _index(self, params: dict[str, list[str]], head: bool) -> None:
            kind = params.get("kind", [""])[0]
            username = params.get("user", [""])[0].strip()[:80]
            if kind not in (*KINDS, ""):
                self._text(400, "Invalid kind", head)
                return
            try:
                page = max(1, min(1000000, int(params.get("page", ["1"])[0])))
            except ValueError:
                page = 1

            filters: list[str] = []
            args: list[str] = []
            if kind:
                filters.append("kind = ?")
                args.append(kind)
            if username:
                filters.append("username = ?")
                args.append(username)
            where = " WHERE " + " AND ".join(filters) if filters else ""
            try:
                with closing(_db_connection(database)) as con:
                    count = con.execute("SELECT COUNT(*) FROM seen" + where, args).fetchone()[0]
                    rows = con.execute(
                        "SELECT kind, item_key, username, archived_at, path FROM seen"
                        + where + " ORDER BY archived_at DESC, item_key DESC LIMIT ? OFFSET ?",
                        [*args, PAGE_SIZE, (page - 1) * PAGE_SIZE],
                    ).fetchall()
            except sqlite3.Error:
                self._text(503, "Archive database unavailable", head)
                return

            def link(next_page: int) -> str:
                return "/?" + urlencode({"kind": kind, "user": username, "page": next_page})

            filters_html = (
                '<form method="get" action="/">'
                '<label>種類 <select name="kind">'
                + "".join(
                    f'<option value="{_escape(value)}"{" selected" if value == kind else ""}>'
                    f'{_escape(label)}</option>'
                    for value, label in (
                        ("", "すべて"), ("post", "投稿"), ("story", "ストーリー"), ("instant", "インスタント")
                    )
                )
                + '</select></label> '
                + f'<label>ユーザー <input name="user" value="{_escape(username)}"></label> '
                + '<button type="submit">絞り込む</button> <a href="/">リセット</a></form>'
            )
            items = []
            for row in rows:
                file = _safe_media(row["path"], root)
                media_url = "/media?" + urlencode({"kind": row["kind"], "id": row["item_key"]})
                if file is None:
                    preview = "ファイルなし"
                elif MEDIA_TYPES[file.suffix.lower()].startswith("image/"):
                    preview = (
                        f'<a href="{_escape(media_url)}">'
                        f'<img src="{_escape(media_url)}" loading="lazy" width="180" alt="preview"></a>'
                    )
                else:
                    preview = f'<video controls preload="metadata" width="240" src="{_escape(media_url)}"></video>'
                action = f'<a href="{_escape(media_url)}">ファイルを開く</a>' if file else "—"
                items.append(
                    "<tr>"
                    f'<td>{preview}</td><td>{_escape(row["kind"])}</td>'
                    f'<td>{_escape(row["username"])}</td>'
                    f'<td>{_escape(row["archived_at"])}</td>'
                    f'<td>{_escape(row["item_key"])}</td><td>{action}</td>'
                    "</tr>"
                )

            previous = f'<a href="{_escape(link(page - 1))}">前へ</a>' if page > 1 else "前へ"
            following = f'<a href="{_escape(link(page + 1))}">次へ</a>' if page * PAGE_SIZE < count else "次へ"
            document = (
                '<!doctype html><html lang="ja"><head><meta charset="utf-8">'
                '<meta name="viewport" content="width=device-width, initial-scale=1">'
                '<title>IG Archive</title></head><body>'
                '<h1>IG Archive</h1><p>保存済みメディアの閲覧専用ページです。</p>'
                + filters_html
                + f'<p>{count} 件 / {page} ページ目（1ページ最大 {PAGE_SIZE} 件）</p>'
                + '<table border="1" cellpadding="6"><thead><tr>'
                '<th>プレビュー</th><th>種類</th><th>ユーザー</th>'
                '<th>保存日時 (UTC)</th><th>ID</th><th>ファイル</th>'
                '</tr></thead><tbody>'
                + "".join(items)
                + "</tbody></table>"
                + f"<p>{previous} | {following}</p>"
                + "</body></html>"
            ).encode("utf-8")
            self._send_headers(200, "text/html; charset=utf-8", len(document))
            if not head:
                self.wfile.write(document)

        def _media(self, params: dict[str, list[str]], head: bool) -> None:
            kind = params.get("kind", [""])[0]
            item_key = params.get("id", [""])[0]
            if kind not in KINDS or not item_key or len(item_key) > 200:
                self._text(404, "Not found", head)
                return
            try:
                with closing(_db_connection(database)) as con:
                    row = con.execute(
                        "SELECT path FROM seen WHERE kind=? AND item_key=?", (kind, item_key)
                    ).fetchone()
            except sqlite3.Error:
                self._text(503, "Archive database unavailable", head)
                return
            path = _safe_media(row["path"] if row else None, root)
            if path is None:
                self._text(404, "Media not found", head)
                return
            try:
                with path.open("rb") as media:
                    size = path.stat().st_size
                    start, end, status = 0, size - 1, 200
                    range_header = self.headers.get("Range")
                    if range_header:
                        match = RANGE.fullmatch(range_header)
                        if (
                            not match or not any(match.groups()) or size == 0
                            or any(len(group) > 20 for group in match.groups())
                        ):
                            self._send_headers(416, "text/plain", 0, Content_Range=f"bytes */{size}")
                            return
                        first, last = match.groups()
                        if first:
                            start = int(first)
                            end = min(int(last), size - 1) if last else size - 1
                        else:
                            start = max(0, size - int(last))
                        if start > end or start >= size:
                            self._send_headers(416, "text/plain", 0, Content_Range=f"bytes */{size}")
                            return
                        status = 206
                    headers = {"Accept_Ranges": "bytes"}
                    if status == 206:
                        headers["Content_Range"] = f"bytes {start}-{end}/{size}"
                    self._send_headers(
                        status, MEDIA_TYPES[path.suffix.lower()],
                        end - start + 1 if size else 0, **headers
                    )
                    if head:
                        return
                    media.seek(start)
                    remaining = end - start + 1 if size else 0
                    while remaining:
                        chunk = media.read(min(65536, remaining))
                        if not chunk:
                            break
                        self.wfile.write(chunk)
                        remaining -= len(chunk)
            except (BrokenPipeError, ConnectionResetError):
                return
            except OSError:
                self.close_connection = True

    return ArchiveHandler


def serve_archive(
    database: Path, archive_root: Path, host: str = "127.0.0.1", port: int = 8765
) -> None:
    if not database.is_file():
        raise FileNotFoundError(f"Archive database not found: {database}")
    with ThreadingHTTPServer((host, port), create_handler(database, archive_root)) as server:
        print(f"Archive viewer: http://{host}:{server.server_port}/ (Ctrl+C to stop)")
        server.serve_forever()
