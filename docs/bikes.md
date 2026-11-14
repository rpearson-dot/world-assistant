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
- **Which bike:** choose a bike for commutes, for each kind of Garmin ride (Gravel Cycling, Indoor Cycling, ...) and
  for any other. A ride Garmin marks as a commute (its type, or its event type) goes to the Commute bike first.
  Recent rides are still asked about, as before; rides more than four weeks old are not.
- **Weight:** your latest Garmin weight sets the tyre pressures (switch it off on the card).
- **Each new ride's file** (rides from the last four weeks, up to 30 a sync): its route, as points about a kilometre
  apart (for the rain along it), and the batteries its devices reported at the end of the ride. An SRAM AXS
  derailleur's battery status (new, good, ok, low, critical) and voltage go onto the AXS battery fitted to that bike:
  "low" or "critical" makes it due to charge. With two AXS batteries on a bike (a 2x bike's front and rear
  derailleurs), put the derailleur's serial (shown on the bike's card) in each battery's details.
- **The key runs out:** a warning on the card a fortnight before; Garmin also emails a week before. A token that stops
  early tells the page the key's end date, if you did not give it.
- **The probe:** `python -m app.modules.bikes.garmin [RIDER]` prints what your key gives: how far back the rides go, the fields
  of a ride and its details (looking for gear, sensors and batteries), whether Garmin's gear can be read, the original
  file's GPS points, devices and developer fields (where tyre pressure sensors write), and weight. It only reads. It is
  part of the live check.

## Workshop
At the top of the Bikes page: your bikes in a row (each with its front and rear wheel, and an odometer for the bike and
each wheel), and shelves for spares, kit and wheels in storage. Drag with a mouse or a finger:
- a part onto a wheel or the bike's frame: fitted, if it can go there (below); a spare dropped where there is one
  already replaces it (logged as the replacement, its count starting again);
- a wheel onto a bike: it goes on, and the bike's wheel at that end goes into storage. **Build a wheel** (on the storage
  shelf) makes a new one there, empty or with a bike's usual wheel parts: drag its parts onto it, then drag it onto a bike;
- anything onto a shelf: it comes off (spares onto the spares shelf, kit into your kit).
Tap a part or wheel to see its odometer and wear. Services and checks are not shown there: they stay on each bike's card.

**What can go where** (refused, with the reason): a cassette or hub gear only on a rear wheel; wheel parts on wheels and
bike parts on bikes; a rotor or disc pads only with a disc brake at that end, rim pads with a rim brake, sealant on a
tubeless tyre and a tube on a tubed one; a chain or belt as the drive is, gear cables, coin cells or a Di2 battery as
the shifting is; suspension services as the bike has; an AXS battery only on a bike with AXS shifting or a wireless
dropper; and never a second tyre, cassette, chain and so on where there is one already. A wheel whose parts do not suit
the bike it goes onto is fitted, with a warning. If a refusal is wrong, the bike's answers are: change them with **Its
answers**.

**Pictures:** each bike is drawn from typical frame geometry for its type, facing right so the drivetrain shows. In
**Its answers**, choose its Model (where listed: its own geometry) and its frame and second colour.

## Wheels
- **A new wheel:** on the bike, **Add a wheel**: front or rear, its name, its wheelset group (wheels in the same group
  swap together, e.g. "Winter wheels"), since when, whether to fit it now (the bike's current one goes into storage)
  and whether it comes with the usual parts for that bike (tyre, tube or sealant, tyre check, rotor, cassette, hub,
  from the bike's answers). Without them, add its parts one by one with **Add a part** on the wheel.
- **Moving a part between wheels** (the cassette onto the new wheel): the part's **Move to…** lists every wheel, on a
  bike or in storage.
- **Which wheels a ride was on** comes from when each wheel was put on: a ride counts against the wheels on its bike at
  the time (Recent rides shows them). To put rides on other wheels, move the wheel (or the whole group) with a
  **from** date: rides from that date until the next move count on it, and every part's wear is worked out again.
- **Wheels in storage** are listed with their parts, which keep their wear for when they go back on.

## Many rides at once
- **Rides to confirm** says how many there are, and has **Mark every unanswered ride dry** (leaving, if ticked, the
  ones the rain gauge says were wet for you to answer) and **Keep every bike as it is**.
- **Rides without a bike** lists the kinds of ride among them, with how many of each (Garmin's kinds, from the sync or
  its CSV: "Road Cycling" in the CSV is the same kind as the sync's Road Biking). Choose a bike for each and **Give
  them these bikes**: every ride of that kind without a bike gets it, taken as confirmed, and the choice is kept as the
  rule for new Garmin rides.

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

## The Garage
The Garage (`/bikes/garage`, linked from the top of the Bikes page) is the garage as a game's tuning screen. Choose a
bike along the top and only that bike shows, with a marker on its drawing where each part is (chain, cassette,
derailleur, crank, brakes, tyres, rims, hubs, bars, saddle, fork, shock), ringed green to amber to red by the most
worn part there; a due one pulses. The spares shelf, the bin and the shop counter stay along the bottom of the screen;
each spare is a tile filled from the bottom in the same green to red.

- **Tap a marker** for its parts, then one for its card: what it is, how worn (km, hours or days of its life), the last
  thing done to it, and buttons. **Replace** opens what could take its place, worked out for that bike: the spares on
  the shelf (those that fit first, with anything that would not go with the rest of the bike), and things to buy (the
  same again, the bike's groupset's, others with the same speeds and freehub; pads for the calliper on the bike).
  **Fit this** uses a spare, **Fit new** a new one of that make and model, **Buy** puts it in the shop basket.
- **Drag** a spare from the shelf onto a marker to fit it in place of the part of its kind there, or a part or spare
  onto the bin to bin it (retired: kept in the history and costs).
- **The shop counter** shows the basket and what you might need: spares below the number you keep, and the first thing
  to buy for each worn part without a spare on the shelf.
- **The service list** collects every job (replace, service, bin, buy) and does them all with **Do them all**; anything
  that could not be done stays on the list with why. Bought things go onto the Outdoors list; nothing is ordered or
  paid for. The list is kept in that browser until it is done.

## Sensors
Your Garmin sensors with batteries (speed, cadence, heart rate strap, power meter, radar, lights) are added to the
**Sensors** section the first time a ride's file shows them, one for each serial number. The files of your other
recent activities (a run with the heart rate strap) are read too, for their sensors only (up to 10 a sync). Each shows
the battery status and voltage it last reported, after which activity and on which bike; low or critical makes it
due. Log **New battery** when you change it (a reading from before then no longer counts); a year without one also
makes it due. Edit one to name it ("Reiver speed sensor") or say its battery (CR2032). AXS derailleurs are not here:
their battery is the labelled AXS battery on the bike.

Battery levels (sensors, AXS batteries, shifter coin cells, Di2) show as "--" until Garmin has reported one, then
its status and level ("good (80%)"). A new battery goes back to "--" until its first reading.

**Ready to ride:** each bike has a battery summary under its name on the Bikes page and in the Garage. It lists the
derailleur (AXS) and shifter batteries, the sensors last seen on that bike (speed, cadence, power, radar), and your
heart rate strap, which is on every bike's list because you wear it. A battery at 20% or under, or reported low,
makes it "Charge or replace first". The strap alone never makes a bike "ready".

## Rain and rough riding
- **Wet or dry** is suggested from the Environment Agency rain gauges along a Garmin ride's route (the nearest gauge to
  points about 12 km apart), over the ride and the three hours before it: wet if any had as much rain as a wet ride.
  Rides without a route use the gauge nearest the start, as before.
- **Gravel and mountain-bike rides** wear the chain, cassette, chainrings, jockey wheels, pads and tyres faster:
  Garmin's Gravel Cycling (and cyclocross) counts 1.3 times, Mountain Biking (and enduro, downhill, e-MTB) 1.6
  times, on top of the wet factor when it was also wet. A plain "Cycling" ride on a mountain bike counts as mountain
  biking. Both factors are in Settings.

## Groupsets and what goes together
- **Groupset** (on a bike's card): choose the groupset it has (Shimano Sora to Ultegra Di2, GRX, Deore to XT including
  2005's XT M760, SRAM Force 10-speed from about 2010, Apex, Rival and Force AXS including the 2025 13-speed Rival
  AXS E1 XPLR, NX and GX Eagle). The bike's answers are set to match (shifting, one or two rings, speeds, drop or flat
  bars, a SRAM hydraulic brake's DOT fluid), which adds or takes off parts (a front derailleur for two rings), then
  each part gets its make, model and details: levers or shifters and brake levers, derailleurs, crankset, cassette
  (with its freehub), chain, callipers, and for some builds the rotors. Bikes often come with a mix, so change any
  part afterwards; "only fill in parts with no make or model yet" keeps what you have said.
- **The parts** are on every bike now: shift and brake levers (drop bars) or shifters and brake levers (flat bars),
  rear and front derailleurs, crankset and brake callipers. They have no life of their own (replace them when they
  break or are upgraded) and are there for their makes and models.
- **What goes together** is checked on each bike and shown on its card: a cassette and chain of different speeds, either
  against the bike's speeds; a cassette whose freehub (HG, XD, XDR, Microspline) does not fit the rear hub's; a
  Centre Lock rotor on a 6-bolt hub; a rotor size other than the brake is set up for; mineral oil in a DOT brake;
  a tyre wider than the frame takes, or for another wheel size. Each needs both details known. The number answers
  (speeds, widest tyre, rotor sizes) and wheel size are in Its answers.
- **Remove** takes a part the bike does not have off for good (a dropper on a bike with a fixed seatpost changes its
  answers to "no dropper").

## Measuring a chain
**Measure** on a chain (or a tandem's timing chain): the chain checker's reading. From the readings, a straight line
from new says how fast it is stretching and the km it will be worn out at (0.5% for 11-speed and up, 0.75% for 10 and
below), which becomes the chain's life instead of the typical 3,500 km. A reading at the limit makes it due.

## Checklists
**Pre-ride check** and **Service check** on each bike's card, made from its answers: AXS batteries charged on an AXS
bike, sag on a suspension bike, the timing chain's tension on a tandem, sealant on a tubeless one, and so on; add items
of your own. Tick them off and press Done: it is logged (with what was not done), the card shows when each was last
done, and the items that are the garage's own checks (tyre pressures, the hanger, the spokes, the timing chain)
restart those parts' counts.

## Records and recalls
- **Records** (on each bike's card): maker, frame number, when and where it was bought and for how much, the receipt
  or order number, insurer and policy number, BikeRegister ID, other marks, and a photo of the frame number (kept on
  your server). A link goes to BikeRegister, the UK's police-approved register, to register it or check a bike.
- **Safety recalls**: once a week (and with "Check for safety recalls now" in Settings), GOV.UK's product safety
  alerts, reports and recalls (from the Office for Product Safety and Standards) are searched for the makers of your
  bikes and parts. Notices from the last three years that name a maker and are about bikes are shown at the top of
  the page; "Not mine" hides one. A match is the maker, not necessarily your model: read the notice.

## Spares to keep
In Settings, say how many of each kind to keep on the shelf (two chains, four pairs of pads). When the shelf has
fewer, it shows under Due and (with "automatically") goes onto the Outdoors list once, named as the last one you had.

## Routes you ride

The Routes you ride card lists the routes you ride again and again. They are found from your rides' GPS routes: two
rides are the same route when they start and finish within 400 m of each other (either way round) and run within
250 m of each other on average. Only rides from the last 5 years count, and a route is offered only once it has been
ridden more than 5 times. Change either number in Settings.

**Watch** keeps the route's corridor (150 m either side), so road works and closures along it are found as they are
for a trip. This needs the National Highways module. **Stop watching** removes the corridor. The bike garage is never
linked to Home Assistant, and watching a route doesn't change that.

`GET /v1/bikes/routes?rider=`, `POST`/`DELETE /v1/bikes/routes/{id}/watch?rider=`.

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
- **Today's riding**: the pressures on a bike's card are for its usual riding; choose road, gravel or wet today for
  that day's (gravel about 15% lower than road, wet about 7% lower than usual, never over the tyre's maximum). The
  choice is remembered on that device.

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
`bike_ride` (log a ride), `tyre_pressure` (also for today's road, gravel or wet riding). `bike_service` also logs a
chain checker's reading ("the Reiver's chain measures 0.3%").

Not sent to Home Assistant.

## Defaults
Every life, threshold and answer is a starting point and can be changed. The defaults are in `app/modules/bikes/defaults.py`:
chain 3,500 km (measure its stretch), cassette 10,000, chainrings 20,000, disc pads 2,000 (check every 500), rotor
8,000 (to its minimum thickness), tyre 4,500, sealant 90 days, tyre check weekly (latex: daily), hub service 8,000 km
or a year, bottom bracket 15,000 (press-fit 10,000), brake bleed yearly, RockShox fork lowers 50 h and damper 200 h,
shock air can 50 h and damper 200 h, dropper 50 h and 200 h, helmet 5 years, AXS battery 20 h until it has learned
its own.
