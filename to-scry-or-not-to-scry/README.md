# To Scry or Not to Scry

Ranks Path of Exile scrying orbs by price and the best maps to scry onto. A GitHub Action rebuilds the page every 15 minutes and deploys it to GitHub Pages. Nothing is committed.

## How it works

- League: the first league poe.ninja lists, so it follows the current challenge league on its own.
- Orb prices: poe.ninja. A league with no orbs priced yet shows a notice instead of an error.
- Card prices: poe.ninja divination cards for the current league. With fewer than 100 cards priced (early in a league) it falls back to Standard prices and the page says so.
- Map ratings and which maps are on the Atlas: Maps of Exile. The page warns if its ratings are still from the previous league.
- Map card lists: PoEDB, refreshed daily and checked before use. If PoEDB changes or fails, the last good lists stay, then `map-cards.json` (a snapshot) is used. The page says when it is on the snapshot.
- Map score: layout × 3, density × 2, boss × 1 and cheap cards × 2, out of 100.

## Sources

- [poe.ninja](https://poe.ninja) ([API docs](https://poe.ninja/docs/api))
- [PoEDB](https://poedb.tw/us/Atlas_of_Worlds)
- [Maps of Exile](https://mapsofexile.com) ([data](https://github.com/deathbeam/maps-of-exile), MIT), built on community sheets by FixFaxer and not_Shorex
