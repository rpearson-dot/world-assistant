"""Energy supply: what electricity costs now and later, for Home Assistant to act on.

A domain module, in the shape new categories should follow: one package holding its own client for the
outside service, its pure logic (no network, no database, so it can be tested alone), its store, its worker
job, its API routes and the Home Assistant sensors it publishes, with one switch that turns the lot off.

World Assistant only *reports* here. Deciding when the battery charges or discharges, and driving the
inverter, is Home Assistant's job; nothing in this package talks to an inverter.
"""
