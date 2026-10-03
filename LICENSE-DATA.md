# Data licences in this repository

The MIT licence in `LICENSE` covers the code. It does not cover the data files below.

## spots-osm.csv is under the Open Database Licence

`spots-osm.csv` holds swim spots taken from OpenStreetMap. It is a database derived from
OpenStreetMap, © OpenStreetMap contributors, and it is made available under the
[Open Database Licence 1.0](https://opendatacommons.org/licenses/odbl/1-0/) (ODbL). See
<https://www.openstreetmap.org/copyright>.

The same applies to `data/raw/osm_swim_candidates.csv`, the list of OpenStreetMap elements the
spots were chosen from, which `scripts/osm_spot_candidates.py` writes.

If you use, share or adapt either file, the ODbL asks you to credit OpenStreetMap's contributors,
keep the licence with it, and offer any database you make from it under the ODbL too.

## spots.csv is not under the ODbL

`spots.csv` is SwimSignal's own list: the 38 Environment Agency designated inland bathing waters
(their names and positions are Environment Agency data, Open Government Licence v3.0) and river
and lake spots picked by hand. It is not under the ODbL, and it must not take any OpenStreetMap
data.

The two files are kept apart on purpose. A file that mixed OpenStreetMap rows with ours would be a
derivative of OpenStreetMap, and the whole file would have to be offered under the ODbL. So:

- Never copy a row, a name or a position from OpenStreetMap into `spots.csv`. It goes in
  `spots-osm.csv`.
- Ids in `spots-osm.csv` start with `osm-`, so they cannot clash with ids in `spots.csv`.
- Neither file has a column that refers to the other.

The build reads both and credits OpenStreetMap on the terms page and in the `credits` field of
`data/spots.json`, where each spot from `spots-osm.csv` carries `"source": "openstreetmap"`.

## Other data

The other data sources, their licences and the credits they ask for are listed on the site's terms
page ("Data sources and credits") and in the README's Data section.
