# propre-rentable-capi-relay

Relais Chariow → Meta API Conversions : envoie un événement `Purchase` pour chaque vente payée
(Chariow n'envoie pas cet événement). `event_id` = ID de vente, donc dédoublonnage automatique côté Meta.

Variables : `CHARIOW_API_KEY`, `META_CAPI_TOKEN`, `PIXEL_ID`, `STORE_URL`, `POLL_SECONDS` (60),
`LOOKBACK_HOURS` (48), `STATE_FILE` (/data/sent.json), `META_TEST_EVENT_CODE` (optionnel), `DRY_RUN=1`.
Santé : `GET :8080/` → dernier passage, dernière erreur, total envoyé.
