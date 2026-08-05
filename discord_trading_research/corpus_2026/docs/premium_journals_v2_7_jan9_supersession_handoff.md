# Premium Journals v2.7 Jan. 9 supersession handoff

Status: `superseded_non_authoritative_never_activate`

The reviewed Jan. 9 migration candidate and its independent audit are preserved as immutable historical evidence, but they no longer authorize an activation workflow. Jan. 9 collection is proceeding on v2.6. No Jan. 9 activation plan, activation-plan audit, activation receipt, projection bundle, rollback receipt, or commit marker may be created.

The exact pre-activation schedule bytes were preserved at `working/superseded_premium_journals_v2_7_jan9_activation_draft_v1/pre_activation_schedule.json` solely so the abandoned design and generic crash tests remain reproducible. That snapshot is not a live schedule, collection route, canonical source, or Cardinal input.

The activation implementation and tests are a reusable fail-closed harness only. The public plan writer, commit entry point, reader, and route resolver reject authority for every root, including when the source is imported from the superseded archive. Isolated tests reach only explicitly private fixture helpers. A future v2.7 authority proposal must be a new Jan. 10 artifact family created only after the Jan. 9 v2.6 canonical is promoted and the resulting schedule SHA-256 and byte count are known. It must receive a new independent audit and cannot inherit activation authority from any Jan. 9 candidate or draft.

Until that new Jan. 10 chain is fully reviewed and committed, operators must follow the current schedule and must not infer authority from the presence of v2.7 code, tests, candidate files, or the superseded snapshot.
