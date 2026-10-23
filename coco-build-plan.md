# CoCo build record and production backlog

CoCo is the development assistant; the runtime is a Claude-powered Cortex Agent. The hackathon connection, SQL path and Agent API were verified with CoCo. The current deployed resource record is in `architecture.md`. The numbered list below is the production-hardening backlog, not a claim that each item exists in this POC.

## First session

Read architecture.md, review.md, sql/001_core.sql, data/demo_trip.json, and the local recovery engine/tests. Inspect the configured hackathon connection without printing credentials. Confirm account, active role, warehouse, supported Claude model, Cortex privileges, and available Search/Agent features. Report prerequisites that fail. Work within a new isolated schema using synthetic data. Do not alter account-wide cross-region routing or grant broader privileges automatically. The initial demo admits one authenticated synthetic traveler; it is not authorized to serve private multi-user data through one shared agent/Search service.

## Tasks in order

1. Validate DDL against the account and confirm the selected database/schema. The bootstrap is incomplete: add numbered migrations for inventory/payment/policy-version records as required, without assuming CREATE IF NOT EXISTS upgrades tables. Deploy reviewed objects and load synthetic itinerary facts. Record deployed names, privileges, and smoke-test results.
2. Implement the planned SQLite control store with transactional upload reservations, unique inbox/outbox/action keys and restartable job states. Harden the fixture engine with typed input validation; reject malformed/nonfinite/negative/mixed-currency inputs and handle unknown availability. Add boundary tests before consuming model/provider output.
3. Implement a Snowflake repository adapter that produces the hardened engine input. Backend adapters write timestamped inventory/event snapshots; procedures read them. Package the validator as a supported caller-rights procedure, with scalar IDs or validated serialized JSON rather than unsupported OBJECT parameters. Compare local and Snowflake results.
4. Build the semantic view restricted to the one admitted traveler, with descriptions for departure, arrival, transfer buffer, additional cost and currency. Define verified Analyst questions and expected answers. Separate descriptive analytics from authoritative plan validation.
5. Build staged synthetic booking/policy documents, parsing, reviewed extraction, source/page mappings and Cortex Search. Bound file/page/chunk sizes and parsing retries. Keep identity scans outside general Search. Search runs with owner's rights: isolate its source to the POC traveler; a model-selected filter is not authorization. Verify the 15-document boundary, deduplication, failure retry and deletion lifecycle.
6. Create a Cortex Agent on an available Claude model with Analyst, Search and narrow validation/benefit tools. Do not enable a shared private-document Search tool across tenants. The backend derives user/resource scope, validates final tool results and saves proposals through a guarded revision check. Agent text cannot mark actions approved or bookings confirmed.
7. Call the agent through the Run API with the demo event. Verify facts, citations, rejection reasons, missing-information states and no guaranteed reimbursement claim. Bind thread IDs to the authenticated itinerary. Record traces, latency, input revision and resource use without sensitive document text in ordinary logs.
8. Wire backend approval/execution with simulated inventory/actions. Test duplicate/stale events, expired quotes, partial failure, provider timeout with unknown outcome, no feasible alternatives and restart recovery. Preserve confirmed reservations. Real booking/cancel/payment execution is out of scope.
9. Run the acceptance matrix in architecture.md and record pass/fail/not implemented. Cancellation events and general multi-leg recovery need their own implementations; do not label delay-only success as support for them. Only then add rewards/passport metadata panels and a live status adapter if credentials permit.

Use existing account-supported capabilities and current official documentation. Keep raw model names, deployed resources, and measured results in project records. Label each integration as implemented/verified/pending; never replace a missing cloud connection with an unlabeled mock.

## Required deployment record

Record the isolated schema, runtime identity/role, warehouse, model, agent, semantic view, Search source scope, custom procedure execution rights, schema version, provider mode, and commands/results of smoke tests. Preserve no secrets in this record. Stop before admitting a second user until the multi-tenant gate in architecture.md is implemented and tested.
