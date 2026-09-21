"""Prop Edge - rank PrizePicks props by no-vig sportsbook probability (Streamlit app)."""
import json, math, re, time, unicodedata
from collections import Counter, defaultdict
from datetime import datetime, timezone, timedelta
from zoneinfo import ZoneInfo

import pandas as pd
import requests
import streamlit as st

ODDS_API = "https://api.the-odds-api.com/v4"
LOCAL_TZ = ZoneInfo("America/Phoenix")

# ---------------------------------------------------------------- config
LEAGUES = {
    "NFL":  {"pp_id": 9,  "odds_key": "americanfootball_nfl",   "stats": "football"},
    "CFB":  {"pp_id": 15, "odds_key": "americanfootball_ncaaf", "stats": "football"},
    "MLB":  {"pp_id": 2,  "odds_key": "baseball_mlb",           "stats": "baseball"},
    "WNBA": {"pp_id": 3,  "odds_key": "basketball_wnba",        "stats": "basketball"},
    "NBA":  {"pp_id": 7,  "odds_key": "basketball_nba",         "stats": "basketball"},
    "NHL":  {"pp_id": 8,  "odds_key": "icehockey_nhl",          "stats": "hockey"},
}
PP_ID_TO_LEAGUE = {str(v["pp_id"]): k for k, v in LEAGUES.items()}
# Pre-ticked on load; every other league stays one click away.
DEFAULT_LEAGUES = ["NFL", "CFB"]

BOOKS = {"fanduel": "FanDuel", "draftkings": "DraftKings", "betmgm": "BetMGM",
         "williamhill_us": "Caesars", "fanatics": "Fanatics", "betrivers": "BetRivers",
         "bovada": "Bovada"}
DEFAULT_BOOKS = ["fanduel", "draftkings", "betmgm", "williamhill_us", "fanatics"]

# PrizePicks stat_type (lowercase, no spaces) -> The Odds API market key
STAT_MAP = {
    "football": {
        "passyards": "player_pass_yds", "passtds": "player_pass_tds",
        "passattempts": "player_pass_attempts", "passcompletions": "player_pass_completions",
        "int": "player_pass_interceptions", "passints": "player_pass_interceptions",
        "interceptions": "player_pass_interceptions", "longestcompletion": "player_pass_longest_completion",
        "rushyards": "player_rush_yds", "rushattempts": "player_rush_attempts",
        "rushtds": "player_rush_tds", "longestrush": "player_rush_longest",
        "receivingyards": "player_reception_yds", "receptions": "player_receptions",
        "longestreception": "player_reception_longest", "rectds": "player_reception_tds",
        "rush+recyds": "player_rush_reception_yds", "pass+rushyds": "player_pass_rush_yds",
        "kickingpoints": "player_kicking_points", "fgmade": "player_field_goals",
        "tackles+ast": "player_tackles_assists", "sacks": "player_sacks",
    },
    "baseball": {
        "hits": "batter_hits", "totalbases": "batter_total_bases", "rbis": "batter_rbis",
        "runs": "batter_runs_scored", "hits+runs+rbis": "batter_hits_runs_rbis",
        "singles": "batter_singles", "doubles": "batter_doubles", "walks": "batter_walks",
        "hitterstrikeouts": "batter_strikeouts", "homeruns": "batter_home_runs",
        "stolenbases": "batter_stolen_bases",
        "pitcherstrikeouts": "pitcher_strikeouts", "hitsallowed": "pitcher_hits_allowed",
        "walksallowed": "pitcher_walks", "earnedrunsallowed": "pitcher_earned_runs",
        "pitchingouts": "pitcher_outs",
    },
    "basketball": {
        "points": "player_points", "rebounds": "player_rebounds", "assists": "player_assists",
        "3-ptmade": "player_threes", "blockedshots": "player_blocks", "steals": "player_steals",
        "turnovers": "player_turnovers", "pts+rebs+asts": "player_points_rebounds_assists",
        "pts+rebs": "player_points_rebounds", "pts+asts": "player_points_assists",
        "rebs+asts": "player_rebounds_assists", "blks+stls": "player_blocks_steals",
    },
    "hockey": {
        "points": "player_points", "assists": "player_assists", "goals": "player_goals",
        "shotsongoal": "player_shots_on_goal", "goaliesaves": "player_total_saves",
        "blockedshots": "player_blocked_shots", "powerplaypoints": "player_power_play_points",
    },
}

# Verify in the PrizePicks app - payouts vary by state and change.
DEFAULT_PAYOUTS = pd.DataFrame([
    ["2P", 3.0, 0.0, 0.0], ["3P", 6.0, 0.0, 0.0], ["4P", 10.0, 0.0, 0.0],
    ["5P", 20.0, 0.0, 0.0], ["6P", 37.5, 0.0, 0.0], ["3F", 3.0, 1.0, 0.0],
    ["4F", 6.0, 1.5, 0.0], ["5F", 10.0, 2.0, 0.4], ["6F", 25.0, 2.0, 0.4],
], columns=["Entry", "All hit", "Miss 1", "Miss 2"])

BOOKMARKLET_JS = '(async()=>{const GID=\'__GID__\',TOK=\'__TOK__\';const L={NFL:9,CFB:15,MLB:2,WNBA:3,NBA:7,NHL:8};const props=[],rep=[];const w=z=>new Promise(r=>setTimeout(r,z));for(const[k,id]of Object.entries(L)){let c=\'\';for(let t=0;t<3;t++){try{const r=await fetch(\'/projections?league_id=\'+id+\'&per_page=1000&single_stat=true&game_mode=pickem\',{credentials:\'include\'});if(!r.ok){c=\'blocked (\'+r.status+\')\';await w(2500);continue}const j=await r.json();const P={};for(const i of j.included||[]){if(i.type===\'new_player\'||i.type===\'player\')P[i.id]=i.attributes||{}}let m=0;for(const p of j.data||[]){const a=p.attributes||{};const rel=p.relationships||{};const d=(rel.new_player||{}).data||{};const pl=P[d.id]||{};const n=pl.display_name||pl.name||\'\';if(!n||pl.combo||n.indexOf(\' + \')>=0)continue;if(a.status&&a.status!==\'pre_game\')continue;const ln=parseFloat(a.line_score);if(isNaN(ln))continue;props.push({lg:k,p:n,t:pl.team||\'\',o:a.description||\'\',s:a.stat_type||\'\',l:ln,ot:a.odds_type||\'standard\',st:a.start_time||\'\'});m++}c=m+\' props\';break}catch(e){c=\'error\';await w(2500)}}rep.push(k+\': \'+c);await w(1200)}const body=JSON.stringify({_fetched_at:new Date().toISOString(),_slim:1,props:props});let msg;try{const g=await fetch(\'https://api.github.com/gists/\'+GID,{method:\'PATCH\',headers:{\'Authorization\':\'Bearer \'+TOK,\'Accept\':\'application/vnd.github+json\',\'Content-Type\':\'application/json\'},body:JSON.stringify({files:{\'board.json\':{content:body}}})});if(g.ok){msg=\'Sent to Prop Edge. Click "Refresh board" in the app.\'}else{msg=\'Gist upload failed (\'+g.status+\'). Saved a file instead.\';const a=document.createElement(\'a\');a.href=URL.createObjectURL(new Blob([body],{type:\'application/json\'}));a.download=\'prizepicks_board.json\';document.body.appendChild(a);a.click()}}catch(e){msg=\'Gist upload failed (\'+e.message+\'). Saved a file instead.\';const a=document.createElement(\'a\');a.href=URL.createObjectURL(new Blob([body],{type:\'application/json\'}));a.download=\'prizepicks_board.json\';document.body.appendChild(a);a.click()}alert(msg+\'\\n\\n\'+props.length+\' props total\\n\'+rep.join(\'\\n\'))})();'


def bookmarklet(gist_id, token):
    """Personalised bookmarklet: scrape every league, push a slim board to the gist."""
    return "javascript:" + BOOKMARKLET_JS.replace("__GID__", gist_id.strip()).replace(
        "__TOK__", token.strip())

# ---------------------------------------------------------------- math
def american_to_prob(a):
    a = float(a)
    return 100.0 / (a + 100.0) if a > 0 else -a / (-a + 100.0)


def devig(price_over, price_under, method="power"):
    po, pu = american_to_prob(price_over), american_to_prob(price_under)
    if method == "mult":
        return po / (po + pu), pu / (po + pu)
    lo, hi = 0.01, 50.0
    for _ in range(100):
        k = (lo + hi) / 2
        lo, hi = (k, hi) if po ** k + pu ** k > 1 else (lo, k)
    fo = po ** k
    return fo, 1 - fo


def hit_distribution(probs):
    """P(exactly k hits) for independent legs with different probabilities."""
    d = [1.0]
    for p in probs:
        nd = [0.0] * (len(d) + 1)
        for k, v in enumerate(d):
            nd[k] += v * (1 - p)
            nd[k + 1] += v * p
        d = nd
    return d


def entry_ev(probs, table):
    d = hit_distribution(probs)
    return sum(d[k] * m for k, m in table.items() if k < len(d))


def breakeven(table):
    n = max(table)
    lo, hi = 0.01, 0.99
    for _ in range(100):
        mid = (lo + hi) / 2
        lo, hi = (mid, hi) if entry_ev([mid] * n, table) < 1 else (lo, mid)
    return (lo + hi) / 2


def payout_tables(df):
    out = {}
    for _, r in df.iterrows():
        n = int(re.sub(r"\D", "", str(r["Entry"])))
        t = {n: float(r["All hit"]), n - 1: float(r["Miss 1"]), n - 2: float(r["Miss 2"])}
        out[str(r["Entry"])] = {k: v for k, v in t.items() if v > 0 and k >= 0}
    return out

# ---------------------------------------------------------------- helpers
SUFFIXES = {"jr", "sr", "ii", "iii", "iv", "v"}


def norm_name(s):
    s = unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode().lower()
    s = re.sub(r"[^a-z0-9 ]", " ", s.replace("'", "").replace(".", ""))
    return " ".join(p for p in s.split() if p not in SUFFIXES)


def norm_stat(s):
    return re.sub(r"\s+", "", str(s).lower())


def parse_time(s):
    try:
        return datetime.fromisoformat(str(s).replace("Z", "+00:00"))
    except Exception:
        return None


def secret(name):
    try:
        return st.secrets.get(name)
    except Exception:
        return None

# ---------------------------------------------------------------- PrizePicks board
GIST_API = "https://api.github.com/gists/"


def gist_headers(token):
    h = {"Accept": "application/vnd.github+json"}
    if token:
        h["Authorization"] = "Bearer " + token.strip()
    return h


class BoardError(Exception):
    pass


@st.cache_data(ttl=120, show_spinner=False)
def fetch_gist_board(gist_id, token, nonce):
    """Pull board.json out of the gist. nonce busts the cache on an explicit refresh."""
    h = gist_headers(token)
    r = requests.get(GIST_API + gist_id.strip(), headers=h, timeout=30)
    if r.status_code == 404:
        raise BoardError("Gist not found. Check the ID, and add a token if the gist is secret.")
    if r.status_code in (401, 403):
        raise BoardError(f"GitHub rejected the token ({r.status_code}). It needs the 'gist' scope.")
    if r.status_code != 200:
        raise BoardError(f"GitHub returned {r.status_code}: {r.text[:200]}")
    files = r.json().get("files") or {}
    f = files.get("board.json") or next(iter(files.values()), None)
    if not f:
        raise BoardError("That gist has no files yet. Click the PP Board bookmark first.")
    if f.get("truncated") and f.get("raw_url"):
        raw = requests.get(f["raw_url"], headers=h, timeout=60)
        if raw.status_code != 200:
            raise BoardError(f"Could not read the full board ({raw.status_code}).")
        text = raw.text
    else:
        text = f.get("content") or ""
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        raise BoardError("The gist does not contain valid board JSON. Re-run the bookmark.")


def rows_from_payload(j):
    """Accepts the slim board the bookmarklet pushes, or raw PrizePicks JSON:API."""
    if j.get("_slim"):
        out = []
        for x in j.get("props", []):
            try:
                line = float(x["l"])
            except (KeyError, TypeError, ValueError):
                continue
            league = x.get("lg")
            if league not in LEAGUES:
                continue
            odds_type = x.get("ot") or "standard"
            out.append({"league": league, "player": x.get("p", ""), "team": x.get("t", "") or "",
                        "opp": x.get("o", "") or "", "stat": x.get("s", ""), "line": line,
                        "odds_type": odds_type, "can_less": odds_type == "standard",
                        "start_dt": parse_time(x.get("st", ""))})
        return [r for r in out if r["player"]]

    players = {i["id"]: i.get("attributes", {}) for i in j.get("included", [])
               if i.get("type") in ("new_player", "player")}
    rows = []
    for p in j.get("data", []):
        a = p.get("attributes", {}) or {}
        rel = p.get("relationships", {}) or {}
        pl = players.get(((rel.get("new_player") or {}).get("data") or {}).get("id"), {})
        name = pl.get("display_name") or pl.get("name") or ""
        if not name or pl.get("combo") or " + " in name:
            continue
        if a.get("status") not in (None, "pre_game"):
            continue
        league_rel = str(((rel.get("league") or {}).get("data") or {}).get("id", ""))
        league = a.get("_league") or PP_ID_TO_LEAGUE.get(league_rel) or \
            (pl.get("league") if pl.get("league") in LEAGUES else None)
        if league not in LEAGUES:
            continue
        try:
            line = float(a["line_score"])
        except (KeyError, TypeError, ValueError):
            continue
        odds_type = a.get("odds_type") or "standard"
        rows.append({"league": league, "player": name, "team": pl.get("team", "") or "",
                     "opp": a.get("description", "") or "", "stat": a.get("stat_type", ""),
                     "line": line, "odds_type": odds_type, "can_less": odds_type == "standard",
                     "start_dt": parse_time(a.get("start_time", ""))})
    return rows


def board_rows(payloads):
    """Merge one or more board payloads, dropping PrizePicks' duplicate listings."""
    rows, seen, fetched = [], set(), []
    for j in payloads:
        t = parse_time(j.get("_fetched_at", ""))
        if t:
            fetched.append(t)
        for r in rows_from_payload(j):
            key = (r["league"], r["player"], r["stat"], r["line"], r["odds_type"])
            if key in seen:
                continue
            seen.add(key)
            rows.append(r)
    return rows, fetched


# ---------------------------------------------------------------- The Odds API
class OddsError(Exception):
    pass


def _api_get(path, params):
    """Uncached transport. Keys starting with _ are cache-busters, not API params."""
    q = {k: v for k, v in dict(params).items() if not k.startswith("_")}
    r = requests.get(ODDS_API + path, params=q, timeout=25)
    if r.status_code != 200:
        raise OddsError(f"{r.status_code}: {r.text[:200]}")
    return r.json(), r.headers.get("x-requests-remaining")


@st.cache_data(ttl=3600, show_spinner=False)
def events_get(sport, api_key):
    """Cleared by the Refresh board button - that is how new games get discovered.
    Must NOT route through odds_get, whose long cache would mask new events."""
    return _api_get(f"/sports/{sport}/events", (("apiKey", api_key),))


@st.cache_data(ttl=6 * 3600, show_spinner=False)
def odds_get(path, params):
    """Long TTL: these are the calls that cost credits."""
    return _api_get(path, params)


def event_generation(key, max_age_min, force=False):
    """Bump an event's cache generation only once its odds have aged out.

    Same generation -> odds_get returns from cache, costing no credits.
    Returns (generation, is_fresh_fetch)."""
    store = st.session_state.setdefault("ev_fetched", {})
    gen, ts = store.get(key, (0, 0.0))
    never = max_age_min == float("inf")
    if force or (not never and time.time() - ts > max_age_min * 60) or ts == 0.0:
        store[key] = (gen + 1, time.time())
        return gen + 1, True
    return gen, False


def fetch_event(sport, event_id, markets, books, api_key, gen):
    path = f"/sports/{sport}/events/{event_id}/odds"
    base = (("apiKey", api_key), ("oddsFormat", "american"), ("bookmakers", ",".join(books)),
            ("_gen", gen))
    try:
        return [odds_get(path, base + (("markets", ",".join(markets)),))]
    except OddsError as e:
        if not str(e).startswith("422"):
            raise
        out = []  # one bad market key rejects the whole call - retry market by market
        for m in markets:
            try:
                out.append(odds_get(path, base + (("markets", m),)))
            except OddsError:
                pass
        return out


def price_pick(row, quotes, method, allow_bounds):
    line, cands = row["line"], []
    exact = [q for q in quotes if abs(q["point"] - line) < 1e-9]
    if exact:
        p_over = sum(devig(q["over"], q["under"], method)[0] for q in exact) / len(exact)
        bks = sorted({q["book"] for q in exact})
        cands = [("More", p_over, line, bks, "exact"), ("Less", 1 - p_over, line, bks, "exact")]
    elif allow_bounds and quotes:
        above = [q for q in quotes if q["point"] > line]
        below = [q for q in quotes if q["point"] < line]
        if above:
            pt = min(q["point"] for q in above)
            qs = [q for q in above if q["point"] == pt]
            p = sum(devig(q["over"], q["under"], method)[0] for q in qs) / len(qs)
            cands.append(("More", p, pt, sorted({q["book"] for q in qs}), "floor"))
        if below:
            pt = max(q["point"] for q in below)
            qs = [q for q in below if q["point"] == pt]
            p = sum(devig(q["over"], q["under"], method)[1] for q in qs) / len(qs)
            cands.append(("Less", p, pt, sorted({q["book"] for q in qs}), "floor"))
    if not row["can_less"]:
        cands = [c for c in cands if c[0] == "More"]
    return max(cands, key=lambda c: c[1]) if cands else None


def run_pricing(rows, leagues, books, method, bounds, hours, max_credits, api_key,
                include_alt, progress, max_age_min=60, force=False):
    now = datetime.now(timezone.utc)
    horizon = now + timedelta(hours=hours)
    results, unmapped, notes = [], Counter(), []
    spent_est, remaining, unmatched = 0, None, 0
    n_fetched, n_reused = 0, 0
    for li, lg in enumerate(leagues):
        sport, smap = LEAGUES[lg]["odds_key"], STAT_MAP[LEAGUES[lg]["stats"]]
        lrows = [r for r in rows if r["league"] == lg and (include_alt or r["odds_type"] == "standard")
                 and r["start_dt"] and now < r["start_dt"] <= horizon]
        for r in lrows:
            if norm_stat(r["stat"]) not in smap:
                unmapped[f"{lg}: {r['stat']}"] += 1
        markets = sorted({smap[norm_stat(r["stat"])] for r in lrows if norm_stat(r["stat"]) in smap})
        if not markets:
            notes.append(f"{lg}: no priceable props starting in the next {hours}h")
            continue
        try:
            events, rem = events_get(sport, api_key)
        except OddsError as e:
            notes.append(f"{lg}: Odds API error {e}")
            continue
        starts = {r["start_dt"] for r in lrows}
        evs = []
        for e in events:
            t = parse_time(e.get("commence_time"))
            if t and now < t <= horizon and any(abs((t - s).total_seconds()) <= 900 for s in starts):
                evs.append(e)  # only games PrizePicks is actually offering
        # An event already priced within max_age_min comes back from cache for free,
        # so only the new or stale ones count against the cap.
        plan = []
        for e in evs:
            gen, is_new = event_generation((sport, e["id"], tuple(markets), tuple(books)),
                                           max_age_min, force)
            plan.append((e, gen, is_new))
        due = [x for x in plan if x[2]]
        est = len(due) * len(markets)
        if spent_est + est > max_credits:
            notes.append(f"{lg}: skipped - {len(due)} game(s) need pricing, up to {est} credits "
                         f"(cap {max_credits}). Raise the cap or shorten the time window.")
            for e, _, _ in due:   # undo the bump so they stay due next run
                st.session_state["ev_fetched"].pop(
                    (sport, e["id"], tuple(markets), tuple(books)), None)
            continue
        spent_est += est
        quotes = defaultdict(list)
        for ei, (e, gen, is_new) in enumerate(plan):
            n_fetched, n_reused = n_fetched + is_new, n_reused + (not is_new)
            progress((li + (ei + 1) / max(len(plan), 1)) / len(leagues),
                     f"{lg}: {e.get('away_team')} @ {e.get('home_team')}"
                     + ("" if is_new else " (cached)"))
            try:
                responses = fetch_event(sport, e["id"], markets, books, api_key, gen)
            except OddsError as err:
                notes.append(f"{lg} {e.get('away_team')} @ {e.get('home_team')}: {err}")
                continue
            for data, r_rem in responses:
                remaining = r_rem or remaining
                for bk in data.get("bookmakers", []):
                    for m in bk.get("markets", []):
                        pair = defaultdict(dict)
                        for o in m.get("outcomes", []):
                            if o.get("point") is None or not o.get("description"):
                                continue
                            pair[(o["description"], float(o["point"]))][o["name"].lower()] = o["price"]
                        for (player, point), sides in pair.items():
                            if "over" in sides and "under" in sides:
                                quotes[(norm_name(player), m["key"])].append(
                                    {"book": bk["key"], "point": point,
                                     "over": sides["over"], "under": sides["under"]})
        for r in lrows:
            mkt = smap.get(norm_stat(r["stat"]))
            if not mkt:
                continue
            priced = price_pick(r, quotes.get((norm_name(r["player"]), mkt), []), method, bounds)
            if not priced:
                unmatched += 1
                continue
            side, prob, bline, bks, basis = priced
            results.append({
                "League": lg, "Player": r["player"], "Team": r["team"], "Opp": r["opp"],
                "Stat": r["stat"], "Line": r["line"], "Pick": side, "Prob": round(prob * 100, 2),
                "Book line": bline, "Books": ", ".join(BOOKS.get(b, b) for b in bks),
                "# Books": len(bks), "Basis": basis,
                "Type": r["odds_type"], "Start": r["start_dt"].astimezone(LOCAL_TZ).strftime("%a %I:%M %p").replace(" 0", " "),
                "Push risk": float(r["line"]).is_integer(), "_start": r["start_dt"].isoformat(),
            })
    return (pd.DataFrame(results), unmapped, notes, remaining, unmatched,
            n_fetched, n_reused, spent_est)



def build_slips(df, tables, one_per_game=True, one_per_player=True, min_prob=0.0):
    """Best slip of each type. For independent legs, EV rises with every leg's
    probability, so the top-n eligible picks by probability IS the optimal slip."""
    pool, used_players, used_games = [], set(), set()
    for r in df.sort_values("Prob", ascending=False).to_dict("records"):
        if r["Prob"] / 100 < min_prob:
            break
        if one_per_player and r["Player"] in used_players:
            continue
        game = (r["League"], r["_start"], frozenset({r["Team"], r["Opp"]} - {""}))
        if one_per_game and any(game[:2] == g[:2] and (game[2] & g[2]) for g in used_games):
            continue
        used_players.add(r["Player"])
        used_games.add(game)
        pool.append(r)
    out = []
    for name, table in tables.items():
        n = max(table)
        if n > len(pool):
            continue
        legs = pool[:n]
        probs = [x["Prob"] / 100 for x in legs]
        d = hit_distribution(probs)
        out.append({"slip": name, "n": n, "ev": entry_ev(probs, table) - 1,
                    "all_hit": math.prod(probs), "cash": sum(d[k] for k in table),
                    "legs": legs})
    return sorted(out, key=lambda s: s["ev"], reverse=True), pool


def slip_text(s):
    lines = [f"{s['slip']} - EV {s['ev'] * 100:+.1f}% - all hit {s['all_hit']:.1%}"]
    for x in s["legs"]:
        lines.append(f"{x['Player']} ({x['League']}) {x['Pick']} {x['Line']:g} {x['Stat']}"
                     f"  [{x['Prob']:.1f}%]")
    return "\n".join(lines)


def same_game(a, b):
    return a["League"] == b["League"] and a["_start"] == b["_start"] and \
        bool({a["Team"], a["Opp"]} & {b["Team"], b["Opp"]} - {""})

# ---------------------------------------------------------------- UI
CSS = """
<style>
.block-container {padding-top: 2rem;}
div[data-testid="stMetric"] {background: rgba(34,197,94,0.06); border: 1px solid rgba(34,197,94,0.25);
  border-radius: 12px; padding: 12px 16px;}
.pick-card {border: 1px solid rgba(148,163,184,0.25); border-radius: 14px; padding: 14px 18px;
  background: rgba(148,163,184,0.06);}
.pick-card .who {font-size: 1.05rem; font-weight: 700;}
.pick-card .what {opacity: 0.8; margin: 2px 0 8px 0;}
.pick-card .prob {font-size: 1.8rem; font-weight: 800; color: #22c55e;}
.pick-card .meta {font-size: 0.8rem; opacity: 0.7;}
</style>
"""


def setup_help(gist_id, token):
    st.subheader("One-time setup")
    st.markdown(
        "PrizePicks blocks servers, so the board has to come from your own browser. Set this up "
        "once and after that it is two clicks: the bookmark, then **Refresh board** here.\n\n"
        "1. Go to [gist.github.com](https://gist.github.com), filename **board.json**, content `{}`, "
        "then **Create secret gist**. Copy the long ID from the end of its URL.\n"
        "2. Go to [github.com/settings/tokens](https://github.com/settings/tokens) → "
        "**Generate new token (classic)**. Tick **only** the `gist` box, generate, copy it.\n"
        "3. Put both in this app's **Settings → Secrets** (then they are remembered):\n"
        "```\nGIST_ID = \"your gist id\"\nGITHUB_TOKEN = \"your token\"\n```\n"
        "4. Paste them below to build your bookmark, then in Chrome press Ctrl+Shift+B, "
        "right-click the bookmarks bar → **Add page**, name it **PP Board**, and paste the "
        "generated code as the URL."
    )
    g = st.text_input("Gist ID", value=gist_id or "")
    t = st.text_input("GitHub token (used only to build the code below - not stored)",
                      value=token or "", type="password")
    if g and t:
        st.caption("Copy this whole thing into the bookmark's URL field:")
        st.code(bookmarklet(g, t), language=None, wrap_lines=True)
        st.warning("This code contains your token, so keep the bookmark to yourself. "
                   "A `gist`-only token can touch nothing but your gists.")
    else:
        st.info("Enter both above to generate your bookmark.")
    st.divider()
    st.markdown(
        "**Each time you want fresh lines:** open "
        "[api.prizepicks.com/projections](https://api.prizepicks.com/projections?league_id=9&per_page=10) "
        "(solve the captcha if one appears), click **PP Board**, wait for the popup, then come back "
        "here and click **Refresh board**.\n\n"
        "If the gist upload ever fails, the bookmark saves a file instead - switch the sidebar to "
        "**Upload file** and load it that way."
    )


def main():
    st.set_page_config(page_title="Prop Edge", page_icon="📈", layout="wide")
    st.markdown(CSS, unsafe_allow_html=True)

    pw = secret("APP_PASSWORD")
    if pw and not st.session_state.get("authed"):
        st.title("📈 Prop Edge")
        if st.text_input("Password", type="password") == pw:
            st.session_state.authed = True
            st.rerun()
        st.stop()

    with st.sidebar:
        st.title("📈 Prop Edge")
        api_key = secret("ODDS_API_KEY") or st.text_input("Odds API key", type="password")
        gist_id, gh_token = secret("GIST_ID"), secret("GITHUB_TOKEN")
        source = st.radio("Board source", ["GitHub Gist", "Upload file"], horizontal=True,
                          index=0 if gist_id else 1,
                          help="The Gist option needs no downloading or uploading.")
        uploads = []
        if source == "GitHub Gist":
            if st.button("Refresh board", width="stretch"):
                st.session_state.gist_nonce = st.session_state.get("gist_nonce", 0) + 1
                fetch_gist_board.clear()   # the nonce alone is unreliable - a page reload
                events_get.clear()         # resets it and re-serves a stale cache entry
        else:
            uploads = st.file_uploader("PrizePicks board (.json)", type="json",
                                       accept_multiple_files=True)
        books = st.multiselect("Sportsbooks", list(BOOKS), default=DEFAULT_BOOKS, format_func=BOOKS.get,
                               help="Up to 10 books cost the same credits as 1.")
        entry = st.selectbox("Rank against entry", DEFAULT_PAYOUTS["Entry"].tolist(), index=8)
        method = st.radio("De-vig method", ["power", "mult"], horizontal=True,
                          format_func={"power": "Power", "mult": "Multiplicative"}.get)
        bounds = st.toggle("Use nearby book lines as floors", value=True)
        include_alt = st.toggle("Include demons / goblins", value=False)
        hours = st.slider("Games starting within (hours)", 1, 96, 36)
        max_credits = st.number_input("Max credits per run", 10, 20000, 400, step=50)
        auto_stale = st.toggle("Auto re-price aging odds", value=False,
                               help="Off: a game is priced once and never re-priced unless you "
                                    "ask. On: it re-prices after the age below.")
        max_age_min = (st.slider("Re-price a game after (min)", 5, 360, 60, step=5)
                       if auto_stale else float("inf"))
        force_all = st.button("Re-price everything now", width="stretch",
                              help="The only thing that repays for games already priced.")
        with st.expander("Payout table - verify in app"):
            pay_df = st.data_editor(DEFAULT_PAYOUTS, hide_index=True, disabled=["Entry"], key="payouts")

    tables = payout_tables(pay_df)
    be = {k: breakeven(v) for k, v in tables.items()}

    st.title("PrizePicks vs. the books")
    st.caption(f"No-vig sportsbook probability for every PrizePicks prop. "
               f"{entry} break-even: **{be[entry]:.1%}** per leg.")

    payloads = []
    if source == "GitHub Gist":
        if not gist_id:
            setup_help(gist_id, gh_token)
            st.stop()
        try:
            payloads = [fetch_gist_board(gist_id, gh_token,
                                         st.session_state.get("gist_nonce", 0))]
        except BoardError as e:
            st.error(str(e))
            with st.expander("Set up the gist board"):
                setup_help(gist_id, gh_token)
            st.stop()
    else:
        if not uploads:
            setup_help(gist_id, gh_token)
            st.stop()
        payloads = [json.load(f) for f in uploads]

    rows, fetched = board_rows(payloads)
    counts = Counter(r["league"] for r in rows)
    if not rows:
        st.warning("That board has no supported props yet. Run the PP Board bookmark, "
                   "then click Refresh board.")
        setup_help(gist_id, gh_token)
        st.stop()

    c1, c2 = st.columns([3, 1])
    with c1:
        avail = [lg for lg in LEAGUES if counts[lg]]
        preset = [lg for lg in DEFAULT_LEAGUES if lg in avail] or avail
        leagues = st.multiselect("Leagues to price", avail, default=preset,
                                 format_func=lambda lg: f"{lg} ({counts[lg]})",
                                 help="Each league costs credits separately. Add or remove freely.")
    with c2:
        st.write("")
        go = st.button("Price the board", type="primary", width="stretch",
                       disabled=not (api_key and leagues and books))
    if fetched:
        age = (datetime.now(timezone.utc) - max(fetched)).total_seconds() / 60
        stale = " - click PP Board again, then Refresh board." if age > 30 else ""
        (st.warning if age > 30 else st.caption)(f"Board captured {age:.0f} min ago{stale}")
    if not api_key:
        st.info("Add your Odds API key in the sidebar (or in the app's secrets).")

    if go or force_all:
        bar = st.progress(0.0, text="Pricing...")
        df, unmapped, notes, remaining, unmatched, n_new, n_reused, spent = run_pricing(
            rows, leagues, books, method, bounds, hours, max_credits, api_key, include_alt,
            lambda f, t: bar.progress(min(f, 1.0), text=t), max_age_min, force_all)
        bar.empty()
        st.session_state.update(res=df, unmapped=unmapped, notes=notes, remaining=remaining,
                                unmatched=unmatched, run_at=datetime.now(LOCAL_TZ),
                                n_new=n_new, n_reused=n_reused, spent=spent)

    if "res" not in st.session_state:
        return
    df = st.session_state.res.copy()
    for n in st.session_state.notes:
        st.warning(n)
    if df.empty:
        st.info("Nothing priced. Check the notes above or widen the time window.")
        return
    df["Edge"] = (df["Prob"] - be[entry] * 100).round(2)
    df = df.sort_values("Prob", ascending=False).reset_index(drop=True)

    m1, m2, m3, m4 = st.columns(4)
    m1.metric("Props priced", len(df))
    m2.metric(f"Above {entry} break-even", int((df["Edge"] > 0).sum()))
    m3.metric("Best probability", f"{df['Prob'].max():.1f}%")
    m4.metric("Odds API credits left", st.session_state.remaining or "cached",
              help=f"~{st.session_state.get('spent', 0)} credits this run. "
                   f"{st.session_state.get('n_new', 0)} game(s) priced, "
                   f"{st.session_state.get('n_reused', 0)} reused free.")

    top = df[df["Edge"] > 0].head(3)
    if not top.empty:
        cols = st.columns(3)
        for col, (_, r) in zip(cols, top.iterrows()):
            col.markdown(
                f"<div class='pick-card'><div class='who'>{r['Player']} <span class='meta'>{r['League']} · "
                f"{r['Team']} vs {r['Opp']}</span></div><div class='what'>{r['Pick']} {r['Line']:g} {r['Stat']}"
                f"</div><div class='prob'>{r['Prob']:.1f}%</div><div class='meta'>{r['Edge']:+.1f} pts vs "
                f"break-even · {r['# Books']} book(s) · {r['Basis']}</div></div>", unsafe_allow_html=True)

    st.write("")
    st.caption("These filters drive both the table and the suggested slips below.")
    f1, f2, f3, f4, f5 = st.columns([2, 2.6, 1.4, 1.3, 1.3])
    fl = f1.multiselect("League", sorted(df["League"].unique()), placeholder="All")
    fs = f2.multiselect("Stat", sorted(df["Stat"].unique()), placeholder="All")
    upcoming = f3.toggle("Not started yet", value=True,
                         help="Hides games that have already kicked off since you priced.")
    plus = f4.toggle("+EV only", value=False)
    exact_only = f5.toggle("Exact lines only", value=False)
    view = df
    if fl:
        view = view[view["League"].isin(fl)]
    if fs:
        view = view[view["Stat"].isin(fs)]
    if upcoming:
        started = pd.to_datetime(view["_start"], utc=True, format="mixed", errors="coerce")
        view = view[started.isna() | (started > pd.Timestamp.now(tz="UTC"))]
    if plus:
        view = view[view["Edge"] > 0]
    if exact_only:
        view = view[view["Basis"] == "exact"]
    view = view.reset_index(drop=True)
    if view.empty:
        st.warning("No props match those filters.")

    shown = ["League", "Player", "Stat", "Line", "Pick", "Prob", "Edge", "Book line", "Books",
             "Basis", "Start", "Push risk"]
    styled = view[shown].style.map(
        lambda v: f"color: {'#22c55e' if v > 0 else '#ef4444'}; font-weight: 600", subset=["Edge"]
    ).format({"Edge": "{:+.1f}", "Line": "{:g}", "Book line": "{:g}"})
    st.caption("Tick rows to build an entry below.")
    event = st.dataframe(
        styled, hide_index=True, width="stretch", height=520, on_select="rerun",
        selection_mode="multi-row", key="board",
        column_config={
            "Prob": st.column_config.ProgressColumn("Prob", format="%.1f%%", min_value=40, max_value=70),
            "Edge": st.column_config.Column("Edge (pts)", help=f"Probability minus {entry} break-even"),
            "Push risk": st.column_config.CheckboxColumn("Whole line", help="Landing exactly on it is a push"),
        })

    st.subheader("Suggested slips")
    sc1, sc2, sc3 = st.columns([1.2, 1.2, 2])
    one_game = sc1.toggle("One pick per game", value=True,
                          help="Same-game legs are correlated, which breaks the EV math.")
    one_player = sc2.toggle("One pick per player", value=True)
    floor = sc3.slider("Minimum probability per leg (%)", 50.0, 60.0, float(be[entry] * 100), 0.1)
    slips, pool = build_slips(view, tables, one_game, one_player, floor / 100)
    st.caption(f"{len(pool)} eligible picks from the {len(view)} shown above. "
               "Legs are the highest-probability eligible picks, which is the best slip of each "
               "size when legs are independent.")
    if not slips:
        st.info("No slip fits those filters. Lower the minimum probability or turn off a filter.")
    else:
        pos = [s for s in slips if s["ev"] > 0]
        if not pos:
            st.warning("Every slip is negative EV with these picks. The best is still a losing bet "
                       "on average.")
        for s in slips[:3]:
            c = st.container(border=True)
            h, m = c.columns([2, 3])
            h.markdown(f"### {s['slip']}")
            h.metric("Expected return", f"{s['ev'] * 100:+.1f}%")
            m.write("")
            m1, m2 = m.columns(2)
            m1.metric("All hit", f"{s['all_hit']:.1%}")
            m2.metric("Cashes anything", f"{s['cash']:.1%}")
            c.dataframe(pd.DataFrame(s["legs"])[["League", "Player", "Pick", "Line", "Stat",
                                                 "Prob", "Start"]],
                        hide_index=True, width="stretch")
            with c.expander("Copy for the PrizePicks app"):
                st.code(slip_text(s), language=None, wrap_lines=True)
        with st.expander("All slip types ranked"):
            st.dataframe(pd.DataFrame([{"Slip": s["slip"], "Legs": s["n"],
                                        "EV %": round(s["ev"] * 100, 1),
                                        "All hit %": round(s["all_hit"] * 100, 1),
                                        "Cashes %": round(s["cash"] * 100, 1)} for s in slips]),
                         hide_index=True, width="stretch")

    st.subheader("Entry builder")
    sel = view.iloc[event.selection.rows] if event.selection.rows else view.iloc[0:0]
    if len(sel) < 2:
        st.caption("Select 2-6 picks in the table to see power and flex expected value.")
    elif len(sel) > 6:
        st.warning("PrizePicks entries max out at 6 picks.")
    else:
        n, probs = len(sel), (sel["Prob"] / 100).tolist()
        e1, e2, e3 = st.columns(3)
        if f"{n}P" in tables:
            ev = entry_ev(probs, tables[f"{n}P"])
            e1.metric(f"{n}-pick Power EV", f"{(ev - 1) * 100:+.1f}%", help="Expected return per $1")
        if f"{n}F" in tables:
            ev = entry_ev(probs, tables[f"{n}F"])
            e2.metric(f"{n}-pick Flex EV", f"{(ev - 1) * 100:+.1f}%", help="Expected return per $1")
        e3.metric("All hit", f"{math.prod(probs):.1%}")
        recs = sel.to_dict("records")
        pairs = [(a["Player"], b["Player"]) for i, a in enumerate(recs) for b in recs[i + 1:] if same_game(a, b)]
        if pairs:
            st.warning("Same-game picks (outcomes are correlated, so EV above is approximate): "
                       + "; ".join(f"{a} + {b}" for a, b in pairs))
        st.dataframe(sel[["Player", "Stat", "Line", "Pick", "Prob", "Start"]], hide_index=True, width="stretch")

    with st.expander("PP Board bookmark setup"):
        setup_help(gist_id, gh_token)

    with st.expander("Diagnostics"):
        st.write(f"PrizePicks props with no sportsbook match: {st.session_state.unmatched}")
        if st.session_state.unmapped:
            st.write("Stat types with no sportsbook market (not priced):")
            st.dataframe(pd.DataFrame(st.session_state.unmapped.most_common(),
                                      columns=["Stat", "Props"]), hide_index=True)
    stamp = st.session_state.run_at.strftime("%Y%m%d_%H%M")
    out = df.drop(columns=["_start"]).assign(run_at=st.session_state.run_at.isoformat(), entry=entry,
                                            devig=method)
    st.download_button("Download this run (CSV)", out.to_csv(index=False), f"prop_edge_{stamp}.csv",
                       "text/csv")


if __name__ == "__main__":
    main()
