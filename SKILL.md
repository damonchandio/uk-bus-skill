---
name: uk-bus-live
description: Live bus positions for any route in England from the DfT Bus Open Data Service (SIRI-VM). Use when someone asks where their bus is, whether a bus is coming, which buses are near a place, or how late a West Yorkshire route usually runs.
---

# Where is my bus? (England, live)

Use this skill when the user asks:
- "Where is the 72?" / "Is my bus coming?" for a route in England
- "What buses are near <place> right now?"
- "Is the 72 usually late?" (West Yorkshire only, uses is-my-bus-late's hourly measurements)

Do not use it for timetables, journey planning, fares or trains. It shows where buses are now, not when they are due.

## Setup

Python 3.10+ standard library only. No API key needed: without one the script downloads the open bulk feed (about 3 MB) and filters it locally. If `BODS_API_KEY` is set (free, from https://data.bus-data.dft.gov.uk/) the server filters instead, which is quicker.

Check the install offline: `python scripts/bods_live.py --selftest`

## Commands

Always pass at least one filter. The unfiltered feed is every bus in England (around 27,000 rows).

| Question | Command |
|---|---|
| Where is route X? | `python scripts/bods_live.py --line 72 --operator FBRA` |
| Buses near a point | `python scripts/bods_live.py --near 53.7950,-1.7520 --radius 1` |
| Route X near a point | `python scripts/bods_live.py --line 72 --near 53.7950,-1.7520 --radius 2` |
| Everything one operator runs | `python scripts/bods_live.py --operator FBRA` |
| Is route X usually late? (West Yorkshire) | `python scripts/bods_live.py --late 72` |

Add `--json` to any command for machine-readable rows.

The operator code is the National Operator Code (NOC), e.g. `FBRA` First Bradford, `FLDS` First Leeds, `YTSY` Yorkshire Tiger, `ARBB` Arriva Yorkshire. If you don't know it, search by `--near` first and read the `operator` column. Line numbers repeat across England, so add `--operator` or `--near` whenever you can.

## Reading the rows

| Column | Meaning |
|---|---|
| line | route as printed on the bus (PublishedLineName) |
| operator | NOC of the bus company |
| destination | where this trip ends |
| aimed_departure | when this trip was timetabled to leave its first stop (local time), which identifies the trip |
| position_age_min | minutes since the bus last reported its position |
| distance_km | straight-line distance from `--near`; empty without it |
| vehicle | fleet number, useful to follow one bus across calls |

**position_age_min matters.** Buses report about every 30 seconds.
- Under 2: the position is current.
- 2 to 10: treat it as approximate. The bus has moved on since.
- Over 10: stale. The bus may be out of service, at the depot, or its tracker has dropped. Don't tell the user "your bus is here" from a stale row.

**PublishedLineName vs LineRef.** The number on the bus (`PublishedLineName`, e.g. `72`) can differ from the feed's internal `LineRef` (e.g. `072`). The script matches either and ignores leading zeros, and if the server's exact `lineRef` filter returns nothing it retries without it and filters locally. So always pass the number the user sees on the bus.

**Lateness (`--late`).** Comes from https://github.com/damonchandio/is-my-bus-late, which measures West Yorkshire buses at stops every hour. `typical_minutes_late` is the observation-weighted median delay; "on time" means 1 minute early to 5 minutes late. `ranked: false` means fewer than 30 measured trips, so say the figure is based on little data.
