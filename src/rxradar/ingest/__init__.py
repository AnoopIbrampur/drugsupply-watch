"""Source ingestion. Each module saves untouched raw snapshots under
data/raw/<source>/<fetch-date>/ so every later step can be replayed from disk
and we can always tell what we knew on which day."""
