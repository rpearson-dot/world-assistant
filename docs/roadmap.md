# Roadmap

Ideas parked for later. None of these are built. What is built is in the [README](../README.md) and the
[changelog](../CHANGELOG.md).

* **Ticketmaster Inventory Status (live ticket availability):** says whether an event has tickets, few left,
  or none (`TICKETS_AVAILABLE`, `FEW_TICKETS_LEFT`, `TICKETS_NOT_AVAILABLE`), near real time, for up to ~350
  events per request. Not open to a normal key: access is for authorised clients by email request
  (devportalinquiry@ticketmaster.com) and is under the Partner API terms, so a personal project may not
  qualify. The Discovery API we already use gives only the sale status and sale window, not seats left.
  If access is ever granted: a separate key secret, one batched call per search for the Ticketmaster
  events on the page, never mixing sources or regions in one batch.
* **More event sources:** many local councils and venues publish no event feed, so local events there would
  have to be entered by hand. Skiddle and Ticketmaster are built (see docs/data-sources.md).
* **Live traffic journey times:** needs a traffic-aware routing provider (TomTom, HERE or similar);
  check its terms on traffic-aware routing and on storing results first.
* **Fill-up and efficiency log, and insurance reminders.** (Tax and MOT are built: the Vehicles page, from DVLA
  and DVSA.)
* **Dover to Calais crossings (ferries and Eurotunnel):** parked (Oct 2026). Looked for feeds and found none
  offered: Port of Dover, P&O, DFDS and Irish Ferries publish only web pages and social posts for people, no
  API or open data. Eurotunnel's own website board (`.../api/v1/departures/GetCombinedBoardResults`) is
  undocumented and token-protected; terms unchecked. Revisit if an operator offers a feed, or by asking
  Port of Dover and Eurotunnel whether they share data with personal projects.
* **Modules as clean, separable parts (agreed, not started):**
  * each module in its own folder (code, pages, tests, docs);
  * a CI check that the core never imports a module, and that a module uses only the core's published interface
    and the modules it declares;
  * that interface written down as a small "module SDK" (places, keys, notifications, Home Assistant sensors,
    pages, jobs, assistant tools);
  * new installs starting from the essentials, with the setup wizard asking which areas you want.

  The bike garage is the pilot. Separate repositories, and installing signed outside modules, wait until a module
  needs them ([contributing a module](contributing-a-module.md)).
