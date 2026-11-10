# uk-bus-skill

![licence](https://img.shields.io/badge/licence-MIT-16A6F0) ![python](https://img.shields.io/badge/python-3.10%2B-3776AB?logo=python&logoColor=white) ![deps](https://img.shields.io/badge/dependencies-none-2ea44f)

**An agent skill that answers "where is my bus?" for any route in England, from the government's live bus feed.**

Every bus in England reports its position to the DfT Bus Open Data Service (BODS) about every 30 seconds. This skill gives an agent (Claude Code, or anything that reads `SKILL.md`) one script to query it: which buses are on a route, where they are, and how fresh that position is. For West Yorkshire it can also say how late a route usually runs, using the measurements from [is-my-bus-late](https://github.com/damonchandio/is-my-bus-late).

```
$ python scripts/bods_live.py --line 72 --operator FBRA
line  operator  destination           aimed_departure  position_age_min  distance_km  vehicle
----  --------  --------------------  ---------------  ----------------  -----------  ----------
72    FBRA      Little Germany W      00:15            0.8                            FBRA-35653
72    FBRA      Eastgate              00:15            1.8                            FBRA-35647
72    FBRA      Little Germany W      23:49            21.9                           FBRA-35649
```

## Install

As a Claude Code skill:

```bash
git clone https://github.com/damonchandio/uk-bus-skill ~/.claude/skills/uk-bus-skill
python ~/.claude/skills/uk-bus-skill/scripts/bods_live.py --selftest
```

Or just run the script. No packages to install and no API key needed: Python 3.10+ standard library only.

Optional: set `BODS_API_KEY` (free from https://data.bus-data.dft.gov.uk/) and the server does the filtering, which is quicker. Without a key the script downloads the open bulk feed (every bus in England, about 3 MB zipped) and filters it itself, in about two seconds.

## Usage

```bash
python scripts/bods_live.py --line 72 --operator FBRA              # every bus on a route
python scripts/bods_live.py --near 53.7950,-1.7520 --radius 1      # every bus within 1 km
python scripts/bods_live.py --line 72 --near 53.7950,-1.7520       # a route near you, nearest first
python scripts/bods_live.py --late 72                              # West Yorkshire: how late does it usually run?
python scripts/bods_live.py --operator FBRA --json                 # JSON rows
```

At least one of `--line`, `--operator` or `--near` is required: the unfiltered feed is every bus in England, around 27,000 rows.

[SKILL.md](SKILL.md) explains the columns, including how to read `position_age_min` and why the number on the bus (`PublishedLineName`, `72`) can differ from the feed's `LineRef` (`072`).

## How it works

- With a key, calls the BODS SIRI-VM datafeed (`/api/v1/datafeed/`) with `lineRef`, `operatorRef` and a `boundingBox` built from `--near` and `--radius`, so the server does most of the filtering. Without one, reads the same SIRI-VM data from the open bulk archive.
- Parses `VehicleActivity` records with `xml.etree`, matches the line on either `PublishedLineName` or `LineRef` with leading zeros ignored, and retries without `lineRef` when the server's exact match finds nothing.
- `--late` downloads is-my-bus-late's hourly per-route counts (one tarball) and aggregates them the same way its `pipeline/report.py` does.
- `--selftest` runs offline against a bundled SIRI-VM sample and a small set of lateness runs.

## Licence

Code MIT. Data from the [Bus Open Data Service](https://data.bus-data.dft.gov.uk/), Open Government Licence v3.0.

Built by Damon Chandio, [Damon Dynamics](https://damondynamics.co.uk), Bradford.
