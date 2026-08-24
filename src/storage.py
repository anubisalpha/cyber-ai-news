"""SQLite storage backend.

Replaces whole-file JSON read/write so unlimited history scales: filtering and
pagination happen in SQL, and refresh upserts rows instead of rewriting a file.
JSON is retained only as an import/export format.

List-valued fields (categories/regions/content_types) are stored as JSON text
and filtered with LIKE — good enough for the substring semantics the API uses.
"""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .models import Article

_COLUMNS = [
    "id", "title", "url", "source", "domain", "summary", "published",
    "first_seen", "weight", "severity", "categories", "regions",
    "content_types", "classified_by", "cluster_id", "duplicate_of",
]

_LIST_FIELDS = {"categories", "regions", "content_types"}


class Storage:
    def __init__(self, db_path: Path):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(str(self.db_path), timeout=10, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        # WAL lets readers (the API) proceed during a writer (a refresh);
        # busy_timeout waits instead of erroring if the db is briefly locked.
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute("PRAGMA busy_timeout=5000")
        self._init_schema()

    def _init_schema(self) -> None:
        self.conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS articles (
                id            TEXT PRIMARY KEY,
                title         TEXT,
                url           TEXT,
                source        TEXT,
                domain        TEXT,
                summary       TEXT,
                published     TEXT,
                first_seen    TEXT,
                weight        INTEGER,
                severity      TEXT,
                categories    TEXT,
                regions       TEXT,
                content_types TEXT,
                classified_by TEXT,
                cluster_id    TEXT,
                duplicate_of  TEXT
            );
            CREATE INDEX IF NOT EXISTS idx_published  ON articles(published);
            CREATE INDEX IF NOT EXISTS idx_domain     ON articles(domain);
            CREATE INDEX IF NOT EXISTS idx_severity   ON articles(severity);
            CREATE INDEX IF NOT EXISTS idx_first_seen ON articles(first_seen);

            CREATE TABLE IF NOT EXISTS source_health (
                name          TEXT PRIMARY KEY,
                last_checked  TEXT,
                last_success  TEXT,
                last_count    INTEGER,
                ok            INTEGER,
                error         TEXT,
                fail_streak   INTEGER DEFAULT 0
            );

            CREATE TABLE IF NOT EXISTS alert_hits (
                watchlist   TEXT,
                article_id  TEXT,
                notified_at TEXT,
                PRIMARY KEY (watchlist, article_id)
            );

            CREATE TABLE IF NOT EXISTS users (
                id                    INTEGER PRIMARY KEY AUTOINCREMENT,
                username              TEXT UNIQUE NOT NULL,
                password_hash         TEXT NOT NULL,
                is_admin              INTEGER NOT NULL DEFAULT 0,
                force_password_change INTEGER NOT NULL DEFAULT 0,
                auth_source           TEXT NOT NULL DEFAULT 'local',
                created_at            TEXT,
                last_login            TEXT
            );

            CREATE TABLE IF NOT EXISTS sources (
                name        TEXT PRIMARY KEY,
                label       TEXT,
                url         TEXT,
                type        TEXT,
                enabled     INTEGER NOT NULL DEFAULT 1,
                domain      TEXT,
                weight      INTEGER DEFAULT 50,
                tags        TEXT,
                created_at  TEXT,
                updated_at  TEXT,
                created_by  TEXT
            );

            CREATE TABLE IF NOT EXISTS source_changes (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                changed_at  TEXT,
                changed_by  TEXT,
                source_name TEXT,
                action      TEXT,
                detail      TEXT
            );
            CREATE INDEX IF NOT EXISTS idx_sc_source ON source_changes(source_name);
            CREATE INDEX IF NOT EXISTS idx_sc_time   ON source_changes(changed_at);

            CREATE TABLE IF NOT EXISTS user_sources (
                user_id     INTEGER NOT NULL,
                source_name TEXT NOT NULL,
                enabled_at  TEXT,
                PRIMARY KEY (user_id, source_name)
            );

            CREATE TABLE IF NOT EXISTS ad_config (
                id            INTEGER PRIMARY KEY CHECK (id = 1),
                server        TEXT,
                port          INTEGER DEFAULT 389,
                use_tls       INTEGER DEFAULT 0,
                base_dn       TEXT,
                bind_dn       TEXT,
                bind_password TEXT,
                user_filter   TEXT,
                group_dn      TEXT,
                updated_at    TEXT
            );
            """
        )
        # Schema migrations for columns added after initial release
        for stmt in [
            "ALTER TABLE users ADD COLUMN auth_source TEXT NOT NULL DEFAULT 'local'",
            "ALTER TABLE users ADD COLUMN email TEXT",
        ]:
            try:
                self.conn.execute(stmt)
                self.conn.commit()
            except Exception:
                pass

    # ---- (de)serialisation ----------------------------------------------
    @staticmethod
    def _to_row(a: Article) -> dict:
        d = a.to_dict()
        for f in _LIST_FIELDS:
            d[f] = json.dumps(d.get(f) or [])
        return {c: d.get(c) for c in _COLUMNS}

    @staticmethod
    def _from_row(row: sqlite3.Row) -> Article:
        d = dict(row)
        for f in _LIST_FIELDS:
            d[f] = json.loads(d.get(f) or "[]")
        return Article.from_dict(d)

    # ---- writes ----------------------------------------------------------
    def upsert_many(self, articles: list[Article]) -> int:
        """Insert or update rows, preserving the original first_seen."""
        placeholders = ", ".join(f":{c}" for c in _COLUMNS)
        updates = ", ".join(
            f"{c}=excluded.{c}" for c in _COLUMNS if c not in ("id", "first_seen")
        )
        sql = (
            f"INSERT INTO articles ({', '.join(_COLUMNS)}) VALUES ({placeholders}) "
            f"ON CONFLICT(id) DO UPDATE SET {updates}, "
            f"first_seen=COALESCE(articles.first_seen, excluded.first_seen)"
        )
        rows = [self._to_row(a) for a in articles]
        self.conn.executemany(sql, rows)
        self.conn.commit()
        return len(rows)

    def trim_history(self, days: int) -> int:
        """Delete rows first seen longer ago than `days`. 0 = keep everything."""
        if not days:
            return 0
        cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
        cur = self.conn.execute(
            "DELETE FROM articles WHERE first_seen IS NOT NULL AND first_seen < ?",
            (cutoff,),
        )
        self.conn.commit()
        return cur.rowcount

    def replace_all(self, articles: list[Article]) -> None:
        """Used by the dedup pass: rewrite full rows (fields may have changed)."""
        self.upsert_many(articles)

    # ---- reads -----------------------------------------------------------
    def all(self) -> list[Article]:
        cur = self.conn.execute("SELECT * FROM articles")
        return [self._from_row(r) for r in cur.fetchall()]

    def count(self) -> int:
        return self.conn.execute("SELECT COUNT(*) FROM articles").fetchone()[0]

    def query(
        self,
        domain=None, category=None, severity=None, region=None,
        source=None, q=None, sort="date", limit=50, offset=0,
        source_list: list[str] | None = None,
    ) -> tuple[list[Article], int]:
        where, params = [], []
        if domain:
            where.append("domain IN (?, 'both')")
            params.append(domain)
        if severity:
            where.append("severity = ?")
            params.append(severity)
        if source:
            where.append("source LIKE ?")
            params.append(f"%{source}%")
        if category:
            where.append("categories LIKE ?")
            params.append(f"%{category}%")
        if region:
            where.append("regions LIKE ?")
            params.append(f"%{region}%")
        if q:
            where.append("(title LIKE ? OR summary LIKE ?)")
            params.extend([f"%{q}%", f"%{q}%"])
        if source_list is not None:
            if not source_list:
                return [], 0  # user has no sources selected — empty result
            placeholders = ",".join("?" * len(source_list))
            where.append(f"source IN ({placeholders})")
            params.extend(source_list)

        clause = ("WHERE " + " AND ".join(where)) if where else ""
        order = ("weight DESC, published DESC" if sort == "weight"
                 else "published DESC")

        total = self.conn.execute(
            f"SELECT COUNT(*) FROM articles {clause}", params
        ).fetchone()[0]

        cur = self.conn.execute(
            f"SELECT * FROM articles {clause} ORDER BY {order} LIMIT ? OFFSET ?",
            [*params, limit, offset],
        )
        return [self._from_row(r) for r in cur.fetchall()], total

    def stats(self) -> dict:
        by_domain = self._group("domain")
        by_severity = self._group("severity", skip_null=True)
        by_source = self._group("source")
        # categories are multi-valued -> count in Python
        by_category: dict[str, int] = {}
        for (cats,) in self.conn.execute("SELECT categories FROM articles"):
            for c in json.loads(cats or "[]"):
                by_category[c] = by_category.get(c, 0) + 1
        return {
            "total": self.count(),
            "by_domain": by_domain,
            "by_severity": by_severity,
            "by_category": dict(sorted(by_category.items(), key=lambda kv: -kv[1])),
            "by_source": dict(sorted(by_source.items(), key=lambda kv: -kv[1])),
        }

    def new_since(self, iso: str) -> int:
        """Count articles first seen at/after an ISO timestamp."""
        return self.conn.execute(
            "SELECT COUNT(*) FROM articles WHERE first_seen IS NOT NULL AND first_seen >= ?",
            (iso,),
        ).fetchone()[0]

    def _group(self, column: str, skip_null: bool = False) -> dict:
        sql = f"SELECT {column}, COUNT(*) FROM articles"
        if skip_null:
            sql += f" WHERE {column} IS NOT NULL"
        sql += f" GROUP BY {column}"
        return {k: v for k, v in self.conn.execute(sql) if k is not None}

    # ---- source health ---------------------------------------------------
    def record_fetch(self, name: str, ok: bool, count: int, error: str | None) -> None:
        now = datetime.now(timezone.utc).isoformat()
        row = self.conn.execute(
            "SELECT fail_streak, last_success FROM source_health WHERE name=?", (name,)
        ).fetchone()
        prev_streak = row["fail_streak"] if row else 0
        prev_success = row["last_success"] if row else None
        streak = 0 if ok else prev_streak + 1
        last_success = now if ok else prev_success
        self.conn.execute(
            """INSERT INTO source_health
                 (name, last_checked, last_success, last_count, ok, error, fail_streak)
               VALUES (?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(name) DO UPDATE SET
                 last_checked=excluded.last_checked,
                 last_success=excluded.last_success,
                 last_count=excluded.last_count,
                 ok=excluded.ok, error=excluded.error, fail_streak=excluded.fail_streak""",
            (name, now, last_success, count, 1 if ok else 0, error, streak),
        )
        self.conn.commit()

    def health(self) -> list[dict]:
        cur = self.conn.execute(
            "SELECT * FROM source_health ORDER BY ok ASC, name ASC"
        )
        return [dict(r) for r in cur.fetchall()]

    # ---- alert bookkeeping ----------------------------------------------
    def alert_baseline_exists(self, watchlist: str) -> bool:
        row = self.conn.execute(
            "SELECT 1 FROM alert_hits WHERE watchlist=? LIMIT 1", (watchlist,)
        ).fetchone()
        return row is not None

    def known_alert_ids(self, watchlist: str) -> set[str]:
        cur = self.conn.execute(
            "SELECT article_id FROM alert_hits WHERE watchlist=?", (watchlist,)
        )
        return {r[0] for r in cur.fetchall()}

    def record_alert_hits(self, watchlist: str, ids: list[str]) -> None:
        now = datetime.now(timezone.utc).isoformat()
        self.conn.executemany(
            "INSERT OR IGNORE INTO alert_hits (watchlist, article_id, notified_at) "
            "VALUES (?, ?, ?)",
            [(watchlist, i, now) for i in ids],
        )
        self.conn.commit()

    # ---- migration / export ---------------------------------------------
    def import_json(self, json_path: Path) -> int:
        if not Path(json_path).exists():
            return 0
        raw = json.loads(Path(json_path).read_text(encoding="utf-8"))
        arts = [Article.from_dict(d) for d in raw]
        return self.upsert_many(arts)

    def export_json(self, json_path: Path) -> int:
        arts = self.all()
        Path(json_path).write_text(
            json.dumps([a.to_dict() for a in arts], indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        return len(arts)

    def close(self) -> None:
        self.conn.close()

    # ---- user management ------------------------------------------------
    def has_any_users(self) -> bool:
        return self.conn.execute("SELECT 1 FROM users LIMIT 1").fetchone() is not None

    def create_user(self, username: str, password_hash: str, is_admin: bool = False,
                    force_password_change: bool = False, auth_source: str = "local") -> int:
        now = datetime.now(timezone.utc).isoformat()
        cur = self.conn.execute(
            "INSERT INTO users (username, password_hash, is_admin, force_password_change, auth_source, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (username, password_hash, 1 if is_admin else 0,
             1 if force_password_change else 0, auth_source, now),
        )
        self.conn.commit()
        return cur.lastrowid

    def upsert_ldap_user(self, username: str, is_admin: bool, email: str | None = None) -> int:
        """Create or update an LDAP-provisioned user; returns their id."""
        existing = self.get_user_by_username(username)
        if existing:
            self.conn.execute(
                "UPDATE users SET is_admin=?, auth_source='ldap', email=COALESCE(?,email) WHERE id=?",
                (1 if is_admin else 0, email, existing["id"]),
            )
            self.conn.commit()
            return existing["id"]
        user_id = self.create_user(username, "LDAP", is_admin=is_admin, auth_source="ldap")
        if email:
            self.update_user_email(user_id, email)
        return user_id

    def get_user_by_username(self, username: str) -> dict | None:
        row = self.conn.execute("SELECT * FROM users WHERE username=?", (username,)).fetchone()
        return dict(row) if row else None

    def get_user_by_id(self, user_id: int) -> dict | None:
        row = self.conn.execute("SELECT * FROM users WHERE id=?", (user_id,)).fetchone()
        return dict(row) if row else None

    def list_users(self) -> list[dict]:
        cur = self.conn.execute("SELECT id,username,is_admin,force_password_change,created_at,last_login FROM users ORDER BY id")
        return [dict(r) for r in cur.fetchall()]

    def update_user_password(self, user_id: int, new_hash: str, clear_force_change: bool = True) -> None:
        self.conn.execute(
            "UPDATE users SET password_hash=?, force_password_change=? WHERE id=?",
            (new_hash, 0 if clear_force_change else 1, user_id),
        )
        self.conn.commit()

    def update_user_last_login(self, user_id: int) -> None:
        self.conn.execute(
            "UPDATE users SET last_login=? WHERE id=?",
            (datetime.now(timezone.utc).isoformat(), user_id),
        )
        self.conn.commit()

    # ---- AD config -------------------------------------------------------
    def get_ad_config(self) -> dict | None:
        row = self.conn.execute("SELECT * FROM ad_config WHERE id=1").fetchone()
        return dict(row) if row else None

    def update_user_email(self, user_id: int, email: str) -> None:
        self.conn.execute("UPDATE users SET email=? WHERE id=?", (email, user_id))
        self.conn.commit()

    # ---- sources (DB-managed feed registry) -----------------------------
    def has_any_sources(self) -> bool:
        return self.conn.execute("SELECT 1 FROM sources LIMIT 1").fetchone() is not None

    def seed_sources(self, sources: list[dict], created_by: str = "system") -> None:
        now = datetime.now(timezone.utc).isoformat()
        for s in sources:
            self.conn.execute(
                """INSERT OR IGNORE INTO sources
                   (name,label,url,type,enabled,domain,weight,tags,created_at,updated_at,created_by)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
                (s.get("name"), s.get("label", s.get("name")), s.get("url", ""),
                 s.get("type", "rss"), 1 if s.get("enabled", True) else 0,
                 s.get("domain", "both"), s.get("weight", 50),
                 json.dumps(s.get("tags", [])), now, now, created_by),
            )
        self.conn.commit()

    def list_sources(self, enabled_only: bool = False) -> list[dict]:
        sql = "SELECT * FROM sources"
        if enabled_only:
            sql += " WHERE enabled=1"
        sql += " ORDER BY weight DESC, name"
        return [dict(r) for r in self.conn.execute(sql).fetchall()]

    def get_source(self, name: str) -> dict | None:
        row = self.conn.execute("SELECT * FROM sources WHERE name=?", (name,)).fetchone()
        return dict(row) if row else None

    def create_source(self, data: dict, created_by: str) -> None:
        now = datetime.now(timezone.utc).isoformat()
        self.conn.execute(
            """INSERT INTO sources (name,label,url,type,enabled,domain,weight,tags,created_at,updated_at,created_by)
               VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
            (data["name"], data.get("label", data["name"]), data.get("url", ""),
             data.get("type", "rss"), 1 if data.get("enabled", True) else 0,
             data.get("domain", "both"), data.get("weight", 50),
             json.dumps(data.get("tags", [])), now, now, created_by),
        )
        self._log_source_change(data["name"], "add", created_by, data)
        self.conn.commit()

    def update_source(self, name: str, changes: dict, changed_by: str) -> bool:
        existing = self.get_source(name)
        if not existing:
            return False
        now = datetime.now(timezone.utc).isoformat()
        allowed = {"label", "url", "type", "enabled", "domain", "weight", "tags"}
        sets, vals = [], []
        for k, v in changes.items():
            if k in allowed:
                sets.append(f"{k}=?")
                vals.append(json.dumps(v) if k == "tags" else v)
        if not sets:
            return True
        sets.append("updated_at=?")
        vals.append(now)
        vals.append(name)
        self.conn.execute(f"UPDATE sources SET {', '.join(sets)} WHERE name=?", vals)
        self._log_source_change(name, "edit", changed_by, changes)
        self.conn.commit()
        return True

    def delete_source(self, name: str, changed_by: str) -> bool:
        existing = self.get_source(name)
        if not existing:
            return False
        self._log_source_change(name, "delete", changed_by, {})
        self.conn.execute("DELETE FROM sources WHERE name=?", (name,))
        self.conn.execute("DELETE FROM user_sources WHERE source_name=?", (name,))
        self.conn.commit()
        return True

    def _log_source_change(self, name: str, action: str, by: str, detail: dict) -> None:
        self.conn.execute(
            "INSERT INTO source_changes (changed_at,changed_by,source_name,action,detail) VALUES (?,?,?,?,?)",
            (datetime.now(timezone.utc).isoformat(), by, name, action, json.dumps(detail)),
        )

    def source_changelog(self, limit: int = 100) -> list[dict]:
        cur = self.conn.execute(
            "SELECT * FROM source_changes ORDER BY changed_at DESC LIMIT ?", (limit,)
        )
        return [dict(r) for r in cur.fetchall()]

    # ---- user source selection ------------------------------------------
    def get_user_sources(self, user_id: int) -> list[str]:
        cur = self.conn.execute(
            "SELECT source_name FROM user_sources WHERE user_id=?", (user_id,)
        )
        return [r[0] for r in cur.fetchall()]

    def set_user_sources(self, user_id: int, source_names: list[str]) -> None:
        now = datetime.now(timezone.utc).isoformat()
        self.conn.execute("DELETE FROM user_sources WHERE user_id=?", (user_id,))
        self.conn.executemany(
            "INSERT INTO user_sources (user_id, source_name, enabled_at) VALUES (?,?,?)",
            [(user_id, name, now) for name in source_names],
        )
        self.conn.commit()

    # ---- AD config -------------------------------------------------------
    def upsert_ad_config(self, **fields) -> None:
        now = datetime.now(timezone.utc).isoformat()
        fields["updated_at"] = now
        cols = ", ".join(fields.keys())
        placeholders = ", ".join("?" for _ in fields)
        updates = ", ".join(f"{k}=excluded.{k}" for k in fields if k != "id")
        self.conn.execute(
            f"INSERT INTO ad_config (id, {cols}) VALUES (1, {placeholders}) "
            f"ON CONFLICT(id) DO UPDATE SET {updates}",
            list(fields.values()),
        )
        self.conn.commit()
