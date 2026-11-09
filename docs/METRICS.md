# Dashboard metrics

Player activity is inferred from server connection/disconnection messages and
reconciled with fresh observer heartbeats. Ambiguous account matches are not
guessed. The database starts empty; only history retained on this deployment is
available. Player names and account IDs are runtime data, not repository fixtures.

World metrics read committed chunked save generations (formats 40–42): progression
flags, generated terrain, portals, and surviving creator-stamped pieces. Generated
zones are not explored-map coverage. Build sites cluster adjacent 32 m cells with
at least five pieces. Plant prefabs and their grown replacements are excluded.
Unsupported or racing saves retain the previous successful snapshot.

Survival deaths are inferred from spawned-character reset signals. Alive online
world time pauses on logout and follows the in-game world clock, including sleep.
Historical interpolated totals are estimates, not lifetime character statistics.

Observer telemetry adds movement, planting, harvest and station observations.
Confirmed combat records cover creatures owned by the dedicated server; they do
not establish every client-owned kill. Version-sensitive adapters fail closed on
unverified Valheim versions, while the identity/clock heartbeat remains available.

The dashboard is a read-only view of game state. Its database, journal history,
heartbeats and telemetry contain player information and must stay out of source
control. Exposing the dashboard makes displayed player names and world activity
visible to its visitors; keep it local or put authentication in your reverse proxy.
