"""Sports odds via The Odds API (https://the-odds-api.com). Needs ODDS_API_KEY (Secret Manager →
env). Returns per-game moneyline / spread / total prices from US books; theScore Bet uses ESPN BET
lines, so `espnbet` is the closest proxy for Stan's book."""
from __future__ import annotations

import os
from datetime import datetime, timezone

import httpx
from langchain_core.tools import tool

BASE = "https://api.the-odds-api.com/v4"
SPORT_ALIASES = {
    "ncaaf": "americanfootball_ncaaf", "college football": "americanfootball_ncaaf", "cfb": "americanfootball_ncaaf",
    "nfl": "americanfootball_nfl", "nba": "basketball_nba", "ncaab": "basketball_ncaab", "mlb": "baseball_mlb",
    "nhl": "icehockey_nhl", "wnba": "basketball_wnba", "mls": "soccer_usa_mls", "epl": "soccer_epl", "ufc": "mma_mixed_martial_arts",
}
PREFERRED_BOOKS = ["espnbet", "draftkings", "fanduel", "betmgm", "caesars", "betrivers", "bovada", "pinnacle"]


def _key() -> str | None:
    return os.environ.get("ODDS_API_KEY") or None


def _am(price) -> str:
    try:
        p = int(price)
    except (TypeError, ValueError):
        return str(price)
    return f"+{p}" if p > 0 else str(p)


def _fmt_game(g: dict, books: list[str]) -> str:
    when = datetime.fromisoformat(g["commence_time"].replace("Z", "+00:00")).astimezone(timezone.utc)
    lines = [f"{g['away_team']} @ {g['home_team']}  ({when:%a %m/%d %H:%M} UTC)"]
    by_key = {b["key"]: b for b in g.get("bookmakers", [])}
    shown = [b for b in books if b in by_key] or list(by_key)[:3]
    for bk in shown:
        b = by_key[bk]
        parts = []
        for m in b.get("markets", []):
            oc = m.get("outcomes", [])
            if m["key"] == "h2h":
                parts.append("ML " + " / ".join(f"{o['name']} {_am(o['price'])}" for o in oc))
            elif m["key"] == "spreads":
                parts.append("Spread " + " / ".join(f"{o['name']} {o.get('point', 0):+g} ({_am(o['price'])})" for o in oc))
            elif m["key"] == "totals":
                parts.append("Total " + " / ".join(f"{o['name'][0]} {o.get('point')} ({_am(o['price'])})" for o in oc))
        lines.append(f"  [{b['title']}] " + " | ".join(parts))
    if len(by_key) > len(shown):
        lines.append(f"  (+{len(by_key) - len(shown)} more books)")
    return "\n".join(lines)


@tool
def sports_odds(sport: str = "ncaaf", markets: str = "h2h,spreads,totals", books: str = "", team: str = "") -> str:
    """Live betting odds for upcoming games from The Odds API. sport: ncaaf, nfl, nba, ncaab, mlb, nhl,
    wnba, mls, epl, ufc or a raw key like americanfootball_ncaaf. markets: h2h (moneyline), spreads,
    totals. books: comma list of bookmaker keys to show (default espnbet=theScore Bet lines, draftkings,
    fanduel); team: filter to games involving this team name. Compare books to find the best price."""
    key = _key()
    if not key:
        return ("error: ODDS_API_KEY is not configured. Get a free key at https://the-odds-api.com (500 requests/month), "
                "store it as Secret Manager secret ODDS_API_KEY in nikkiaia-prod and map it into the nikki Cloud Run service.")
    sport_key = SPORT_ALIASES.get(sport.lower().strip(), sport.strip())
    want_books = [b.strip() for b in books.split(",") if b.strip()] or PREFERRED_BOOKS[:3]
    params = {"apiKey": key, "regions": "us,us2", "markets": markets, "oddsFormat": "american"}
    try:
        r = httpx.get(f"{BASE}/sports/{sport_key}/odds", params=params, timeout=30)
    except httpx.HTTPError as e:
        return f"error: {type(e).__name__}: {e}"
    if r.status_code != 200:
        return f"error: odds API {r.status_code}: {r.text[:300]}"
    games = r.json()
    if team:
        t = team.lower()
        games = [g for g in games if t in g["home_team"].lower() or t in g["away_team"].lower()]
    if not games:
        return f"no upcoming {sport_key} games found" + (f" for '{team}'" if team else "")
    games.sort(key=lambda g: g["commence_time"])
    out = [_fmt_game(g, want_books) for g in games[:40]]
    remaining = r.headers.get("x-requests-remaining")
    tail = f"\n\n({len(games)} games; API requests left this month: {remaining})" if remaining else ""
    return "\n\n".join(out) + tail


@tool
def sports_scores(sport: str = "ncaaf", days_from: int = 1) -> str:
    """Recent and live scores for a sport (same sport keys as sports_odds). days_from: 1-3 days of
    completed games to include."""
    key = _key()
    if not key:
        return "error: ODDS_API_KEY is not configured (see sports_odds)"
    sport_key = SPORT_ALIASES.get(sport.lower().strip(), sport.strip())
    try:
        r = httpx.get(f"{BASE}/sports/{sport_key}/scores", params={"apiKey": key, "daysFrom": max(1, min(days_from, 3))}, timeout=30)
    except httpx.HTTPError as e:
        return f"error: {type(e).__name__}: {e}"
    if r.status_code != 200:
        return f"error: odds API {r.status_code}: {r.text[:300]}"
    rows = []
    for g in r.json():
        sc = {s["name"]: s["score"] for s in (g.get("scores") or [])}
        status = "FINAL" if g.get("completed") else ("LIVE" if sc else "upcoming")
        rows.append(f"{g['away_team']} {sc.get(g['away_team'], '-')} @ {g['home_team']} {sc.get(g['home_team'], '-')}  [{status}]")
    return "\n".join(rows) or "no games"


for _t in (sports_odds, sports_scores):
    _t.metadata = {"requires_approval": False}

TOOLS = [sports_odds, sports_scores]
