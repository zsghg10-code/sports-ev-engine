Runtime data directory.

v3.2 local fallback files may be created here:
- prediction_snapshots.jsonl
- market_observations.jsonl
- settled_predictions.jsonl
- postgame_reviews.jsonl
- refresh_events.jsonl
- smart_refresh_state.json

When Supabase is configured, prediction/market/settlement/review/refresh records are also mirrored to PostgreSQL. Local JSONL remains a fail-soft fallback.
