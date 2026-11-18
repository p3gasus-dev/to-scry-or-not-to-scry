#!/usr/bin/env python3
"""Builds data.json for "To Scry or Not to Scry". Standard library only.

Sources
  poe.ninja        league list, Scrying Orb and divination card prices (essential)
  Maps of Exile    map ratings, league of the ratings (essential)
  PoEDB            the four cards listed for each Atlas map (best effort)

If an essential source fails, this exits non-zero so the previously deployed
site stays up. PoEDB is refreshed at most once a day and never fatal: a bad or
missing parse falls back to the last good lists, then to the bundled snapshot.
"""
import argparse
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from html.parser import HTMLParser

NINJA = "https://poe.ninja/poe1/api/economy"
MOE = "https://raw.githubusercontent.com/deathbeam/maps-of-exile/master/site/src/data"
POEDB_ATLAS = "https://poedb.tw/us/Atlas_of_Worlds"


def log(message):
    print(message, file=sys.stderr)


def norm(text):
    """Collapse whitespace and straighten apostrophes so names compare equal."""
    text = text.replace("’", "'").replace("‘", "'").replace(" ", " ")
    return re.sub(r"\s+", " ", text).strip()


# --------------------------------------------------------------------------- #
# Fetching
# --------------------------------------------------------------------------- #

class Fetcher:
    def __init__(self, user_agent, fixtures=None):
        self.user_agent = user_agent
        self.fixtures = fixtures

    def get(self, name, url, optional=False, tolerate=()):
        """Return the response body as text.

        name      file to read instead of the network when running with --fixtures
        optional  return None instead of failing
        tolerate  HTTP status codes that mean "nothing there" and return None
        """
        if self.fixtures:
            path = os.path.join(self.fixtures, name)
            if os.path.exists(path):
                with open(path, encoding="utf-8") as handle:
                    return handle.read()
            if optional or tolerate:
                return None
            raise SystemExit("missing fixture: " + name)

        error = None
        for attempt in range(3):
            try:
                request = urllib.request.Request(url, headers={"User-Agent": self.user_agent})
                with urllib.request.urlopen(request, timeout=30) as response:
                    return response.read().decode("utf-8", "replace")
            except urllib.error.HTTPError as exc:
                if exc.code in tolerate:
                    return None
                error = exc
                if 400 <= exc.code < 500 and exc.code != 429:
                    break  # retrying will not help
            except Exception as exc:  # network trouble: retry
                error = exc
            time.sleep(2 * (attempt + 1))

        if optional:
            log("optional source failed: %s (%s)" % (url, error))
            return None
        raise SystemExit("could not fetch %s: %s" % (url, error))


# --------------------------------------------------------------------------- #
# PoEDB Atlas page -> {map name: [card names]}
# --------------------------------------------------------------------------- #

class _Tokens(HTMLParser):
    """Flattens a page into ("text", s) and ("a", s) tokens, in document order."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.tokens = []
        self._hidden = 0
        self._anchor = None

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style"):
            self._hidden += 1
        elif tag == "a":
            self._anchor = []

    def handle_endtag(self, tag):
        if tag in ("script", "style"):
            self._hidden = max(0, self._hidden - 1)
        elif tag == "a" and self._anchor is not None:
            text = norm(" ".join(self._anchor))
            if text:
                self.tokens.append(("a", text))
            self._anchor = None

    def handle_data(self, data):
        if self._hidden:
            return
        if self._anchor is not None:
            self._anchor.append(data)
            return
        text = norm(data)
        if text:
            self.tokens.append(("text", text))


_OTHER_LABELS = {
    "bonus", "boss fights", "boss fight", "boss", "bosses",
    "crafting bench recipe", "crafting bench recipes", "crafting recipe",
}


def parse_poedb(html, atlas_maps, known_cards):
    """Read each map block of the Atlas page.

    A block starts at the map's name link followed by its "Tier" label, then has
    a "Card" label whose links are the cards, then a "Link" label whose links are
    neighbouring maps (ignored). Only names found in the two known lists count, so
    unexpected markup yields little or nothing instead of wrong names.
    """
    parser = _Tokens()
    parser.feed(html)
    parser.close()

    tier_labels = sum(1 for kind, text in parser.tokens
                      if kind == "text" and re.match(r"(?i)tier\b", text))
    if tier_labels < 20:
        log("PoEDB: found %d 'Tier' labels, page layout not recognised" % tier_labels)
        return {}

    result = {}
    owner = None
    mode = None
    tokens = parser.tokens
    for index, (kind, text) in enumerate(tokens):
        if kind == "text":
            label = text.rstrip(":").strip().lower()
            if re.match(r"tier\b", label):
                # The map's name is the nearest map-name token just before its "Tier" label,
                # whether the page shows it as plain text or as a link.
                owner = None
                for kind_before, before in reversed(tokens[max(0, index - 4):index]):
                    if before in atlas_maps:
                        owner = before
                        break
                if owner in result:
                    owner = None  # the same map listed twice: keep the first
                elif owner:
                    result[owner] = []
                mode = None
                continue
            if re.fullmatch(r"cards?", label):
                mode = "cards"
                continue
            if re.fullmatch(r"links?|connections?|connected( maps)?", label):
                mode = "link"
                continue
            if label in _OTHER_LABELS or (text.endswith(":") and len(text) <= 30):
                mode = None
                continue
        if mode == "cards" and owner and text in known_cards and text not in result[owner]:
            result[owner].append(text)
    return {name: cards for name, cards in result.items() if cards}


def plausible(parsed, atlas_maps):
    sizes = [len(cards) for cards in parsed.values()]
    average = sum(sizes) / len(sizes) if sizes else 0
    log("PoEDB: %d of %d Atlas maps parsed, %.1f cards per map on average, at most %d"
        % (len(parsed), len(atlas_maps), average, max(sizes) if sizes else 0))
    missing = sorted(atlas_maps - set(parsed))
    if missing:
        log("PoEDB: not found: " + ", ".join(missing[:25]) + (" …" if len(missing) > 25 else ""))
    if len(parsed) < max(30, int(0.5 * len(atlas_maps))):
        return False
    return 2 <= average <= 8 and max(sizes) <= 20


def agrees_with(parsed, reference):
    """Cheap check that cards were attached to the right maps: most maps should
    still share at least one card with the last good lists."""
    shared = [name for name in parsed if name in reference]
    if len(shared) < 20:
        return True  # not enough overlap to judge
    hits = sum(1 for name in shared if set(parsed[name]) & set(reference[name]))
    return hits / len(shared) >= 0.4


def choose_card_lists(fetcher, previous, seed, atlas_maps, known_cards, today, force):
    """Returns (lists, source, updated). source is "live" or "snapshot"."""
    previous = previous or {}
    meta = previous.get("cardLists") or {}
    last_good = {}
    if meta.get("source") == "live":
        last_good = {name: [c["name"] for c in cards]
                     for name, cards in (previous.get("mapCards") or {}).items()}

    if last_good and meta.get("updated") == today:
        return last_good, "live", today  # already refreshed today

    html = fetcher.get("poedb.html", POEDB_ATLAS, optional=True)
    if html:
        parsed = parse_poedb(html, atlas_maps, known_cards)
        reference = last_good or seed
        if not parsed:
            log("PoEDB: no card lists parsed")
        elif not plausible(parsed, atlas_maps):
            log("PoEDB: parsed %d maps, which does not look right" % len(parsed))
        elif not force and not agrees_with(parsed, reference):
            log("PoEDB: parsed lists disagree with the last good lists; ignoring them "
                "(re-run the workflow with 'accept new card lists' if the cards really changed)")
        else:
            log("PoEDB: %d maps refreshed" % len(parsed))
            return parsed, "live", today

    if last_good:
        return last_good, "live", meta.get("updated")
    return seed, "snapshot", None


# --------------------------------------------------------------------------- #
# Everything else
# --------------------------------------------------------------------------- #

def art_name(card):
    """File name of a card's art on the game's image server: the name without spaces or punctuation."""
    return re.sub(r"[^A-Za-z0-9]", "", card)


_NOT_CHALLENGE = re.compile(r"^(standard|hardcore|hc|ssf|solo|ruthless|private|event)", re.I)


def pick_league(leagues, override):
    """The current temporary challenge league: the first softcore, non-SSF, non-Ruthless
    entry poe.ninja lists. An explicit override (workflow input or repo variable LEAGUE) wins."""
    ids = [entry.get("id") or entry.get("name") for entry in leagues if isinstance(entry, dict)]
    log("poe.ninja leagues: " + ", ".join(str(i) for i in ids[:12]))
    if override:
        log("league overridden to %s" % override)
        return override
    for entry in leagues:
        league_id = entry.get("id") or entry.get("name") or ""
        if league_id and not entry.get("hardcore") and not _NOT_CHALLENGE.match(league_id):
            return league_id
    return ids[0]


def build_orbs(payload):
    orbs = []
    for line in (payload or {}).get("lines") or []:
        spark = line.get("sparkLine") or {}
        orbs.append({
            "name": line["name"],
            "chaosValue": line.get("chaosValue"),
            "divineValue": line.get("divineValue"),
            "count": line.get("count"),
            "listingCount": line.get("listingCount"),
            "change": spark.get("totalChange"),
            "icon": line.get("icon"),
        })
    return orbs


def build_ratings(moe_maps):
    rated = []
    for entry in moe_maps:
        rating = entry.get("rating") or {}
        if entry.get("type") != "map" or entry.get("atlas") is not True:
            continue
        if any(rating.get(key) is None for key in ("layout", "density", "boss")):
            continue
        rated.append({
            "name": re.sub(r" Map$", "", entry["name"]),
            "layout": rating["layout"],
            "density": rating["density"],
            "boss": rating["boss"],
            "densityEstimated": "unreliable" in ((entry.get("info") or {}).get("density") or ""),
            "tags": [key for key, value in (entry.get("tags") or {}).items() if value is True],
        })
    return rated


# --------------------------------------------------------------------------- #
# Kingsmarch page: runegraft and tattoo prices plus port quota rewards
# --------------------------------------------------------------------------- #

# poe.ninja item types to try, in order, for each group. The first that returns lines wins.
KM_TYPES = {"runegraft": ["Runegraft", "Runegrafts"], "tattoo": ["Tattoo", "Tattoos"]}
UNIQUE_TYPES = ["UniqueArmour", "UniqueAccessory", "UniqueWeapon", "UniqueJewel", "UniqueFlask"]


def exchange_lines(payload):
    """Lines from the exchange endpoint: prices in chaos, names and images in the item lists."""
    items = {}
    for item in (payload.get("items") or []) + ((payload.get("core") or {}).get("items") or []):
        items[item.get("detailsId")] = item
        items[item.get("id")] = item
    out = []
    for line in payload.get("lines") or []:
        item = items.get(line.get("id")) or {}
        if not item.get("name") or line.get("primaryValue") is None:
            continue
        spark = line.get("sparkline") or line.get("sparkLine") or {}
        out.append({
            "name": item["name"],
            "chaosValue": line["primaryValue"],
            "divineValue": None,
            "count": None,          # the exchange has no listing counts
            "listingCount": None,
            "change": spark.get("totalChange"),
            "icon": urllib.parse.urljoin("https://poe.ninja/", item["image"]) if item.get("image") else None,
        })
    return out


def item_lines(fetcher, league, types):
    """Returns (type, lines) for the first item type poe.ninja answers with lines, trying the
    exchange endpoint and then the stash endpoint for each type name."""
    for item_type in types:
        for kind in ("exchange", "stash"):
            if kind == "exchange":
                url = "%s/exchange/current/overview?league=%s&type=%s" % (NINJA, urllib.parse.quote(league), item_type)
            else:
                url = "%s/stash/current/item/overview?league=%s&type=%s" % (NINJA, urllib.parse.quote(league), item_type)
            text = fetcher.get("km-%s-%s.json" % (kind, item_type), url, optional=True)
            try:
                payload = json.loads(text) if text else {}
                lines = exchange_lines(payload) if kind == "exchange" else build_orbs(payload)
            except (ValueError, KeyError, TypeError, AttributeError):
                lines = []
            if lines:
                return "%s/%s" % (kind, item_type), lines
    return None, []


POEDB_KINGSMARCH = "https://poedb.tw/us/Kingsmarch"


def parse_pools(html, port_names, known_items):
    """Each port's reward list: the known runegraft/tattoo names found after a port's name
    and before the next port's name. Names not in the poe.ninja list are ignored."""
    parser = _Tokens()
    parser.feed(html)
    parser.close()
    pools = {name: [] for name in port_names}
    current = None
    for _, text in parser.tokens:
        if text in pools:
            current = text
        elif current and text in known_items and text not in pools[current]:
            pools[current].append(text)
    return {name: items for name, items in pools.items() if items}


def choose_pools(fetcher, previous, static, known_items, today, force):
    """Returns (ports, source, updated). Live pools from PoEDB when they pass checks,
    else the last good live pools, else the bundled static ones."""
    meta = (previous or {}).get("pools") or {}
    last_good = {}
    if meta.get("source") == "live":
        last_good = {p["name"]: p["pool"] for p in (previous or {}).get("ports") or []}
    ports = static.get("ports") or []

    def apply(pools):
        return [dict(p, pool=pools.get(p["name"], p["pool"])) for p in ports]

    if last_good and meta.get("updated") == today:
        return apply(last_good), "live", today
    if known_items:
        html = fetcher.get("poedb-kingsmarch.html", POEDB_KINGSMARCH, optional=True)
        if html:
            names = [p["name"] for p in ports]
            parsed = parse_pools(html, names, known_items)
            # A runegraft port holds only runegrafts and a tattoo port only tattoos.
            for p in ports:
                if p["name"] in parsed:
                    parsed[p["name"]] = [n for n in parsed[p["name"]] if n.startswith(p["kind"] + " of")]
            parsed = {k: v for k, v in parsed.items() if len(v) >= 3}
            reference = last_good or {p["name"]: p["pool"] for p in ports}
            overlaps = [len(set(v) & set(reference[k])) / len(set(v) | set(reference[k]))
                        for k, v in parsed.items() if k in reference and reference[k]]
            log("Kingsmarch: PoEDB pools for %d of %d ports%s" % (
                len(parsed), len(ports),
                (", %.0f%% overlap with the last good pools" % (100 * sum(overlaps) / len(overlaps))) if overlaps else ""))
            if len(parsed) < len(ports):
                log("Kingsmarch: not every port found on PoEDB, keeping the last good pools")
            elif overlaps and sum(overlaps) / len(overlaps) < 0.5 and not force:
                log("Kingsmarch: PoEDB pools differ a lot from the last good ones; ignoring them "
                    "(re-run the workflow with 'accept new lists' if the rewards really changed)")
            else:
                return apply(parsed), "live", today
    if last_good:
        return apply(last_good), "live", meta.get("updated")
    return ports, "static", None


def build_kingsmarch(fetcher, league, static, fetched_at, previous=None, today=None, force=False):
    """Never raises: the Kingsmarch page is secondary, so any failure leaves it
    with its static tables and no prices rather than stopping the whole deploy."""
    data = dict(static)
    data.update({"league": league, "fetchedAt": fetched_at, "lines": [], "quota": []})
    try:
        for group, types in KM_TYPES.items():
            used, lines = item_lines(fetcher, league, types)
            log("Kingsmarch: %s prices: %s" % (group, ("%d from %s" % (len(lines), used)) if used else "none found"))
            data["lines"].extend(lines)

        # Chaos per divine, so prices can be shown in divines (exchange lines carry no divine value).
        _, currency = item_lines(fetcher, league, ["Currency"])
        for line in currency:
            if line["name"] == "Divine Orb":
                data["divineRate"] = line["chaosValue"]
        wanted = {q["name"] for q in static.get("quotaRewards") or []}
        found = {}
        for item_type in UNIQUE_TYPES:
            if len(found) == len(wanted):
                break
            _, lines = item_lines(fetcher, league, [item_type])  # uniques are on the stash endpoint
            for line in lines:
                if line["name"] in wanted:
                    best = found.get(line["name"])
                    if best is None or (line.get("listingCount") or 0) > (best.get("listingCount") or 0):
                        found[line["name"]] = line
        data["quota"] = list(found.values())
        known = {line["name"] for line in data["lines"]}
        data["ports"], source, updated = choose_pools(fetcher, previous, static, known, today or fetched_at[:10], force)
        data["pools"] = {"source": source, "updated": updated}
        pooled = {name for port in data["ports"] for name in port["pool"]}
        data["unpooled"] = sorted(known - pooled)
        if data["unpooled"]:
            log("Kingsmarch: %d priced runegrafts/tattoos are in no port pool" % len(data["unpooled"]))
        log("Kingsmarch: %d of %d quota rewards priced" % (len(found), len(wanted)))
    except Exception as exc:  # noqa: BLE001 - see docstring
        log("Kingsmarch: prices skipped (%s)" % exc)
    return data


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", default="_site/data.json")
    parser.add_argument("--seed", default="map-cards.json", help="bundled snapshot of map card lists")
    parser.add_argument("--km-static", default="kingsmarch-static.json", help="Kingsmarch ports, resources and thresholds")
    parser.add_argument("--km-out", help="where to write the Kingsmarch data (default: kingsmarch.json beside --out)")
    parser.add_argument("--previous", help="URL of the previously deployed data.json")
    parser.add_argument("--fixtures", help="read sources from this folder instead of the network (tests)")
    parser.add_argument("--league", help="override the league (tests)")
    parser.add_argument("--today", help="override today's UTC date, YYYY-MM-DD (tests)")
    args = parser.parse_args()

    repo = os.environ.get("GITHUB_REPOSITORY", "local")
    fetcher = Fetcher("to-scry-or-not-to-scry (https://github.com/%s)" % repo, args.fixtures)
    today = args.today or datetime.now(timezone.utc).strftime("%Y-%m-%d")
    force = os.environ.get("FORCE_CARD_LISTS", "").lower() in ("1", "true", "yes")

    # League: poe.ninja lists the current temporary challenge league first.
    leagues = json.loads(fetcher.get("leagues.json", NINJA + "/leagues"))
    league = pick_league(leagues, args.league or os.environ.get("LEAGUE", "").strip())

    # A league without Scrying Orbs yet is normal: publish an empty list, not an error.
    orbs_url = "%s/stash/current/item/overview?league=%s&type=ScryingOrb" % (NINJA, urllib.parse.quote(league))
    orbs_text = fetcher.get("orbs.json", orbs_url, tolerate=(400, 404))
    orbs = build_orbs(json.loads(orbs_text)) if orbs_text else []

    moe_maps = json.loads(fetcher.get("maps.json", MOE + "/maps.json"))
    moe_globals = json.loads(fetcher.get("globals.json", MOE + "/globals.json"))

    maps = build_ratings(moe_maps)
    if not maps:
        raise SystemExit("no rated Atlas maps in the Maps of Exile data")
    # Card prices come from poe.ninja: the current league, or Standard while the
    # league has too few cards priced (early in a league). Never from Maps of Exile.
    prices, images, price_league = {}, {}, None
    for candidate in dict.fromkeys([league, "Standard"]):
        # Divination cards live on the exchange endpoint: lines carry an id and a
        # price in chaos, and the top-level items list maps each id to its name (and image, when given).
        url = "%s/exchange/current/overview?league=%s&type=DivinationCard" % (NINJA, urllib.parse.quote(candidate))
        text = fetcher.get("divcards.json" if candidate == league else "divcards-standard.json",
                           url, optional=True)
        found, art = {}, {}
        try:
            payload = json.loads(text) if text else {}
            items = {}
            for i in (payload.get("items") or []) + ((payload.get("core") or {}).get("items") or []):
                items[i.get("detailsId")] = i
                items[i.get("id")] = i
            for line in payload.get("lines") or []:
                item = items.get(line.get("id")) or {}
                if item.get("name") and line.get("primaryValue") is not None:
                    key = norm(item["name"])
                    found[key] = line["primaryValue"]
                    if item.get("image"):
                        art[key] = urllib.parse.urljoin("https://poe.ninja/", item["image"])
        except (ValueError, AttributeError):
            pass
        if len(found) >= 100:
            prices, images, price_league = found, art, candidate
            break
        log("poe.ninja card prices for %s unavailable (%d cards)" % (candidate, len(found)))
    if not prices:
        raise SystemExit("no divination card prices from poe.ninja")

    previous = None
    if args.previous:
        text = fetcher.get("previous.json", args.previous, optional=True)
        try:
            previous = json.loads(text) if text else None
        except ValueError:
            previous = None

    with open(args.seed, encoding="utf-8") as handle:
        seed = json.load(handle)

    # Every card name Maps of Exile knows, so cards poe.ninja doesn't price still match.
    try:
        all_cards = json.loads(fetcher.get("cards.json", MOE + "/cards.json", optional=True) or "[]")
    except ValueError:
        all_cards = []
    lists, source, updated = choose_card_lists(
        fetcher, previous, seed, {m["name"] for m in maps},
        set(prices) | {norm(c["name"]) for c in all_cards}, today, force)
    # Stack size and reward text for the hover card, from Maps of Exile (optional).
    details = {}
    try:
        for card in all_cards:
            extra = {k: card[k] for k in ("stack", "reward") if card.get(k)}
            details[norm(card["name"])] = extra
    except (ValueError, KeyError, TypeError):
        details = {}
    map_cards = {name: [dict({"name": card, "price": prices.get(norm(card), 0), "art": art_name(card)}, **details.get(norm(card), {}))
                      for card in cards]
                 for name, cards in lists.items()}

    last_update = moe_globals.get("lastUpdate")
    ratings_updated = (datetime.fromtimestamp(last_update / 1000, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
                       if last_update else None)

    data = {
        "league": league,
        "fetchedAt": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "lines": orbs,
        "maps": maps,
        "mapCards": map_cards,
        "ratings": {"league": moe_globals.get("league"), "updated": ratings_updated},
        "cardLists": {"source": source, "updated": updated},
        "cardPrices": price_league,
    }

    os.makedirs(os.path.dirname(args.out) or ".", exist_ok=True)
    with open(args.out, "w", encoding="utf-8") as handle:
        json.dump(data, handle, separators=(",", ":"))

    if os.path.exists(args.km_static):
        with open(args.km_static, encoding="utf-8") as handle:
            km_static = json.load(handle)
        km_out = args.km_out or os.path.join(os.path.dirname(args.out) or ".", "kingsmarch.json")
        km_previous = None
        if args.previous:
            text = fetcher.get("previous-kingsmarch.json", args.previous.replace("data.json", "kingsmarch.json"), optional=True)
            try:
                km_previous = json.loads(text) if text else None
            except ValueError:
                km_previous = None
        with open(km_out, "w", encoding="utf-8") as handle:
            json.dump(build_kingsmarch(fetcher, league, km_static, data["fetchedAt"], km_previous, today, force),
                      handle, separators=(",", ":"))
    log("%s: %d orbs, %d rated maps, card lists for %d maps (%s%s)" % (
        league, len(orbs), len(maps), len(map_cards), source, ", " + updated if updated else ""))


if __name__ == "__main__":
    main()
