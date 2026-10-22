# Postcodes, held locally

Ordnance Survey's **Code-Point Open** gives every UK postcode a position: about 1.7 million of them,
under the Open Government Licence. World Assistant keeps a copy in Postgres, so a postcode can be turned
into coordinates (and the other way round) without asking anyone.

> Contains OS data © Crown copyright and database right 2026. Contains Royal Mail data © Royal Mail
> copyright and database right 2026. Contains National Statistics data © Crown copyright and database
> right 2026.

(The licence file inside the download is the authority; use its wording if it differs.)

## What changed

Adding a place by postcode (`PUT /v1/places/{id}`) used to send the postcode to postcodes.io. It now looks
in the local table first. postcodes.io is asked only when the local table does not know the postcode:

- the table has not loaded yet (a fresh install, until the first night's job), or
- the postcode is newer than the quarterly file.

Give coordinates instead of a postcode and nothing is looked up at all.

## How it is loaded

The worker checks every day at 03:10. When the table is empty or a month old it downloads the file
(about 25 MB), reads every data CSV in the zip, and upserts in batches. Rows from an earlier load are
deleted at the end, but only when the file held at least a million postcodes, so a truncated download can
add and update but never wipe. Postcodes the file marks as having no position are skipped.

By hand, from the download or a file you already have:

```
docker compose exec worker python -m app.integrations.codepoint.sync
docker compose exec worker python -m app.integrations.codepoint.sync --file codepo_gb.zip
```

`WA_CODEPOINT_POLL_ENABLED=false` switches the job off. `WA_CODEPOINT_URL` points at a different copy; if
Ordnance Survey ever asks for a key on the download, put it in that URL.

**Not yet verified against the live download.** The file layout (one headerless CSV per postcode area under
`Data/CSV`, ten columns) is from Ordnance Survey's published product description, and the tests use a file
built to that description. The first run is the real check; if it fails, the error says why.

## Positions

The file gives each postcode a grid reference (eastings and northings on the Ordnance Survey grid). Latitude,
longitude and the indexed point are all *generated* from that by PostGIS, so they cannot drift and the
loader writes only what the file says. The conversion is accurate to a few metres, far inside the size of a
postcode.

`quality` is the file's positional quality: 10 is a delivery point, larger numbers are coarser, and 90
(no position) is never stored.

## Asking

| Endpoint | Answers |
| --- | --- |
| `GET /v1/postcodes/{postcode}` | position, quality, and the ONS codes for country, district and ward |
| `GET /v1/postcodes/near?lat=&lon=&radius_km=` | the nearest postcodes to a point, with the distance |

The codes are ONS codes (for example `E09000033`); this project does not hold the names that go with them.
