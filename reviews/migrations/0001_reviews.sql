-- Swimmers' reviews of spots. A review waits ("pending") until the operator publishes it on the
-- moderation page; one that is turned down is deleted, not kept. The photos are in Workers KV
-- (binding PHOTOS), under keys made from the review's id; `photos` holds each one's size.
CREATE TABLE reviews (
  id TEXT PRIMARY KEY,
  spot TEXT NOT NULL,
  again INTEGER NOT NULL CHECK (again IN (0, 1)),     -- would swim here again
  swam_on TEXT NOT NULL,                              -- YYYY-MM-DD, the day they swam
  body TEXT NOT NULL DEFAULT '',
  name TEXT NOT NULL DEFAULT '',                      -- the name to show; empty is "A swimmer"
  photos TEXT NOT NULL DEFAULT '[]',                  -- JSON: [{"w","h","tw","th"}], one a photo
  status TEXT NOT NULL DEFAULT 'pending' CHECK (status IN ('pending', 'published')),
  created_at TEXT NOT NULL,
  published_at TEXT,
  token_hash TEXT NOT NULL                            -- SHA-256 of the key the sender's browser keeps, to delete it
);
CREATE INDEX reviews_by_status ON reviews (status, created_at);

-- Reports of a published review: the reason chosen, until the operator keeps or deletes it.
CREATE TABLE reports (
  review TEXT NOT NULL,
  reason TEXT NOT NULL,
  created_at TEXT NOT NULL
);
CREATE INDEX reports_by_review ON reports (review);

-- Requests a day from one connection, for the rate limits. The key is a keyed hash of the IP
-- address and the day, never the address; the daily cron deletes days before yesterday.
CREATE TABLE hits (
  key TEXT NOT NULL,
  day TEXT NOT NULL,
  n INTEGER NOT NULL DEFAULT 0,
  PRIMARY KEY (key, day)
);
