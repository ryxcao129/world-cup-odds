"""
Multi-sport betting odds fetcher — The Odds API
Ontario/Canada licensed bookmakers only.
"""
import os
import requests
from datetime import datetime, timezone

API_BASE = "https://api.the-odds-api.com/v4"
REGIONS  = "ca,us,us2"

# Ontario iGO-licensed books (key → display name, in preferred order)
ONTARIO_BOOKS = {
    "draftkings":   "DraftKings",
    "fanduel":      "FanDuel",
    "betmgm":       "BetMGM",
    "betrivers":    "BetRivers",
    "thescore_bet": "theScore Bet",
    "pointsbetca":  "PointsBet (CA)",
    "unibet_ca":    "Unibet",
    "bet365":       "Bet365",
    "888sport":     "888sport",
    "betway":       "Betway",
}

# Sport definitions — add new sports here
SPORTS = {
    "worldcup": {
        "sport_key":    "soccer_fifa_world_cup",
        "label":        "FIFA World Cup 2026",
        "short":        "World Cup",
        "has_draw":     True,
        "offseason":    False,
        "offseason_msg": None,
        "sub_keys":     [],   # fallback keys to try if main is inactive
    },
    "nba": {
        "sport_key":    "basketball_nba",
        "label":        "NBA",
        "short":        "NBA",
        "has_draw":     False,
        "offseason":    True,  # updated automatically when season activates
        "offseason_msg": (
            "The NBA regular season is currently in offseason. "
            "The 2026-27 season typically tips off in October. "
            "Summer League (mid-July) and Preseason (October) odds will appear here automatically when posted."
        ),
        "sub_keys": [
            "basketball_nba_summer_league",
            "basketball_nba_preseason",
        ],
    },
}

DISCREPANCY_THRESHOLD = 3.0   # % spread in decimal odds across books
NEAR_ARB_THRESHOLD    = -1.5  # within 1.5% of true arb


def _get_key():
    try:
        from config import ODDS_API_KEY
        return ODDS_API_KEY
    except ImportError:
        pass
    key = os.environ.get("ODDS_API_KEY", "")
    if not key:
        raise RuntimeError("No API key. Add ODDS_API_KEY = 'your-key' to config.py.")
    return key


def fetch_odds(sport_id: str = "worldcup") -> dict:
    sport = SPORTS.get(sport_id)
    if not sport:
        return _err(f"Unknown sport: {sport_id}")

    try:
        key = _get_key()
    except RuntimeError as e:
        return _err(str(e))

    # Try the main key first, then sub_keys (e.g. summer league, preseason)
    keys_to_try = [sport["sport_key"]] + sport.get("sub_keys", [])
    all_matches = []
    active_key_used = None
    quota_used = quota_remaining = None
    last_error = None

    for sport_key in keys_to_try:
        resp_data, qu, qr, err = _fetch_raw(key, sport_key, sport["has_draw"])
        quota_used = qu
        quota_remaining = qr
        if err:
            last_error = err
            continue
        if resp_data:
            active_key_used = sport_key
            all_matches.extend(resp_data)

    all_matches.sort(key=lambda m: m["commence_time_raw"] or "9999")

    # Determine if truly in offseason (no matches found and sport flagged as offseason)
    is_offseason = sport.get("offseason", False) and not all_matches

    return {
        "sport_id":       sport_id,
        "sport":          sport,
        "matches":        all_matches,
        "active_key":     active_key_used,
        "is_offseason":   is_offseason,
        "error":          last_error if not all_matches else None,
        "quota_used":     quota_used,
        "quota_remaining": quota_remaining,
        "fetched_at":     datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
    }


def _fetch_raw(api_key, sport_key, has_draw):
    url = f"{API_BASE}/sports/{sport_key}/odds"
    params = {
        "apiKey":      api_key,
        "regions":     REGIONS,
        "markets":     "h2h",
        "oddsFormat":  "american",
        "dateFormat":  "iso",
    }
    try:
        resp = requests.get(url, params=params, timeout=10)
    except requests.RequestException as e:
        return None, None, None, f"Network error: {e}"

    qu = resp.headers.get("x-requests-used")
    qr = resp.headers.get("x-requests-remaining")

    if resp.status_code == 401:
        return None, qu, qr, "Invalid API key."
    if resp.status_code in (404, 422):
        return [], qu, qr, None   # sport inactive — not an error, just empty
    if not resp.ok:
        return None, qu, qr, f"API error {resp.status_code}: {resp.text[:200]}"

    matches = _parse_matches(resp.json(), has_draw)
    return matches, qu, qr, None


def _parse_matches(raw: list, has_draw: bool) -> list:
    matches = []
    for event in raw:
        home = event.get("home_team") or "TBD"
        away = event.get("away_team") or "TBD"
        commence_raw = event.get("commence_time")

        if commence_raw:
            try:
                dt = datetime.fromisoformat(commence_raw.replace("Z", "+00:00"))
                commence_display = dt.strftime("%b %d, %Y  %H:%M UTC")
                is_tbd = False
            except Exception:
                commence_display = commence_raw
                is_tbd = False
        else:
            commence_display = "TBD"
            is_tbd = True

        is_tbd = is_tbd or home == "TBD" or away == "TBD"

        books = {}
        for bm in event.get("bookmakers", []):
            bkey = bm.get("key", "")
            if bkey not in ONTARIO_BOOKS:
                continue
            title = ONTARIO_BOOKS[bkey]
            for market in bm.get("markets", []):
                if market.get("key") != "h2h":
                    continue
                outcomes = {o["name"]: o["price"] for o in market.get("outcomes", [])}
                home_odds = outcomes.get(home)
                away_odds = outcomes.get(away)
                draw_odds = outcomes.get("Draw") if has_draw else None
                if home_odds is not None or away_odds is not None:
                    books[bkey] = {"title": title, "home": home_odds, "draw": draw_odds, "away": away_odds}

        best_home  = _best(books, "home")
        best_draw  = _best(books, "draw") if has_draw else None
        best_away  = _best(books, "away")
        worst_home = _worst(books, "home")
        worst_draw = _worst(books, "draw") if has_draw else None
        worst_away = _worst(books, "away")

        arb = _arb_analysis(best_home, best_draw if has_draw else None, best_away)
        discrepancies = _discrepancy_flags(best_home, worst_home, best_draw, worst_draw, best_away, worst_away)

        matches.append({
            "id":               event.get("id"),
            "home":             home,
            "away":             away,
            "commence_time_raw": commence_raw,
            "commence_display": commence_display,
            "is_tbd":           is_tbd,
            "has_draw":         has_draw,
            "books":            _order_books(books),
            "best_home":        best_home,
            "best_draw":        best_draw,
            "best_away":        best_away,
            "worst_home":       worst_home,
            "worst_draw":       worst_draw,
            "worst_away":       worst_away,
            "best_home_book":   _book_for(books, "home", best_home),
            "best_draw_book":   _book_for(books, "draw", best_draw),
            "best_away_book":   _book_for(books, "away", best_away),
            "n_books":          len(books),
            "arb":              arb,
            "discrepancies":    discrepancies,
        })
    return matches


def _best(books, outcome):
    vals = [b[outcome] for b in books.values() if b.get(outcome) is not None]
    if not vals:
        return None
    return max(vals, key=lambda x: x if x >= 0 else 10000 + x)


def _worst(books, outcome):
    vals = [b[outcome] for b in books.values() if b.get(outcome) is not None]
    if not vals:
        return None
    return min(vals, key=lambda x: x if x >= 0 else 10000 + x)


def _book_for(books, outcome, target):
    if target is None:
        return ""
    for b in books.values():
        if b.get(outcome) == target:
            return b["title"]
    return ""


def _order_books(books):
    preferred = [(k, books[k]) for k in ONTARIO_BOOKS if k in books]
    others    = sorted([(k, v) for k, v in books.items() if k not in ONTARIO_BOOKS], key=lambda x: x[1]["title"])
    return preferred + others


def american_to_decimal(odds):
    return (odds / 100) + 1 if odds >= 0 else (100 / abs(odds)) + 1


def implied_prob(odds):
    return 1 / american_to_decimal(odds)


def _arb_analysis(best_home, best_draw, best_away):
    outcomes = [o for o in [best_home, best_draw, best_away] if o is not None]
    if len(outcomes) < 2:
        return None
    total_impl = sum(implied_prob(o) for o in outcomes)
    arb_pct    = round((1 - total_impl) * 100, 2)
    is_arb     = arb_pct > 0
    is_near_arb = not is_arb and arb_pct > NEAR_ARB_THRESHOLD
    return {
        "total_implied_pct": round(total_impl * 100, 2),
        "arb_pct":           arb_pct,
        "is_arb":            is_arb,
        "is_near_arb":       is_near_arb,
        "stakes":            _arb_stakes(best_home, best_draw, best_away) if is_arb else None,
    }


def _arb_stakes(home, draw, away, total=1000):
    outcomes = [(o, n) for o, n in [(home, "home"), (draw, "draw"), (away, "away")] if o is not None]
    decimals = {n: american_to_decimal(o) for o, n in outcomes}
    inv_sum  = sum(1 / d for d in decimals.values())
    stakes   = {n: round((1 / decimals[n]) / inv_sum * total, 2) for n in decimals}
    return {"stakes": stakes, "profit": round(total / inv_sum - total, 2), "total": total}


def _discrepancy_flags(bh, wh, bd, wd, ba, wa):
    flags = {}
    def spread(best, worst):
        if best is None or worst is None:
            return 0
        return round((american_to_decimal(best) - american_to_decimal(worst)) / american_to_decimal(worst) * 100, 1)
    if spread(bh, wh) >= DISCREPANCY_THRESHOLD: flags["home"] = spread(bh, wh)
    if spread(bd, wd) >= DISCREPANCY_THRESHOLD: flags["draw"] = spread(bd, wd)
    if spread(ba, wa) >= DISCREPANCY_THRESHOLD: flags["away"] = spread(ba, wa)
    return flags


def _err(msg, qu=None, qr=None):
    return {"matches": [], "error": msg, "quota_used": qu, "quota_remaining": qr,
            "fetched_at": None, "is_offseason": False, "sport": {}, "sport_id": "", "active_key": None}
