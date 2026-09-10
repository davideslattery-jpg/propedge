"""Prop Edge - rank PrizePicks props by no-vig sportsbook probability (Streamlit app)."""
import json, math, re, unicodedata
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

BOOKMARKLET = "javascript:(async()=>{const L={NFL:9,CFB:15,MLB:2,WNBA:3,NBA:7,NHL:8};const o={data:[],included:[],_fetched_at:new Date().toISOString()};const s=new Set();const rep=[];const w=z=>new Promise(r=>setTimeout(r,z));for(const[k,id]of Object.entries(L)){let c='';for(let t=0;t<3;t++){try{const r=await fetch('/projections?league_id='+id+'&per_page=1000&single_stat=true&game_mode=pickem',{credentials:'include'});if(!r.ok){c='blocked ('+r.status+')';await w(2000);continue}const j=await r.json();let m=0;for(const p of j.data||[]){(p.attributes=p.attributes||{})._league=k;o.data.push(p);m++}for(const i of j.included||[]){const q=i.type+':'+i.id;if(!s.has(q)){s.add(q);o.included.push(i)}}c=m+' props';break}catch(e){c='error';await w(2000)}}rep.push(k+': '+c);await w(1000)}const a=document.createElement('a');a.href=URL.createObjectURL(new Blob([JSON.stringify(o)],{type:'application/json'}));a.download='prizepicks_board.json';document.body.appendChild(a);a.click();alert('Saved prizepicks_board.json\\n\\n'+rep.join('\\n'))})();"

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
def load_boards(files):
    data, included, fetched = [], {}, []
    for f in files:
        j = json.load(f)
        data += j.get("data", [])
        for i in j.get("included", []):
            included[(i.get("type"), i.get("id"))] = i
        if j.get("_fetched_at"):
            fetched.append(parse_time(j["_fetched_at"]))
    return {"data": data, "included": list(included.values())}, [t for t in fetched if t]


def parse_board(payload):
    players = {i["id"]: i.get("attributes", {}) for i in payload.get("included", [])
               if i.get("type") in ("new_player", "player")}
    rows, seen = [], set()
    for p in payload.get("data", []):
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
        key = (league, name, a.get("stat_type"), line, odds_type)
        if key in seen:
            continue  # PrizePicks sometimes lists the same prop twice
        seen.add(key)
        rows.append({"league": league, "player": name, "team": pl.get("team", "") or "",
                     "opp": a.get("description", "") or "", "stat": a.get("stat_type", ""),
                     "line": line, "odds_type": odds_type, "can_less": odds_type == "standard",
                     "start_dt": parse_time(a.get("start_time", ""))})
    return rows

# ---------------------------------------------------------------- The Odds API
class OddsError(Exception):
    pass


@st.cache_data(ttl=600, show_spinner=False)
def odds_get(path, params):
    r = requests.get(ODDS_API + path, params=dict(params), timeout=25)
    if r.status_code != 200:
        raise OddsError(f"{r.status_code}: {r.text[:200]}")
    return r.json(), r.headers.get("x-requests-remaining")


def fetch_event(sport, event_id, markets, books, api_key):
    path = f"/sports/{sport}/events/{event_id}/odds"
    base = (("apiKey", api_key), ("oddsFormat", "american"), ("bookmakers", ",".join(books)))
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
                include_alt, progress):
    now = datetime.now(timezone.utc)
    horizon = now + timedelta(hours=hours)
    results, unmapped, notes = [], Counter(), []
    spent_est, remaining, unmatched = 0, None, 0
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
            events, rem = odds_get(f"/sports/{sport}/events", (("apiKey", api_key),))
        except OddsError as e:
            notes.append(f"{lg}: Odds API error {e}")
            continue
        starts = {r["start_dt"] for r in lrows}
        evs = []
        for e in events:
            t = parse_time(e.get("commence_time"))
            if t and now < t <= horizon and any(abs((t - s).total_seconds()) <= 900 for s in starts):
                evs.append(e)  # only games PrizePicks is actually offering
        est = len(evs) * len(markets)
        if spent_est + est > max_credits:
            notes.append(f"{lg}: skipped - could cost up to {est} credits (cap {max_credits}). "
                         f"Raise the cap or shorten the time window.")
            continue
        spent_est += est
        quotes = defaultdict(list)
        for ei, e in enumerate(evs):
            progress((li + (ei + 1) / max(len(evs), 1)) / len(leagues),
                     f"{lg}: {e.get('away_team')} @ {e.get('home_team')}")
            try:
                responses = fetch_event(sport, e["id"], markets, books, api_key)
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
    return pd.DataFrame(results), unmapped, notes, remaining, unmatched


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


def setup_help():
    st.subheader("Load the PrizePicks board")
    st.markdown(
        "PrizePicks blocks servers, so the board comes from your own browser. One-time setup:\n\n"
        "1. In Chrome, show the bookmarks bar (Ctrl+Shift+B), right-click it, choose **Add page**.\n"
        "2. Name it **PP Board**, paste the code below as the URL, save.\n\n"
        "Each time you want fresh lines:\n\n"
        "1. Open [api.prizepicks.com/projections](https://api.prizepicks.com/projections?league_id=9&per_page=10) "
        "(solve the captcha if one appears - you should see raw text).\n"
        "2. Click **PP Board**. It grabs every supported league and downloads `prizepicks_board.json`.\n"
        "3. Upload that file in the sidebar."
    )
    st.code(BOOKMARKLET, language="javascript", wrap_lines=True)
    st.caption("Single-league files saved straight from the browser (like nfl.json) also work.")


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
        uploads = st.file_uploader("PrizePicks board (.json)", type="json", accept_multiple_files=True)
        books = st.multiselect("Sportsbooks", list(BOOKS), default=DEFAULT_BOOKS, format_func=BOOKS.get,
                               help="Up to 10 books cost the same credits as 1.")
        entry = st.selectbox("Rank against entry", DEFAULT_PAYOUTS["Entry"].tolist(), index=8)
        method = st.radio("De-vig method", ["power", "mult"], horizontal=True,
                          format_func={"power": "Power", "mult": "Multiplicative"}.get)
        bounds = st.toggle("Use nearby book lines as floors", value=True)
        include_alt = st.toggle("Include demons / goblins", value=False)
        hours = st.slider("Games starting within (hours)", 1, 96, 36)
        max_credits = st.number_input("Max credits per run", 10, 20000, 400, step=50)
        with st.expander("Payout table - verify in app"):
            pay_df = st.data_editor(DEFAULT_PAYOUTS, hide_index=True, disabled=["Entry"], key="payouts")

    tables = payout_tables(pay_df)
    be = {k: breakeven(v) for k, v in tables.items()}

    st.title("PrizePicks vs. the books")
    st.caption(f"No-vig sportsbook probability for every PrizePicks prop. "
               f"{entry} break-even: **{be[entry]:.1%}** per leg.")

    if not uploads:
        setup_help()
        st.stop()

    board, fetched = load_boards(uploads)
    rows = parse_board(board)
    counts = Counter(r["league"] for r in rows)
    if not rows:
        st.error("No supported props found in that file. Make sure it's the raw PrizePicks JSON.")
        st.stop()

    c1, c2 = st.columns([3, 1])
    with c1:
        leagues = st.multiselect("Leagues to price", [lg for lg in LEAGUES if counts[lg]],
                                 default=[lg for lg in LEAGUES if counts[lg]],
                                 format_func=lambda lg: f"{lg} ({counts[lg]})")
    with c2:
        st.write("")
        go = st.button("Price the board", type="primary", width="stretch",
                       disabled=not (api_key and leagues and books))
    if fetched:
        age = (datetime.now(timezone.utc) - max(fetched)).total_seconds() / 60
        (st.warning if age > 30 else st.caption)(f"Board saved {age:.0f} min ago"
                                                  + (" - re-save for current lines." if age > 30 else ""))
    if not api_key:
        st.info("Add your Odds API key in the sidebar (or in the app's secrets).")

    if go:
        bar = st.progress(0.0, text="Pricing...")
        df, unmapped, notes, remaining, unmatched = run_pricing(
            rows, leagues, books, method, bounds, hours, max_credits, api_key, include_alt,
            lambda f, t: bar.progress(min(f, 1.0), text=t))
        bar.empty()
        st.session_state.update(res=df, unmapped=unmapped, notes=notes, remaining=remaining,
                                unmatched=unmatched, run_at=datetime.now(LOCAL_TZ))

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
    m4.metric("Odds API credits left", st.session_state.remaining or "cached")

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
    f1, f2, f3, f4 = st.columns([2, 3, 1.3, 1.3])
    fl = f1.multiselect("League", sorted(df["League"].unique()), placeholder="All")
    fs = f2.multiselect("Stat", sorted(df["Stat"].unique()), placeholder="All")
    plus = f3.toggle("+EV only", value=False)
    exact_only = f4.toggle("Exact lines only", value=False)
    view = df
    if fl:
        view = view[view["League"].isin(fl)]
    if fs:
        view = view[view["Stat"].isin(fs)]
    if plus:
        view = view[view["Edge"] > 0]
    if exact_only:
        view = view[view["Basis"] == "exact"]
    view = view.reset_index(drop=True)

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
