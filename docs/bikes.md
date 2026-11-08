# Bike garage

Every bike's parts, and how worn they are from your rides: what is due, tyre pressures, AXS batteries, spares on the
shelf, and running cost. The **Bikes** page (`/bikes`), and the assistant.

Design and reasoning: the project's `bike-garage-design.md`. Stage 2 has begun with rides from Garmin Connect's
Personal API (below). Still to come: the true-up after the first sync and Garmin's gear (once the probe shows what the
API gives), each ride's GPS track for rain along the route and saved routes, and safety recall checks.

## Getting started
1. **Add yourself as a rider** (top of the page). Your weight sets the suggested tyre pressures. Anyone can add
   themselves; there are no logins between riders.
2. **Add a bike**: a name, its type (road, gravel, cyclocross, hardtail or full-suspension MTB, hybrid, time trial or
   tandem; any of them an e-bike), and when its current parts were fitted. Open "Its answers" to change any default:
   drive, shifting (mechanical, AXS, Di2), **front and rear brake separately** (hydraulic disc, cable-pull disc,
   cable-actuated hydraulic, rim, V-brake, drum), front and rear suspension separately, dropper, front and rear tyre
   (tubed, latex, tubeless) and width, bottom bracket and pedals.
3. The bike gets the parts its answers give it, each with a default life. Its wheels are two labelled sets
   ("Gravel front wheel", "Gravel rear wheel") carrying their tyre, rotor, cassette, hub, sealant and tyre check.
4. **Add rides**: from Garmin Connect with your key (see "Garmin Connect"), by hand (the bike you choose is taken as
   right), or from Garmin Connect's CSV (on the web: Activities, then Export CSV), choosing the file on the page. Rides already imported are skipped. Rides without a bike are listed to choose one.

## Garmin Connect
- **Your key:** in Garmin Connect on the web, Settings, then Personal API Key; choose how long it lasts (30 days to a
  year). Paste it in with the rider (top of the Bikes page: when adding the rider, or under "This rider's Garmin key"), with the date it runs out. Garmin checks it before it
  is kept (a refused key is not saved); it goes in the keys folder as `garmin_<rider>.txt` and is never shown again.
  Each rider has their own. You can also put the file there yourself.
- **The sync:** every morning at 06:50, and with Sync now. The first reads your whole history; later ones look back a
  month, so a ride edited in Garmin is updated here and one deleted there is taken off. Only rides (cycling of any
  kind, indoor and virtual too) are taken.
- **Rides you imported from the CSV** are matched (start within five minutes, distance within 2%) and become the
  Garmin ride, keeping the bike and the wet answer you gave.
- **Which bike:** choose a bike for each kind of Garmin ride (Gravel Cycling, Indoor Cycling, ...) and for any other.
  Recent rides are still asked about, as before; rides more than four weeks old are not.
- **Weight:** your latest Garmin weight sets the tyre pressures (switch it off on the card).
- **The key runs out:** a warning on the card a fortnight before; Garmin also emails a week before. A token that stops
  early tells the page the key's end date, if you did not give it.
- **The probe:** `python -m app.bikes.garmin [RIDER]` prints what your key gives: how far back the rides go, the fields
  of a ride and its details (looking for gear, sensors and batteries), whether Garmin's gear can be read, the original
  file's GPS points, devices and developer fields (where tyre pressure sensors write), and weight. It only reads. It is
  part of the live check.

## What it shows
- **Due**: parts at 90% of their life or close to its end (within 300 km, 10 hours or a fortnight, for lives well over
  twice that), first the things to replace, then services and checks. "Coming up" from 75%.
  A spare on the shelf is offered first. Due parts go onto the **Outdoors** list in KitchenOwl automatically (once
  each, only things you buy, and only with no spare on the shelf), or when you tap, or never (Settings).
- **Rides to confirm**: which bike each imported ride was on (Garmin keeps the last bike chosen, so it is often wrong):
  a ride whose activity does not fit its bike ("Mountain Biking" on the road bike, an e-bike ride on a bike without a
  motor) is flagged, with "It was ..." when one other bike fits. Changing the bike moves the ride's wear to the right
  parts. Garmin's API cannot change a ride's bike, so a Garmin ride links to itself in Garmin Connect to change it
  there too, keeping Garmin's mileage right.
- And whether each outdoor ride was wet. The suggestion comes from the nearest Environment Agency
  rain gauge (within 15 km of home for now): 1 mm or more during the ride and the three hours before it. The gauges
  keep about four weeks; older rides have no suggestion. A wet ride counts 1.5 times (Settings) for the chain, lube,
  cassette, chainrings, jockey wheels and brake pads.
- **Each bike**: its picture (your photo, else a drawing of its type), tyre pressures front and rear (suggested from
  width, tubeless, the kind of riding and weight, or the one you set), its fitted AXS battery, and every part with a
  bar of its life used. Each wheel can be renamed, given a group ("Winter wheels") and moved: onto another bike, off
  into storage, or with its whole group (the wheels it replaces come off).
- **Shelf and kit**: spares, with how many; and your own kit (helmet, shoes, cleats, labelled AXS batteries).
- **Running cost**: per bike this year and in total, the cost per km, and each kind of part's cost per 1,000 km once
  some have been used up. A spare's price counts when it is fitted, not when it is bought.

## Doing things
- **Replaced** (or Serviced, Checked, Pumped, Lubed, Topped up, Bled, Charged, depending on the part): logs it and
  starts its count again. A replacement retires the old part (its history stays) and fits a new one in the same place,
  from the shelf if you choose the spare.
- **Replaced**: with a spare from the shelf, **the same again** (its brand, model and details carried over, with the
  price asked), or **something different** (a Hope rotor in place of a SRAM one, a 42t chainring in place of the 40t:
  its brand, model and details). The part's **History** says what changed ("SRAM X-Sync 40t → Wolf Tooth 42t").
- **Edit** a part: its label ("Chain A"), brand, model, its model details (teeth, speeds, range, diameter and mount,
  size, compound, standard: whatever its kind has), price, product page (and "Look up" to read its name and
  picture from the page), its life in km, hours or days, the distance it had done before it was added, and a photo
  (taken on the phone or chosen; kept small, on your server). Tyres also take their own pressure, maximum and width.
- **AXS batteries**: add each one to your kit with its label (Battery A, Battery B). Log "Charged" with the level it
  was at; after a few charges each battery's own hours per charge are used, and after six it is compared with its
  first charges: one giving 20% fewer hours is flagged as wearing.

## Undo, delete and retire
- **Recent work** lists what was logged, newest first, each with **Undo**: the part's count goes back to what it was.
  Undoing a replacement puts the old part back where it was, with its wear, and takes the new one away (a spare goes
  back on the shelf). If the new part has had things logged since, undo those first.
- **Recent rides** (under Rides): delete one logged twice; its wear comes off the parts. An imported ride comes back
  if the same file is imported again.
- **Delete** a part (in its Edit) or a bike (with no rides) added by mistake. A used part is replaced instead, and a
  bike with rides is **retired** (sold, stolen, written off), which keeps everything.
- **Retired bikes**: each with its distance, rides, dates, cost (in total and per km), the parts and wheels still on
  it, and its history. Its parts are not due. **Bring it back** to ride it again.

## Assistant
`bikes` (the garage), `bike_due`, `bike_service` ("charged Battery A at 30%", "replaced the front pads"),
`bike_ride` (log a ride), `tyre_pressure`.

Not sent to Home Assistant.

## Defaults
Every life, threshold and answer is a starting point and can be changed. The defaults are in `app/bikes/defaults.py`:
chain 3,500 km (measure its stretch), cassette 10,000, chainrings 20,000, disc pads 2,000 (check every 500), rotor
8,000 (to its minimum thickness), tyre 4,500, sealant 90 days, tyre check weekly (latex: daily), hub service 8,000 km
or a year, bottom bracket 15,000 (press-fit 10,000), brake bleed yearly, RockShox fork lowers 50 h and damper 200 h,
shock air can 50 h and damper 200 h, dropper 50 h and 200 h, helmet 5 years, AXS battery 20 h until it has learned
its own.
