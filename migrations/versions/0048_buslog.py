"""Bus route recorder (the 213): every request to TfL and BODS in the morning window, what each said, kept only ever
added to. Tables are split by month (declarative partitions; buslog.partitions makes the coming months'); the raw
responses (buslog_raw) are kept for a school year, everything else for good.

Revision ID: 0048
Revises: 0047
Create Date: 2026-11-13
"""

from alembic import op

revision = "0048"
down_revision = "0047"
branch_labels = None
depends_on = None

TABLES = ("buslog_poll", "buslog_raw", "buslog_prediction", "buslog_position", "buslog_status", "buslog_timetable",
          "buslog_route")


def upgrade() -> None:
    # Every request made: when, to what, how it went, how many things it returned. A request that failed or came back
    # empty is a row too, so a gap in the data can always be told from a bus that did not come.
    op.execute("""
        CREATE TABLE buslog_poll (
          id bigint GENERATED ALWAYS AS IDENTITY, polled_at timestamptz NOT NULL, service_day date NOT NULL,
          line text NOT NULL, source text NOT NULL, url text NOT NULL, status int, ok boolean NOT NULL,
          error text, ms int, items int, bytes int, PRIMARY KEY (polled_at, id)
        ) PARTITION BY RANGE (polled_at)""")
    # The response exactly as it came, gzip-compressed (kept a school year; partitions older than that are dropped)
    op.execute("""
        CREATE TABLE buslog_raw (
          poll_id bigint NOT NULL, polled_at timestamptz NOT NULL, content_type text, body bytea,
          PRIMARY KEY (polled_at, poll_id)
        ) PARTITION BY RANGE (polled_at)""")
    # TfL predictions: a row when a bus first predicts a stop, each time the prediction moves (15 s or more), and when
    # it is no longer predicted ("gone": arrived, or dropped by TfL). `fields` holds everything TfL sent for it.
    op.execute("""
        CREATE TABLE buslog_prediction (
          seen_at timestamptz NOT NULL, poll_id bigint NOT NULL, service_day date NOT NULL, line text NOT NULL,
          vehicle text NOT NULL, stop text NOT NULL, stop_name text, direction text, destination text, towards text,
          change text NOT NULL, expected_at timestamptz, previous_at timestamptz, moved_s int, time_to_station int,
          computed_at timestamptz, current_location text, fields jsonb NOT NULL DEFAULT '{}'
        ) PARTITION BY RANGE (seen_at)""")
    # BODS positions: every bus in every answer, as it was sent (`fields`: every element of its journey)
    op.execute("""
        CREATE TABLE buslog_position (
          seen_at timestamptz NOT NULL, poll_id bigint NOT NULL, service_day date NOT NULL, line text NOT NULL,
          vehicle text NOT NULL, recorded_at timestamptz, valid_until timestamptz, lat double precision,
          lon double precision, geog geography(Point, 4326), bearing double precision, direction text,
          journey_ref text, block_ref text, origin_ref text, origin_name text, origin_aimed_departure timestamptz,
          destination_ref text, destination_name text, operator text, occupancy text, off_route_m double precision,
          fields jsonb NOT NULL DEFAULT '{}'
        ) PARTITION BY RANGE (seen_at)""")
    # TfL's status for the line (disruptions, diversions), every two minutes
    op.execute("""
        CREATE TABLE buslog_status (
          seen_at timestamptz NOT NULL, poll_id bigint NOT NULL, service_day date NOT NULL, line text NOT NULL,
          severity text, reason text, disruptions jsonb NOT NULL DEFAULT '[]'
        ) PARTITION BY RANGE (seen_at)""")
    # The day's timetable as it stood before the window (TfL, from each end): one row per journey per stop
    op.execute("""
        CREATE TABLE buslog_timetable (
          taken_at timestamptz NOT NULL, poll_id bigint NOT NULL, service_day date NOT NULL, line text NOT NULL,
          source text NOT NULL, direction text NOT NULL, journey int NOT NULL, stop text NOT NULL, stop_name text,
          stop_seq int NOT NULL, scheduled_at timestamptz NOT NULL
        ) PARTITION BY RANGE (taken_at)""")
    # The route each day: TfL's stop order and line shape, with NaPTAN's positions for the stops
    op.execute("""
        CREATE TABLE buslog_route (
          taken_at timestamptz NOT NULL, poll_id bigint NOT NULL, service_day date NOT NULL, line text NOT NULL,
          direction text NOT NULL, stops jsonb NOT NULL, shape geography(MultiLineString, 4326)
        ) PARTITION BY RANGE (taken_at)""")
    for t in TABLES:
        # anything outside the made months still lands somewhere; the recorder's nightly job makes the months ahead
        op.execute(f"CREATE TABLE {t}_default PARTITION OF {t} DEFAULT")
        # this month and the next twelve now, so no row lands in the default partition before the job has run
        op.execute(f"""
            DO $$ DECLARE m date := date_trunc('month', now())::date; BEGIN
              FOR i IN 0..12 LOOP
                EXECUTE format('CREATE TABLE IF NOT EXISTS {t}_%s PARTITION OF {t} FOR VALUES FROM (%L) TO (%L)',
                               to_char(m + make_interval(months => i), 'YYYYMM'), m + make_interval(months => i),
                               m + make_interval(months => i + 1));
              END LOOP; END $$""")
    op.execute("CREATE INDEX ix_buslog_poll_day ON buslog_poll (line, service_day, source)")
    op.execute("CREATE INDEX ix_buslog_prediction_day ON buslog_prediction (line, service_day, vehicle, stop)")
    op.execute("CREATE INDEX ix_buslog_position_day ON buslog_position (line, service_day, vehicle, seen_at)")
    op.execute("CREATE INDEX ix_buslog_position_geog ON buslog_position USING GIST (geog)")
    op.execute("CREATE INDEX ix_buslog_timetable_day ON buslog_timetable (line, service_day, direction, journey)")
    op.execute("CREATE INDEX ix_buslog_route_day ON buslog_route (line, service_day, direction)")


def downgrade() -> None:
    for t in TABLES:
        op.execute(f"DROP TABLE IF EXISTS {t} CASCADE")
