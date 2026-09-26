import hashlib
import secrets
import sqlite3
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS agents (
    id TEXT PRIMARY KEY, key_hash TEXT NOT NULL UNIQUE,
    skills TEXT NOT NULL DEFAULT '[]', capacity INTEGER NOT NULL DEFAULT 1,
    accepting INTEGER NOT NULL DEFAULT 1, last_seen INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS posts (
    id INTEGER PRIMARY KEY AUTOINCREMENT, author TEXT NOT NULL REFERENCES agents(id),
    title TEXT NOT NULL, body TEXT NOT NULL, kind TEXT NOT NULL,
    created_at INTEGER NOT NULL, updated_at INTEGER NOT NULL,
    skill TEXT, target TEXT REFERENCES agents(id), state TEXT,
    claimed_by TEXT REFERENCES agents(id), lease_until INTEGER, lease_token TEXT,
    result_reply_id INTEGER REFERENCES replies(id)
);
CREATE TABLE IF NOT EXISTS replies (
    id INTEGER PRIMARY KEY AUTOINCREMENT, post_id INTEGER NOT NULL REFERENCES posts(id),
    author TEXT NOT NULL REFERENCES agents(id), body TEXT NOT NULL,
    reply_to INTEGER REFERENCES replies(id), created_at INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS replies_post ON replies(post_id, id);
CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY AUTOINCREMENT, recipient TEXT NOT NULL REFERENCES agents(id),
    post_id INTEGER NOT NULL REFERENCES posts(id), reply_id INTEGER REFERENCES replies(id),
    kind TEXT NOT NULL, actor TEXT NOT NULL REFERENCES agents(id),
    created_at INTEGER NOT NULL, is_read INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS events_inbox ON events(recipient, is_read, id);
CREATE INDEX IF NOT EXISTS events_post ON events(recipient, post_id, id);
CREATE INDEX IF NOT EXISTS tasks_queue ON posts(kind, state, id);
CREATE TABLE IF NOT EXISTS idempotency (
    agent TEXT NOT NULL REFERENCES agents(id), key TEXT NOT NULL,
    fingerprint TEXT NOT NULL, response TEXT NOT NULL, status INTEGER NOT NULL,
    created_at INTEGER NOT NULL, PRIMARY KEY(agent, key)
);
CREATE INDEX IF NOT EXISTS idempotency_created ON idempotency(created_at);
"""


def connect(path):
    db = sqlite3.connect(path, timeout=15, isolation_level=None)
    db.row_factory = sqlite3.Row
    db.execute('PRAGMA foreign_keys=ON')
    db.execute('PRAGMA busy_timeout=15000')
    return db


def initialize(path):
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    db = connect(path)
    try:
        db.execute('PRAGMA journal_mode=WAL')
        db.executescript(SCHEMA)
    finally:
        db.close()


def key_hash(key):
    return hashlib.sha256(key.encode()).hexdigest()


def provision(path, agent_id, rotate=False):
    import re
    if not re.fullmatch(r'[a-z][a-z0-9-]{1,39}', agent_id):
        raise ValueError('Agent ID must be 2-40 lowercase letters, digits or hyphens')
    key = 'aif_' + secrets.token_urlsafe(32)
    db = connect(path)
    try:
        db.execute('BEGIN IMMEDIATE')
        if rotate:
            if not db.execute('UPDATE agents SET key_hash=? WHERE id=?',
                              (key_hash(key), agent_id)).rowcount:
                raise ValueError('Unknown agent')
        else:
            db.execute('INSERT INTO agents(id,key_hash) VALUES (?,?)', (agent_id, key_hash(key)))
        db.commit()
    finally:
        db.close()
    return key
