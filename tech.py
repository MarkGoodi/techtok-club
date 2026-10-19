import hashlib
import hmac
import json
import logging
import os
import secrets
import sqlite3
import time
from collections import defaultdict, deque
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


ROOT = Path(__file__).resolve().parent
OPENAI_URL = "https://api.openai.com/v1/responses"
MODEL = os.environ.get("OPENAI_MODEL", "gpt-4o-mini")
HOST = os.environ.get("HOST", "127.0.0.1")
PORT = int(os.environ.get("PORT", "8000"))
DATABASE_URL = os.environ.get("DATABASE_URL")
MODERATION_PASSWORD = os.environ.get("MODERATION_PASSWORD")
SUPABASE_URL = os.environ.get("SUPABASE_URL", "").rstrip("/")
SUPABASE_ANON_KEY = os.environ.get("SUPABASE_ANON_KEY", "")
MAX_BODY_BYTES = 16_384
MAX_SUBMISSION_BYTES = 4_096
RATE_LIMIT = 8
RATE_WINDOW_SECONDS = 60
VISITOR_REQUESTS: dict[str, deque[float]] = defaultdict(deque)
STATIC_FILES = {
    "/": ("tech.html", "text/html; charset=utf-8"),
    "/tech.html": ("tech.html", "text/html; charset=utf-8"),
    "/tech.css": ("tech.css", "text/css; charset=utf-8"),
    "/tech.js": ("tech.js", "text/javascript; charset=utf-8"),
    "/board.js": ("board.js", "text/javascript; charset=utf-8"),
    "/auth.js": ("auth.js", "text/javascript; charset=utf-8"),
    "/moderate": ("moderate.html", "text/html; charset=utf-8"),
    "/moderate.html": ("moderate.html", "text/html; charset=utf-8"),
    "/moderate.js": ("moderate.js", "text/javascript; charset=utf-8"),
}

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")


def connect_database():
    if DATABASE_URL:
        import psycopg

        return psycopg.connect(DATABASE_URL, sslmode="require", connect_timeout=8)
    if HOST == "127.0.0.1":
        connection = sqlite3.connect(ROOT / "techtok.db", timeout=8)
        connection.execute("PRAGMA foreign_keys = ON")
        return connection
    raise RuntimeError("Set DATABASE_URL to a persistent PostgreSQL database before deployment.")


def execute(connection, query: str, params: tuple = ()):
    if isinstance(connection, sqlite3.Connection):
        return connection.execute(query, params)
    return connection.execute(query.replace("?", "%s"), params)


def initialize_database() -> None:
    connection = connect_database()
    try:
        if isinstance(connection, sqlite3.Connection):
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS problems (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name TEXT NOT NULL,
                    title TEXT NOT NULL,
                    description TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'pending'
                        CHECK (status IN ('pending', 'approved')),
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                );
                CREATE TABLE IF NOT EXISTS comments (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    problem_id INTEGER NOT NULL REFERENCES problems(id) ON DELETE CASCADE,
                    name TEXT NOT NULL,
                    body TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'pending'
                        CHECK (status IN ('pending', 'approved')),
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                );
                CREATE TABLE IF NOT EXISTS member_profiles (
                    user_id TEXT PRIMARY KEY,
                    display_name TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS problems_public_order
                    ON problems(status, created_at DESC);
                CREATE INDEX IF NOT EXISTS comments_problem_order
                    ON comments(problem_id, status, created_at);
                """
            )
            problem_columns = {row[1] for row in connection.execute("PRAGMA table_info(problems)")}
            if "owner_id" not in problem_columns:
                connection.execute("ALTER TABLE problems ADD COLUMN owner_id TEXT")
            comment_columns = {row[1] for row in connection.execute("PRAGMA table_info(comments)")}
            if "owner_id" not in comment_columns:
                connection.execute("ALTER TABLE comments ADD COLUMN owner_id TEXT")
            connection.execute(
                "CREATE INDEX IF NOT EXISTS problems_owner_order ON problems(owner_id, created_at DESC)"
            )
            connection.execute(
                "CREATE INDEX IF NOT EXISTS comments_owner_order ON comments(owner_id, created_at DESC)"
            )
        else:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    CREATE TABLE IF NOT EXISTS problems (
                        id BIGSERIAL PRIMARY KEY,
                        name VARCHAR(80) NOT NULL,
                        title VARCHAR(120) NOT NULL,
                        description VARCHAR(2000) NOT NULL,
                        status VARCHAR(10) NOT NULL DEFAULT 'pending'
                            CHECK (status IN ('pending', 'approved')),
                        created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                    )
                    """
                )
                cursor.execute(
                    """
                    CREATE TABLE IF NOT EXISTS comments (
                        id BIGSERIAL PRIMARY KEY,
                        problem_id BIGINT NOT NULL REFERENCES problems(id) ON DELETE CASCADE,
                        name VARCHAR(80) NOT NULL,
                        body VARCHAR(1500) NOT NULL,
                        status VARCHAR(10) NOT NULL DEFAULT 'pending'
                            CHECK (status IN ('pending', 'approved')),
                        created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                    )
                    """
                )
                cursor.execute("ALTER TABLE problems ADD COLUMN IF NOT EXISTS owner_id TEXT")
                cursor.execute("ALTER TABLE comments ADD COLUMN IF NOT EXISTS owner_id TEXT")
                cursor.execute(
                    """
                    CREATE TABLE IF NOT EXISTS member_profiles (
                        user_id TEXT PRIMARY KEY,
                        display_name VARCHAR(80) NOT NULL
                    )
                    """
                )
                cursor.execute(
                    "CREATE INDEX IF NOT EXISTS problems_public_order ON problems(status, created_at DESC)"
                )
                cursor.execute(
                    "CREATE INDEX IF NOT EXISTS comments_problem_order ON comments(problem_id, status, created_at)"
                )
                cursor.execute(
                    "CREATE INDEX IF NOT EXISTS problems_owner_order ON problems(owner_id, created_at DESC)"
                )
                cursor.execute(
                    "CREATE INDEX IF NOT EXISTS comments_owner_order ON comments(owner_id, created_at DESC)"
                )
        connection.commit()
    finally:
        connection.close()


class TechTokHandler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        path = self.path.split("?", 1)[0]
        if path == "/health":
            self._send_json(200, {"status": "ok"})
            return

        if path == "/api/problems":
            self._get_problems()
            return
        if path == "/auth-config.js":
            self._auth_config()
            return
        if path == "/api/account/profile":
            user = self._require_user()
            if user:
                self._get_profile(user)
            return
        if path == "/api/account/items":
            user = self._require_user()
            if user:
                self._get_account_items(user)
            return
        if path == "/api/moderation/queue":
            if not self._is_moderator():
                self._send_json(401, {"error": "Sign in to view the moderation queue."})
                return
            self._get_moderation_queue()
            return
        if path == "/api/moderation/session":
            self._send_json(200, {"authenticated": self._is_moderator()})
            return

        file_entry = STATIC_FILES.get(path)
        if file_entry is None:
            self.send_error(404)
            return

        filename, content_type = file_entry
        content = (ROOT / filename).read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(content)))
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(content)

    def do_POST(self) -> None:
        if self.path == "/api/account/profile":
            user = self._require_user()
            if user:
                self._save_profile(user)
            return
        if self.path == "/api/problems":
            user = self._require_user()
            if user:
                self._submit_problem(user)
            return
        if self.path.startswith("/api/problems/") and self.path.endswith("/comments"):
            user = self._require_user()
            if user:
                self._submit_comment(user)
            return
        if self.path == "/api/moderation/login":
            self._moderator_login()
            return
        if self.path == "/api/moderation/logout":
            self._moderator_logout()
            return
        if self.path == "/api/moderation/action":
            self._moderation_action()
            return
        if self.path != "/api/chat":
            self.send_error(404)
            return
        if self.headers.get_content_type() != "application/json":
            self._send_json(415, {"error": "Send the chat message as JSON."})
            return

        try:
            content_length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            self._send_json(400, {"error": "The request size is invalid."})
            return
        if content_length < 1 or content_length > MAX_BODY_BYTES:
            self._send_json(413, {"error": "That message is too large."})
            return

        if not self._within_rate_limit():
            self._send_json(429, {"error": "Please wait a moment before sending another message."})
            return

        try:
            payload = json.loads(self.rfile.read(content_length))
        except (json.JSONDecodeError, UnicodeDecodeError):
            self._send_json(400, {"error": "The chat request wasn’t valid JSON."})
            return

        messages = payload.get("messages") if isinstance(payload, dict) else None
        if not isinstance(messages, list) or not 1 <= len(messages) <= 10:
            self._send_json(400, {"error": "Send between 1 and 10 chat messages."})
            return

        clean_messages: list[dict[str, str]] = []
        for message in messages:
            if (
                not isinstance(message, dict)
                or not isinstance(message.get("role"), str)
                or message["role"] not in {"user", "assistant"}
            ):
                self._send_json(400, {"error": "The chat history contains an invalid message."})
                return
            text = message.get("content")
            if not isinstance(text, str) or not text.strip() or len(text) > 3_000:
                self._send_json(400, {"error": "Each message must contain 1 to 3,000 characters."})
                return
            clean_messages.append({"role": message["role"], "content": text.strip()})

        if clean_messages[-1]["role"] != "user":
            self._send_json(400, {"error": "The latest chat message must be from you."})
            return

        api_key = os.environ.get("OPENAI_API_KEY")
        if not api_key:
            self._send_json(
                503,
                {"error": "AI chat isn’t configured yet. Please contact us on WhatsApp instead."},
            )
            return

        request_body = json.dumps(
            {
                "model": MODEL,
                "store": False,
                "max_output_tokens": 600,
                "instructions": (
                    "You are the friendly TechTok Club website assistant. Help with technology, "
                    "learning, and creative project questions in a clear, concise, welcoming way. "
                    "You can discuss general topics, but you are not infallible: be honest about "
                    "uncertainty and do not claim you can answer every possible question. Only "
                    "state club-specific facts that are present on the website: TechTok Club is "
                    "a community for curious people to explore technology, share ideas, learn "
                    "together, and build projects. Do not invent event dates, membership rules, "
                    "or services. For private club details, invite the visitor to contact the "
                    "club on WhatsApp. Do not provide professional medical, legal, or financial "
                    "advice. Never request sensitive personal information."
                ),
                "input": clean_messages,
            }
        ).encode("utf-8")
        request = Request(
            OPENAI_URL,
            data=request_body,
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            method="POST",
        )

        try:
            with urlopen(request, timeout=35) as response:
                result = json.loads(response.read())
        except HTTPError as error:
            logging.error("OpenAI request failed with HTTP status %s", error.code)
            self._send_json(502, {"error": "The AI assistant is temporarily unavailable. Try again soon."})
            return
        except (URLError, TimeoutError) as error:
            logging.error("Could not reach OpenAI: %s", error)
            self._send_json(502, {"error": "The AI assistant couldn’t connect. Try again soon."})
            return
        except (json.JSONDecodeError, UnicodeDecodeError):
            logging.error("OpenAI returned an invalid response")
            self._send_json(502, {"error": "The AI assistant returned an invalid response. Try again soon."})
            return

        if not isinstance(result, dict):
            logging.error("OpenAI returned an invalid response shape")
            self._send_json(502, {"error": "The AI assistant returned an invalid response. Try again soon."})
            return

        output_text = result.get("output_text", "")
        reply = output_text.strip() if isinstance(output_text, str) else ""
        if not reply:
            output = result.get("output", [])
            if isinstance(output, list):
                reply = "\n".join(
                    item["text"]
                    for block in output
                    if isinstance(block, dict) and block.get("type") == "message"
                    and isinstance(block.get("content"), list)
                    for item in block["content"]
                    if isinstance(item, dict) and item.get("type") == "output_text"
                    and isinstance(item.get("text"), str)
                ).strip()
        if not reply:
            logging.error("OpenAI response did not contain an answer")
            self._send_json(502, {"error": "The AI assistant couldn’t create a reply. Try again soon."})
            return
        self._send_json(200, {"reply": reply})

    def do_PATCH(self) -> None:
        if self.path == "/api/account/items":
            user = self._require_user()
            if user:
                self._update_account_item(user)
            return
        self.send_error(404)

    def do_DELETE(self) -> None:
        if self.path == "/api/account/items":
            user = self._require_user()
            if user:
                self._delete_account_item(user)
            return
        self.send_error(404)

    def _auth_config(self) -> None:
        content = (
            "window.TECHTOK_SUPABASE_CONFIG = "
            + json.dumps({"url": SUPABASE_URL, "anonKey": SUPABASE_ANON_KEY})
            + ";"
        ).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/javascript; charset=utf-8")
        self.send_header("Content-Length", str(len(content)))
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(content)

    def _require_user(self) -> dict | None:
        authorization = self.headers.get("Authorization", "")
        scheme, _, token = authorization.partition(" ")
        if scheme.lower() != "bearer" or not token or len(token) > 8192:
            self._send_json(401, {"error": "Sign in with Google to continue."})
            return None
        if not SUPABASE_URL.startswith("https://") or not SUPABASE_ANON_KEY:
            self._send_json(503, {"error": "Google sign-in is not configured on this website yet."})
            return None
        request = Request(
            f"{SUPABASE_URL}/auth/v1/user",
            headers={"apikey": SUPABASE_ANON_KEY, "Authorization": f"Bearer {token}"},
        )
        try:
            with urlopen(request, timeout=8) as response:
                user = json.loads(response.read())
        except HTTPError as error:
            if error.code in {400, 401, 403}:
                self._send_json(401, {"error": "Your sign-in has expired. Please sign in again."})
            else:
                logging.error("Supabase auth verification failed with HTTP status %s", error.code)
                self._send_json(503, {"error": "Sign-in verification is temporarily unavailable."})
            return None
        except (URLError, TimeoutError, ValueError) as error:
            logging.error("Could not verify Supabase user: %s", error)
            self._send_json(503, {"error": "Sign-in verification is temporarily unavailable."})
            return None
        except (json.JSONDecodeError, UnicodeDecodeError):
            logging.error("Supabase returned an invalid user response")
            self._send_json(503, {"error": "Sign-in verification is temporarily unavailable."})
            return None
        if not isinstance(user, dict) or not isinstance(user.get("id"), str):
            self._send_json(401, {"error": "Your sign-in could not be verified."})
            return None
        return user

    def _get_profile(self, user: dict) -> None:
        try:
            connection = connect_database()
            try:
                profile = execute(
                    connection,
                    "SELECT display_name FROM member_profiles WHERE user_id = ?",
                    (user["id"],),
                ).fetchone()
                display_name = profile[0] if profile else self._member_display_name(connection, user)
            finally:
                connection.close()
            self._send_json(200, {"display_name": display_name})
        except Exception:
            logging.exception("Could not load member profile")
            self._send_json(503, {"error": "Your profile is temporarily unavailable."})

    def _save_profile(self, user: dict) -> None:
        payload = self._request_json()
        if payload is None:
            return
        display_name = self._trimmed_text(payload, "display_name", 2, 80)
        if not display_name:
            self._send_json(400, {"error": "Your display name must be between 2 and 80 characters."})
            return
        try:
            connection = connect_database()
            try:
                execute(
                    connection,
                    """
                    INSERT INTO member_profiles (user_id, display_name) VALUES (?, ?)
                    ON CONFLICT(user_id) DO UPDATE SET display_name = excluded.display_name
                    """,
                    (user["id"], display_name),
                )
                connection.commit()
            finally:
                connection.close()
            self._send_json(200, {"display_name": display_name})
        except Exception:
            logging.exception("Could not save member profile")
            self._send_json(503, {"error": "Your profile could not be saved. Please try again."})

    def _get_account_items(self, user: dict) -> None:
        try:
            connection = connect_database()
            try:
                problems = execute(
                    connection,
                    """
                    SELECT id, title, description, status, created_at
                    FROM problems WHERE owner_id = ? ORDER BY created_at DESC LIMIT 100
                    """,
                    (user["id"],),
                ).fetchall()
                comments = execute(
                    connection,
                    """
                    SELECT comments.id, comments.problem_id, comments.body, comments.status,
                           comments.created_at, problems.title
                    FROM comments JOIN problems ON problems.id = comments.problem_id
                    WHERE comments.owner_id = ? ORDER BY comments.created_at DESC LIMIT 100
                    """,
                    (user["id"],),
                ).fetchall()
            finally:
                connection.close()
            self._send_json(
                200,
                {
                    "problems": [
                        {"id": row[0], "title": row[1], "description": row[2], "status": row[3], "created_at": str(row[4])}
                        for row in problems
                    ],
                    "comments": [
                        {"id": row[0], "problem_id": row[1], "body": row[2], "status": row[3], "created_at": str(row[4]), "problem_title": row[5]}
                        for row in comments
                    ],
                },
            )
        except Exception:
            logging.exception("Could not load member submissions")
            self._send_json(503, {"error": "Your submissions are temporarily unavailable."})

    def _update_account_item(self, user: dict) -> None:
        payload = self._request_json()
        if payload is None:
            return
        kind = payload.get("kind")
        item_id = payload.get("id")
        if kind not in {"problem", "comment"} or not isinstance(item_id, int) or isinstance(item_id, bool) or item_id < 1:
            self._send_json(400, {"error": "Choose a valid submission to update."})
            return
        if kind == "problem":
            title = self._trimmed_text(payload, "title", 5, 120)
            description = self._trimmed_text(payload, "description", 20, 2000)
            if not title or not description:
                self._send_json(400, {"error": "Add a 5–120 character title and a 20–2,000 character description."})
                return
            query = "UPDATE problems SET title = ?, description = ?, status = 'pending' WHERE id = ? AND owner_id = ?"
            params = (title, description, item_id, user["id"])
        else:
            body = self._trimmed_text(payload, "body", 2, 1500)
            if not body:
                self._send_json(400, {"error": "Write a comment between 2 and 1,500 characters."})
                return
            query = "UPDATE comments SET body = ?, status = 'pending' WHERE id = ? AND owner_id = ?"
            params = (body, item_id, user["id"])
        try:
            connection = connect_database()
            try:
                cursor = execute(connection, query, params)
                changed = cursor.rowcount > 0
                connection.commit()
            finally:
                connection.close()
            if not changed:
                self._send_json(404, {"error": "That submission was not found on your account."})
                return
            self._send_json(200, {"message": "Saved. Your edit is waiting for review before it appears publicly."})
        except Exception:
            logging.exception("Could not update member submission")
            self._send_json(503, {"error": "Your submission could not be updated. Please try again."})

    def _delete_account_item(self, user: dict) -> None:
        payload = self._request_json()
        if payload is None:
            return
        kind = payload.get("kind")
        item_id = payload.get("id")
        if kind not in {"problem", "comment"} or not isinstance(item_id, int) or isinstance(item_id, bool) or item_id < 1:
            self._send_json(400, {"error": "Choose a valid submission to delete."})
            return
        table = "problems" if kind == "problem" else "comments"
        try:
            connection = connect_database()
            try:
                cursor = execute(
                    connection,
                    f"DELETE FROM {table} WHERE id = ? AND owner_id = ?",
                    (item_id, user["id"]),
                )
                changed = cursor.rowcount > 0
                connection.commit()
            finally:
                connection.close()
            if not changed:
                self._send_json(404, {"error": "That submission was not found on your account."})
                return
            self._send_json(200, {"message": "Your submission was deleted."})
        except Exception:
            logging.exception("Could not delete member submission")
            self._send_json(503, {"error": "Your submission could not be deleted. Please try again."})

    def _request_json(self, max_bytes: int = MAX_SUBMISSION_BYTES) -> dict | None:
        if self.headers.get_content_type() != "application/json":
            self._send_json(415, {"error": "Send the request as JSON."})
            return None
        try:
            content_length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            self._send_json(400, {"error": "The request size is invalid."})
            return None
        if content_length < 1 or content_length > max_bytes:
            self._send_json(413, {"error": "The request is empty or too large."})
            return None
        try:
            payload = json.loads(self.rfile.read(content_length))
        except (json.JSONDecodeError, UnicodeDecodeError):
            self._send_json(400, {"error": "The request wasn’t valid JSON."})
            return None
        if not isinstance(payload, dict):
            self._send_json(400, {"error": "The request must be a JSON object."})
            return None
        return payload

    @staticmethod
    def _trimmed_text(payload: dict, field: str, minimum: int, maximum: int) -> str | None:
        value = payload.get(field)
        if not isinstance(value, str):
            return None
        value = value.strip()
        return value if minimum <= len(value) <= maximum else None

    def _get_problems(self) -> None:
        try:
            connection = connect_database()
            try:
                problems = execute(
                    connection,
                    """
                    SELECT id, name, title, description, created_at
                    FROM problems WHERE status = 'approved'
                    ORDER BY created_at DESC LIMIT 100
                    """,
                ).fetchall()
                problem_ids = [problem[0] for problem in problems]
                comments_by_problem: dict[int, list[dict]] = {problem_id: [] for problem_id in problem_ids}
                if problem_ids:
                    placeholders = ",".join("?" for _ in problem_ids)
                    comments = execute(
                        connection,
                        f"""
                        SELECT id, problem_id, name, body, created_at
                        FROM comments
                        WHERE status = 'approved' AND problem_id IN ({placeholders})
                        ORDER BY created_at ASC
                        """,
                        tuple(problem_ids),
                    ).fetchall()
                    for comment in comments:
                        comments_by_problem[comment[1]].append(
                            {
                                "id": comment[0],
                                "name": comment[2],
                                "body": comment[3],
                                "created_at": str(comment[4]),
                            }
                        )
                self._send_json(
                    200,
                    {
                        "problems": [
                            {
                                "id": problem[0],
                                "name": problem[1],
                                "title": problem[2],
                                "description": problem[3],
                                "created_at": str(problem[4]),
                                "comments": comments_by_problem[problem[0]],
                            }
                            for problem in problems
                        ]
                    },
                )
            finally:
                connection.close()
        except Exception:
            logging.exception("Could not load the public problem board")
            self._send_json(503, {"error": "The community board is temporarily unavailable."})

    def _member_display_name(self, connection, user: dict) -> str:
        profile = execute(
            connection,
            "SELECT display_name FROM member_profiles WHERE user_id = ?",
            (user["id"],),
        ).fetchone()
        metadata = user.get("user_metadata")
        metadata_name = metadata.get("full_name") or metadata.get("name") if isinstance(metadata, dict) else ""
        email_name = user.get("email", "").partition("@")[0]
        return (profile[0] if profile else metadata_name or email_name or "Community member")[:80]

    def _submit_problem(self, user: dict) -> None:
        payload = self._request_json()
        if payload is None:
            return
        if payload.get("website"):
            self._send_json(202, {"message": "Thanks. Your problem is waiting for review."})
            return
        title = self._trimmed_text(payload, "title", 5, 120)
        description = self._trimmed_text(payload, "description", 20, 2000)
        if not title or not description:
            self._send_json(400, {"error": "Add a 5–120 character title and a 20–2,000 character description."})
            return
        try:
            connection = connect_database()
            try:
                cursor = execute(
                    connection,
                    "INSERT INTO problems (name, title, description, owner_id) VALUES (?, ?, ?, ?)"
                    if isinstance(connection, sqlite3.Connection)
                    else "INSERT INTO problems (name, title, description, owner_id) VALUES (?, ?, ?, ?) RETURNING id",
                    (self._member_display_name(connection, user), title, description, user["id"]),
                )
                if isinstance(connection, sqlite3.Connection):
                    problem_id = cursor.lastrowid
                else:
                    problem_id = cursor.fetchone()[0]
                connection.commit()
            finally:
                connection.close()
            self._send_json(
                201,
                {"id": problem_id, "message": "Thanks! Your problem is submitted for review before it appears publicly."},
            )
        except Exception:
            logging.exception("Could not save a community problem")
            self._send_json(503, {"error": "We couldn’t save your problem. Please try again later."})

    def _submit_comment(self, user: dict) -> None:
        parts = self.path.strip("/").split("/")
        if len(parts) != 4 or not parts[2].isdigit():
            self.send_error(404)
            return
        problem_id = int(parts[2])
        payload = self._request_json()
        if payload is None:
            return
        if payload.get("website"):
            self._send_json(202, {"message": "Thanks. Your comment is waiting for review."})
            return
        body = self._trimmed_text(payload, "body", 2, 1500)
        if not body:
            self._send_json(400, {"error": "Write a comment between 2 and 1,500 characters."})
            return
        try:
            connection = connect_database()
            try:
                problem = execute(
                    connection,
                    "SELECT status FROM problems WHERE id = ?",
                    (problem_id,),
                ).fetchone()
                if not problem or problem[0] != "approved":
                    self._send_json(404, {"error": "That public problem could not be found."})
                    return
                execute(
                    connection,
                    "INSERT INTO comments (problem_id, name, body, owner_id) VALUES (?, ?, ?, ?)",
                    (problem_id, self._member_display_name(connection, user), body, user["id"]),
                )
                connection.commit()
            finally:
                connection.close()
            self._send_json(201, {"message": "Thanks! Your comment is submitted for review before it appears publicly."})
        except Exception:
            logging.exception("Could not save a community comment")
            self._send_json(503, {"error": "We couldn’t save your comment. Please try again later."})

    def _moderator_login(self) -> None:
        if not self._within_rate_limit():
            self._send_json(429, {"error": "Too many sign-in attempts. Please wait a minute and try again."})
            return
        payload = self._request_json()
        if payload is None:
            return
        if not MODERATION_PASSWORD or len(MODERATION_PASSWORD) < 16:
            self._send_json(503, {"error": "Moderation is not configured. Set MODERATION_PASSWORD on the server."})
            return
        password = payload.get("password")
        if not isinstance(password, str) or not hmac.compare_digest(password, MODERATION_PASSWORD):
            self._send_json(401, {"error": "The moderation password is incorrect."})
            return
        expires = int(time.time()) + 12 * 60 * 60
        nonce = secrets.token_urlsafe(18)
        token = f"{expires}.{nonce}"
        signature = hmac.new(MODERATION_PASSWORD.encode(), token.encode(), hashlib.sha256).hexdigest()
        secure = "; Secure" if self.headers.get("X-Forwarded-Proto") == "https" else ""
        self._send_json(
            200,
            {"authenticated": True},
            extra_headers=[
                (
                    "Set-Cookie",
                    f"ttc_moderation={token}.{signature}; Path=/api/moderation; HttpOnly; SameSite=Strict; Max-Age=43200{secure}",
                )
            ],
        )

    def _is_moderator(self) -> bool:
        if not MODERATION_PASSWORD:
            return False
        cookies = self.headers.get("Cookie", "")
        cookie_value = next(
            (item.partition("=")[2] for item in cookies.split("; ") if item.startswith("ttc_moderation=")),
            "",
        )
        try:
            expires, nonce, signature = cookie_value.split(".", 2)
            if int(expires) < int(time.time()) or len(nonce) < 16:
                return False
        except (ValueError, AttributeError):
            return False
        token = f"{expires}.{nonce}"
        expected = hmac.new(MODERATION_PASSWORD.encode(), token.encode(), hashlib.sha256).hexdigest()
        return hmac.compare_digest(signature, expected)

    def _moderator_logout(self) -> None:
        if not self._is_moderator():
            self._send_json(401, {"error": "Sign in to access moderation."})
            return
        self._send_json(
            200,
            {"authenticated": False},
            extra_headers=[
                ("Set-Cookie", "ttc_moderation=; Path=/api/moderation; HttpOnly; SameSite=Strict; Max-Age=0")
            ],
        )

    def _get_moderation_queue(self) -> None:
        try:
            connection = connect_database()
            try:
                problems = execute(
                    connection,
                    """
                    SELECT id, name, title, description, created_at
                    FROM problems WHERE status = 'pending' ORDER BY created_at ASC
                    """,
                ).fetchall()
                comments = execute(
                    connection,
                    """
                    SELECT comments.id, comments.problem_id, comments.name, comments.body,
                           comments.created_at, problems.title
                    FROM comments JOIN problems ON problems.id = comments.problem_id
                    WHERE comments.status = 'pending' AND problems.status = 'approved'
                    ORDER BY comments.created_at ASC
                    """,
                ).fetchall()
            finally:
                connection.close()
            self._send_json(
                200,
                {
                    "problems": [
                        {"id": row[0], "name": row[1], "title": row[2], "description": row[3], "created_at": str(row[4])}
                        for row in problems
                    ],
                    "comments": [
                        {"id": row[0], "problem_id": row[1], "name": row[2], "body": row[3], "created_at": str(row[4]), "problem_title": row[5]}
                        for row in comments
                    ],
                },
            )
        except Exception:
            logging.exception("Could not load the moderation queue")
            self._send_json(503, {"error": "The moderation queue is temporarily unavailable."})

    def _moderation_action(self) -> None:
        if not self._is_moderator():
            self._send_json(401, {"error": "Sign in to access moderation."})
            return
        origin = self.headers.get("Origin")
        scheme = "https" if self.headers.get("X-Forwarded-Proto") == "https" else "http"
        if origin != f"{scheme}://{self.headers.get('Host')}" or self.headers.get("X-Moderation-Action") != "true":
            self._send_json(403, {"error": "The moderation request origin was rejected."})
            return
        payload = self._request_json()
        if payload is None:
            return
        kind = payload.get("kind")
        action = payload.get("action")
        item_id = payload.get("id")
        if kind not in {"problem", "comment"} or action not in {"approve", "delete"}:
            self._send_json(400, {"error": "Choose a valid moderation action."})
            return
        if not isinstance(item_id, int) or isinstance(item_id, bool) or item_id < 1:
            self._send_json(400, {"error": "The item ID is invalid."})
            return
        table = "problems" if kind == "problem" else "comments"
        try:
            connection = connect_database()
            try:
                if action == "approve":
                    cursor = execute(
                        connection,
                        f"UPDATE {table} SET status = 'approved' WHERE id = ? AND status = 'pending'",
                        (item_id,),
                    )
                else:
                    cursor = execute(connection, f"DELETE FROM {table} WHERE id = ?", (item_id,))
                changed = cursor.rowcount > 0
                connection.commit()
            finally:
                connection.close()
            if not changed:
                self._send_json(404, {"error": "That pending item was not found."})
                return
            self._send_json(200, {"message": f"{kind.capitalize()} {action}d."})
        except Exception:
            logging.exception("Could not process a moderation action")
            self._send_json(503, {"error": "We couldn’t save that moderation action. Please try again."})

    def _within_rate_limit(self) -> bool:
        now = time.monotonic()
        visitor = self.client_address[0]
        requests = VISITOR_REQUESTS[visitor]
        while requests and now - requests[0] >= RATE_WINDOW_SECONDS:
            requests.popleft()
        if len(requests) >= RATE_LIMIT:
            return False
        requests.append(now)
        return True

    def _send_json(
        self, status: int, payload: dict, extra_headers: list[tuple[str, str]] | None = None
    ) -> None:
        content = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(content)))
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Cache-Control", "no-store")
        for name, value in extra_headers or []:
            self.send_header(name, value)
        self.end_headers()
        self.wfile.write(content)


if __name__ == "__main__":
    try:
        initialize_database()
    except Exception:
        logging.exception("The community board database could not be initialized")
        if HOST != "127.0.0.1":
            raise
        logging.warning("The local server will run, but community-board routes need a database.")
    server = ThreadingHTTPServer((HOST, PORT), TechTokHandler)
    print(f"TechTok Club is running at http://{HOST}:{PORT}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopping TechTok Club server.")
    finally:
        server.server_close()