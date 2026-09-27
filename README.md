# Sports EV Engine v2.1.2

Patch release for API-Football Free-plan compatibility.

## Fixes
- Adds required `season` parameter to team fixture queries.
- Never uses the paid/free-restricted `last` parameter.
- Filters to today's KST games by default before calling API-Football.
- Offers today / next 3 days / next 7 days / all date scopes.
- One failed team/game no longer stops the entire batch.
- Shows a failure table with the exact provider reason.
- Keeps API-Football usage lower for the 100 requests/day Free plan.

API keys remain in Streamlit Secrets only.
