# Architecture review — October 23, 2026

## Scope and outcome

Reviewed architecture.md, coco-build-plan.md, README.md, the browser application,
document parser, SQLite workflow, deterministic recovery engine, Snowflake adapter,
provisioning script and 27 tests. Checked current official Snowflake documentation
for semantic views, Search execution rights, managed agent tools and `DATA_AGENT_RUN`.
The hackathon account, Claude inference, semantic view, Search service and managed
agent were tested. A live flight provider was not configured.

The project is a working bounded prototype, not deployment-ready software.

| Priority | Gap found | Design correction / acceptance gate |
|---|---|---|
| P1 | Shared Search/Analyst plus a model-supplied itinerary ID could expose another user's records. | Explicit single-traveler, isolated-schema/role/Search/semantic-view POC. Multi-user admission blocked until end-to-end isolation tests. Search owner's rights called out. |
| Resolved in POC | Concurrent document cap, duplicate events, stale approvals and duplicate simulated actions. | SQLite uses immediate transactions and unique constraints; concurrency/idempotency tests pass. Durable inbox/outbox and cross-provider reconciliation remain production backlog. |
| P1 | Approval and retries could execute stale plans or buy twice after timeouts. | Bind approvals to exact revision/actions/quote; recheck at execution; unknown-outcome reconciliation and partial completion states. |
| P1 | Custom tools were described as if local functions could run inside the managed agent automatically. | Separate backend provider calls from Snowflake snapshot readers/procedures; specify caller-rights validator and supported arguments. |
| P1 | Model-generated prose or candidate flags could be accepted as validation. | Backend enforces mandatory deterministic validation and persists trusted validation IDs; model output is not approval or confirmation. |
| P2 | “Whole trip” and cancellation support overstated the tiny engine. | Explicit delay-only, one-connection fixture; cancellation/unknown arrival/general graph recovery have separate gates. |
| Resolved for fixture | Malformed prices, timestamps, currency, IDs and availability could corrupt decisions. | The engine validates its typed fixture contract and boundary tests pass. Every future provider still needs a schema/freshness adapter. |
| P2 | Polling/Snowflake synchronization could lose jobs or duplicate paid agent runs. | Durable outbox, mirror-before-run, event ordering, itinerary serialization, revision checks and deduplication. |
| P2 | Reimbursement eligibility was underspecified. | Fictional arrival-delay rule identified; actual policy/payment/person/expense/date conditions and missing facts required. Immediate spend remains separate. |
| P2 | Upload cleanup, replacement and deletion were unspecified. | Slot reservations, signatures/page limits, retry states, deduplication, orphan reconciliation and index-aware deletion. |
| P2 | Data model list suggested more SQL existed than it did. | Documented missing migrations/entities; bootstrap comments and versioning warning added. |
| P2 | Reminders/rewards/passport panels could imply live balances or entry clearance. | Explicit snapshot freshness, deterministic reminder lifecycle and expiry-only scope; no implied immigration assessment. |
| P2 | Expensive retries or unbounded parsing could exhaust credits. | Size/token/concurrency/retry limits, warehouse auto-suspend, measurable latency and budget gates. |

## Implementation checks

All 27 local tests pass. Default replay detects a missed train,
recommends the later train, and rejects the cheap bus due to hotel check-in.
Three-hour replay exercises the fictional benefit threshold and produces no
feasible option for the supplied inventory. The document-to-answer workflow and a
managed Snowflake Agent answer were also exercised. These checks do not validate a
live flight provider, real booking execution or claim submission.

## Open build decisions and gates

1. Flight provider must be selected based on account access, permitted use,
   coverage, freshness, update identifiers and limits. Live rebooking inventory
   remains simulated until a separate provider adapter is validated.
2. A deployment host remains undecided. The backend topology is one Python
   process with persistent local SQLite. It is not
   a distributed production design.
3. Structured extraction recognizes labeled fields and readable text PDFs only;
   OCR and provider-specific parsing require separate adapters and tests.
4. Do not deploy private multi-user data until isolation covers all native tools,
   custom tools, threads, downloads, logs and cached/indexed content.

## Sources checked

- [Cortex Search](https://docs.snowflake.com/en/user-guide/snowflake-cortex/cortex-search/cortex-search-overview): searches execute with owner's rights.
- [Agent tool configuration](https://docs.snowflake.com/en/user-guide/snowflake-cortex/cortex-agents-manage): custom procedures/UDFs, execution rights, OBJECT parameter restriction.
- [Cortex Analyst](https://docs.snowflake.com/en/user-guide/snowflake-cortex/cortex-analyst): SQL respects configured Snowflake access controls; a shared service identity is not an end-user identity.
