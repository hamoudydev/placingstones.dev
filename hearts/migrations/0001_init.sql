-- One row per (post, reader). A reader is an anonymous id the browser keeps
-- in localStorage; toggling a heart inserts or deletes the row.
CREATE TABLE IF NOT EXISTS hearts (
  slug TEXT NOT NULL,
  client TEXT NOT NULL,
  ip_hash TEXT NOT NULL,
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (slug, client)
);

CREATE INDEX IF NOT EXISTS idx_hearts_slug ON hearts(slug);
CREATE INDEX IF NOT EXISTS idx_hearts_slug_ip ON hearts(slug, ip_hash);

-- Counts carried over from the previous reaction service, added to the live
-- count so hearts given before the move are not lost.
CREATE TABLE IF NOT EXISTS offsets (
  slug TEXT PRIMARY KEY,
  count INTEGER NOT NULL DEFAULT 0
);
