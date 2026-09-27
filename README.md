# Sports EV Engine v2.1.3

This patch replaces per-team recent-fixture calls with a competition-centric data pipeline.

## Why
API-Football Free plan returned no completed fixtures for the team+season lookup used in v2.1.2.

## New flow
- Resolve selected competition through `/leagues?search=...`
- Fetch current and previous available season using `/fixtures?league=ID&season=YEAR`
- Build one cached competition fixture pool
- Derive each team's recent completed matches locally from that pool
- Analyze every current event from the same pool

For UEFA Nations League the API-Football competition ID is 5 (resolved automatically).

This reduces API-Football calls substantially and avoids the Free-plan `last`/team-season problems.
