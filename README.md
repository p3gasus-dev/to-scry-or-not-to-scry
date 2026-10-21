# To Scry or Not to Scry

Runs itself. Every 15 minutes a GitHub Action builds the page with fresh data and deploys it to Pages. Nothing is committed.

- League: first league poe.ninja lists (the current challenge league). Nothing to change per league.
- Orb prices: poe.ninja. A new league with no orbs priced yet shows a notice instead of an error.
- Card prices: poe.ninja divination cards for the current league. If the league has fewer than 100 cards priced (early in a league), it falls back to Standard prices and the page says so.
- Map ratings and which maps are on the Atlas: Maps of Exile. The page warns if its ratings are still from the previous league.
- Map card lists: PoEDB, refreshed daily and checked before use. If PoEDB changes or fails, the last good lists stay, then `map-cards.json` (a snapshot) is used. The page says when it is on the snapshot.

Setup: Settings > Pages > Source: GitHub Actions. Then run "Update scrying orb prices" once.

GitHub turns off scheduled workflows after 60 days without repository activity. If the page stops updating, open Actions and click Enable workflow, or run `gh workflow enable`.

Test the build offline: `python3 scripts/build_data.py --fixtures <folder>`.
