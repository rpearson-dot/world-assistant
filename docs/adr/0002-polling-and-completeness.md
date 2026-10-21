# 0002 Poll slowly, store everything, replace only on complete data

**Status:** accepted

**Context.** National Highways allows 10 calls a minute per key and its planned-works feed is many
large pages. Fuel Finder prices change a few times a day and a pull takes minutes. Upstream
responses are sometimes truncated or empty.

**Decision.**
- Poll at the speed the data changes: live road events every 10 minutes, planned works every 2
  hours, fuel prices every 30 minutes, stations daily. All intervals are settings.
- Share one Redis sliding-window rate limiter between processes, set below the provider's limit.
- Treat a fetch as authoritative only if every page arrived. Mark disappeared events cleared
  instead of deleting; apply fuel prices only when coverage is at least 90% of known stations.
- Log history only when something changes (sign text, price).

**Consequences.** A bad pull never wipes good data. Storage grows slowly and meaningfully. A genuine
incident can be up to 10 minutes old before it appears.
