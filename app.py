"""
Multi-sport betting odds viewer — Ontario/Canada
Run: python app.py  →  http://localhost:5001
"""
import threading
import webbrowser
from datetime import datetime, timezone

from flask import Flask, render_template, jsonify, request
import odds_fetcher as of

app = Flask(__name__)

CACHE_TTL = 300  # seconds
_CACHE = {}  # keyed by sport_id


def _get_odds(sport_id="worldcup", force=False):
    now = datetime.now(timezone.utc).timestamp()
    cached = _CACHE.get(sport_id)
    if not force and cached and (now - cached["ts"]) < CACHE_TTL:
        return cached["data"]
    data = of.fetch_odds(sport_id)
    _CACHE[sport_id] = {"data": data, "ts": now}
    return data


@app.route("/")
def index():
    sport_id = request.args.get("sport", "worldcup")
    if sport_id not in of.SPORTS:
        sport_id = "worldcup"
    data = _get_odds(sport_id)
    upcoming = [m for m in data["matches"] if not m["is_tbd"]]
    tbd      = [m for m in data["matches"] if m["is_tbd"]]
    return render_template(
        "index.html",
        sport_id=sport_id,
        sports=of.SPORTS,
        upcoming=upcoming,
        tbd=tbd,
        error=data["error"],
        fetched_at=data["fetched_at"],
        quota_used=data["quota_used"],
        quota_remaining=data["quota_remaining"],
        is_offseason=data.get("is_offseason", False),
        active_key=data.get("active_key"),
        cache_ttl=CACHE_TTL,
    )


@app.route("/refresh")
def refresh():
    sport_id = request.args.get("sport", "worldcup")
    data = _get_odds(sport_id, force=True)
    return jsonify({
        "ok":               data["error"] is None,
        "error":            data["error"],
        "n_matches":        len(data["matches"]),
        "fetched_at":       data["fetched_at"],
        "quota_remaining":  data["quota_remaining"],
    })


@app.route("/poll")
def poll():
    sport_id = request.args.get("sport", "worldcup")
    ids = set(request.args.get("ids", "").split(","))
    ids.discard("")
    data = _get_odds(sport_id)
    results = {}
    for m in data["matches"]:
        if m["id"] in ids:
            arb = m["arb"] or {}
            results[m["id"]] = {
                "home": m["home"], "away": m["away"],
                "is_arb": arb.get("is_arb", False),
                "is_near_arb": arb.get("is_near_arb", False),
                "arb_pct": arb.get("arb_pct"),
                "total_implied_pct": arb.get("total_implied_pct"),
                "stakes": arb.get("stakes"),
                "fetched_at": data["fetched_at"],
            }
    return jsonify({"matches": results, "fetched_at": data["fetched_at"]})


if __name__ == "__main__":
    threading.Timer(1.0, lambda: webbrowser.open("http://localhost:5001")).start()
    app.run(debug=False, port=5001)
