"""API-Football provider: bounded, cached requests with explicit quota handling."""
import hashlib
import json
import time
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from threading import Lock

import requests

PROVIDER_BUILD = "3.0.1-quota-guard"
BASE = "https://v3.football.api-sports.io"


class FootballAccessError(RuntimeError):
    """Account-wide failure; do not retry once per team."""


class FootballPlanError(FootballAccessError):
    pass


class APIFootball:
    _states = {}
    _lock = Lock()

    def __init__(self, api_key):
        if not api_key:
            raise RuntimeError("API_FOOTBALL_KEY가 없습니다.")
        self.headers = {"x-apisports-key": api_key}
        key = hashlib.sha256(api_key.encode()).hexdigest()
        with self._lock:
            self.state = self._states.setdefault(key, {
                "cache": {}, "next": 0.0, "blocked": 0.0,
                "block_reason": "", "last_supported": True,
                "remaining_day": None, "remaining_minute": None,
            })

    @staticmethod
    def _number(headers, *names):
        for name in names:
            try:
                value = headers.get(name)
                if value is not None:
                    return int(value)
            except (ValueError, TypeError):
                pass
        return None

    @staticmethod
    def _retry_after(headers):
        raw = headers.get("Retry-After")
        try:
            return max(60.0, float(raw))
        except (ValueError, TypeError):
            try:
                delta = (parsedate_to_datetime(raw) - datetime.now(timezone.utc)).total_seconds()
                return max(60.0, delta)
            except (ValueError, TypeError, OverflowError):
                return 60.0

    def _block(self, seconds, reason):
        with self._lock:
            self.state["blocked"] = max(self.state["blocked"], time.monotonic() + seconds)
            self.state["block_reason"] = reason

    def _get(self, path, params):
        cache_key = (path, json.dumps(params, sort_keys=True))
        with self._lock:
            now = time.monotonic()
            cached = self.state["cache"].get(cache_key)
            if cached and now < cached[0]:
                return cached[1]
            if now < self.state["blocked"]:
                remaining = int(self.state["blocked"] - now) + 1
                raise FootballAccessError(
                    f"API-Football 요청 보류 ({remaining}초): {self.state['block_reason']} "
                    "기존 배당 데이터는 유지하고, 미확인 심층 데이터는 잠정 처리하세요."
                )
            if self.state["remaining_day"] == 0:
                raise FootballAccessError("API-Football 일일 할당량 소진: 다음 할당량 갱신 전 추가 요청을 중단했습니다.")
            wait = max(0.0, self.state["next"] - now)
            self.state["next"] = now + wait + 6.2
        if wait:
            time.sleep(wait)
        try:
            response = requests.get(f"{BASE}/{path}", headers=self.headers, params=params, timeout=45)
        except requests.RequestException as exc:
            raise FootballAccessError(f"API-Football 네트워크 오류: {exc}") from exc

        remaining_day = self._number(response.headers, "x-ratelimit-requests-remaining", "X-RateLimit-Requests-Remaining")
        remaining_minute = self._number(response.headers, "X-RateLimit-Remaining", "x-ratelimit-remaining")
        with self._lock:
            if remaining_day is not None:
                self.state["remaining_day"] = remaining_day
            if remaining_minute is not None:
                self.state["remaining_minute"] = remaining_minute

        if response.status_code == 429:
            seconds = self._retry_after(response.headers)
            if remaining_day == 0:
                self._block(86400, "일일 할당량 소진(제공사 대시보드 확인 필요)")
            else:
                self._block(seconds, "429 요청 제한")
            raise FootballAccessError("API-Football 429: 요청 제한에 도달했습니다. 자동 반복 호출을 중단했습니다.")
        if response.status_code in (401, 403):
            self._block(3600, f"인증/접근 오류 {response.status_code}")
            raise FootballAccessError(f"API-Football 인증/접근 오류 ({response.status_code}): 키와 플랜 권한을 확인하세요.")
        response.raise_for_status()
        body = response.json()
        errors = body.get("errors")
        if errors:
            message = str(errors)
            if isinstance(errors, dict) and "plan" in errors:
                raise FootballPlanError(str(errors["plan"]))
            if any(s in message.lower() for s in ("rate", "limit", "request", "token", "access")):
                self._block(86400 if remaining_day == 0 else 60, "제공사 사용량/요청 제한")
                raise FootballAccessError(f"API-Football 수집 제한: {message}")
            raise RuntimeError(message)
        result = body.get("response", [])
        # Finished match statistics do not change. Caching these avoids repeatedly
        # fetching the same fixture for both home/away xG and xGA.
        if path == "fixtures/statistics":
            ttl = 86400
        elif path in ("teams", "leagues"):
            ttl = 86400
        elif path == "fixtures/lineups":
            ttl = 120 if result else 60
        elif path == "injuries":
            ttl = 600
        else:
            ttl = 900
        with self._lock:
            self.state["cache"][cache_key] = (time.monotonic() + ttl, result)
            # Bound memory growth in long-running Streamlit processes.
            if len(self.state["cache"]) > 2500:
                now = time.monotonic()
                self.state["cache"] = {k: v for k, v in self.state["cache"].items() if v[0] > now}
        return result

    def search_leagues(self, name):
        return self._get("leagues", {"search": name})

    def league_fixtures(self, league_id, season):
        return self._get("fixtures", {"league": int(league_id), "season": int(season)})

    def search_team(self, name):
        return self._get("teams", {"search": name})

    def fixtures_by_date(self, date_str, timezone_name=None):
        params = {"date": date_str}
        if timezone_name:
            params["timezone"] = str(timezone_name)
        return self._get("fixtures", params)

    def team_recent_fixtures(self, team_id, last=16, cutoff_iso=None):
        if self.state["last_supported"]:
            try:
                return self._get("fixtures", {"team": int(team_id), "last": int(last)})
            except FootballPlanError as exc:
                if "last" not in str(exc).lower():
                    raise
                self.state["last_supported"] = False
        cutoff = datetime.fromisoformat(str(cutoff_iso).replace("Z", "+00:00")) if cutoff_iso else datetime.now(timezone.utc)
        found = {}
        for season in range(cutoff.year, cutoff.year - 3, -1):
            try:
                fixtures = self._get("fixtures", {"team": int(team_id), "season": season})
            except FootballPlanError as exc:
                raise FootballPlanError(f"API-Football 플랜이 {season} 시즌 조회를 허용하지 않습니다: {exc}") from exc
            for fx in fixtures:
                fixture = fx.get("fixture", {})
                ts = fixture.get("timestamp") or 0
                if fixture.get("status", {}).get("short") in {"FT", "AET", "PEN"} and 0 < ts < cutoff.timestamp():
                    found[fixture["id"]] = fx
            if len(found) >= last:
                break
        return sorted(found.values(), key=lambda x: x["fixture"]["timestamp"], reverse=True)[:last]

    def odds_for_league_date(self, league_id, season, date_str, max_pages=3):
        out = []
        for page in range(1, max(1, int(max_pages)) + 1):
            rows = self._get("odds", {"league": int(league_id), "season": int(season), "date": str(date_str), "page": page})
            if not rows:
                break
            out.extend(rows)
            if len(rows) < 10:
                break
        return out

    def odds_fixture(self, fixture_id):
        return self._get("odds", {"fixture": int(fixture_id)})

    def lineups(self, fixture_id):
        return self._get("fixtures/lineups", {"fixture": int(fixture_id)})

    def injuries(self, fixture_id):
        return self._get("injuries", {"fixture": int(fixture_id)})

    def fixture_statistics(self, fixture_id):
        return self._get("fixtures/statistics", {"fixture": int(fixture_id)})

    def fixture_players(self, fixture_id):
        return self._get("fixtures/players", {"fixture": int(fixture_id)})

    def team_players(self, team_id, season, max_pages=2):
        out = []
        for page in range(1, max(1, int(max_pages)) + 1):
            rows = self._get("players", {"team": int(team_id), "season": int(season), "page": page})
            if not rows:
                break
            out.extend(rows)
            if len(rows) < 20:
                break
        merged = {}
        for row in out:
            pid = (row.get("player") or {}).get("id")
            if pid is None:
                continue
            if pid not in merged:
                merged[pid] = row
            else:
                merged[pid].setdefault("statistics", []).extend(row.get("statistics") or [])
        return list(merged.values())
