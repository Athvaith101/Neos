# Household participation demo

The `/hub.html` page demonstrates a simulated offline-first pledge flow. A household can submit a demand reduction pledge, go offline, queue actions, reconnect and sync, or submit an SMS-style text command. The event log shows whether each event was applied, rejected, expired, duplicated, or is still queued.

The API is namespaced under `/api/participation` so it does not replace the existing `/api/hub` community resilience model. Each browser receives an isolated in-memory session with a bounded session cache. Pledges are demo state only: no phone, SMS gateway, household device, utility, or physical control system is connected. “Points” represent simulated delivered kWh and are not money.

The Vercel static page uses the configured API origin bridge from `web/assets/api.js`; the API runs on Render by default. For deployment details, see [DEPLOYMENT.md](DEPLOYMENT.md).
