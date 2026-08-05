import assert from "node:assert/strict";
import fs from "node:fs/promises";
import os from "node:os";
import nodePath from "node:path";
import test from "node:test";
import {
  buildForumGroupHeaderNavigationEvidence, buildForumGroupNavigationCheckpoint,
  buildForumNavigationPagePlan, forumGroupEvidenceKey,
  forumGroupNavigationCheckpointFilename,
} from "./discord_browser_collector.mjs";
import {
  buildPremiumV27CanonicalNavigationFields, buildPremiumV27DirectEvidence,
  CHECKPOINT_DIRECTORY_PREFIX, resolvePremiumV27Page,
  selectPremiumV27GroupResolution,
} from "./discord_browser_collector_v2_7.mjs";

const guild = "1167376964680691732", parent = "1283941772577472643";
const child = "1456316273788063925", owner = "1456000000000000000";
const attachment = "1456000000000000001";
const query = "in:premium-journals after:2026-01-07 before:2026-01-09";
const source = `https://discord.com/channels/${guild}/${parent}`;

function accessoryRow(messageId = owner, resultIndex = 1) {
  const membership = [messageId];
  return {
    message_id: messageId, result_index: resultIndex, result_set_size: 1, search_query: query,
    page_number: 1, forum_group_message_ids: membership,
    forum_group_membership_exact: true,
    forum_group_membership_key: forumGroupEvidenceKey(query, 1, membership),
    reply_target_resolution_status: "not_applicable",
    reply_target_unavailability_documented: false,
    attachments: [{ attachment_id: attachment, thread_channel_id: child,
      url: `https://cdn.discordapp.com/attachments/${child}/${attachment}/fixture.png`,
      relation_type: "owned", ownership_status: "owned_exact",
      dom_relation: "exact_message_accessories_descendant", href_in_message_content: false,
      ownership_evidence: { schema_version: "1.0.0", exact: true,
        owner_message_id: messageId, owner_channel_id: child,
        source_channel_id: child, dom_relation: "exact_message_accessories_descendant" } }],
  };
}

function replyRow(messageId = owner, resultIndex = 1) {
  const row = accessoryRow(messageId, resultIndex);
  const target = String(BigInt(messageId) + 20n);
  row.attachments = [];
  Object.assign(row, { reply_context_present: true, reply_context_scope_exact: true,
    reply_target_owner_scoped: true, reply_target_scope_exact: true,
    reply_to_message_id: target, reply_to_channel_id: child,
    reply_to_permalink: `https://discord.com/channels/${guild}/${child}/${target}`,
    reply_to_message_id_source: "owned_reply_context_descendant_content_id",
    reply_target_content_id: `message-content-${target}`,
    reply_to_message_id_conflict: false, reply_to_channel_id_conflict: false,
    reply_target_resolution_status: "exact_target_id",
    reply_target_unavailability_documented: false,
    reply_to_message_id_candidates: [{ message_id: target, channel_id: null,
      source: "owned_reply_context_descendant_content_id", owner_scoped: true }],
    reply_target_id_candidates: [{ message_id: target, channel_id: null,
      source: "owned_reply_context_descendant_content_id", owner_scoped: true }] });
  return row;
}

function planFor(rows, groups = rows.map((row) => [row])) {
  for (const row of rows) row.result_set_size = rows.length;
  return buildForumNavigationPagePlan({ query, pageNumber: 1, reportedTotal: rows.length,
    canonical: {
      groups: groups.map((group) => ({ message_ids: group.map((row) => row.message_id).sort(), direct_header_button_count: 1 })),
      rows: rows.map((row) => ({ message_id: row.message_id, result_index: row.result_index })),
    }, observedAtUtc: "2026-07-22T00:00:00Z" });
}

async function writePlan(temp, plan) {
  await fs.mkdir(temp, { recursive: true });
  const path = nodePath.join(temp, "page_plan.json");
  await fs.writeFile(path, JSON.stringify(plan, null, 2) + "\n");
  return path;
}
function pageDirectory(root) { return nodePath.join(root, CHECKPOINT_DIRECTORY_PREFIX, "2026-01-08", "page_001"); }

async function exactHeaderFallback(temp, row, plan) {
  const evidence = buildForumGroupHeaderNavigationEvidence({ query, pageNumber: 1,
    messageIds: row.forum_group_message_ids, parentForumChannelId: parent,
    sourceUrl: source, destinationUrl: `https://discord.com/channels/${guild}/${child}`,
    backUrl: source, restoredQuery: query, restoredPageNumber: 1,
    restoredGroupMessageIds: row.forum_group_message_ids,
    preNavigationPageMembershipSha256: plan.page_membership_sha256,
    restoredPageMembershipSha256: plan.page_membership_sha256,
    observedAtUtc: "2026-07-22T00:01:00Z" });
  const checkpoint = buildForumGroupNavigationCheckpoint(evidence);
  checkpoint.checkpointed_at_utc = "2026-07-22T00:01:01Z";
  const checkpointPath = nodePath.join(temp, forumGroupNavigationCheckpointFilename(evidence.evidence_key));
  await fs.writeFile(checkpointPath, JSON.stringify(checkpoint, null, 2) + "\n");
  return { method: "header_navigation_v2_6", evidence, checkpoint, checkpointPath };
}

test("v2.7 selects exact direct evidence only when explicitly enabled", () => {
  const row = accessoryRow(); const plan = planFor([row]);
  const input = { groupRows: [row], query, pageNumber: 1,
    pageMembershipSha256: plan.page_membership_sha256,
    pagePlanSha256: "b".repeat(64), pagePlanBytes: 100, currentSourceUrl: source };
  assert.equal(buildPremiumV27DirectEvidence(input).eligible, true);
  assert.equal(selectPremiumV27GroupResolution(input).method, "header_navigation_v2_6");
  assert.equal(selectPremiumV27GroupResolution(input, { enableV27Pilot: true }).method, "direct_consensus_v2_7");
});

test("arbitrary exact:true fallback is rejected; exact v2.6 disk checkpoint passes", async () => {
  const temp = await fs.mkdtemp(nodePath.join(os.tmpdir(), "premium-v27-"));
  try {
    const pageDir = pageDirectory(temp);
    const row = accessoryRow(); row.attachments[0].ownership_evidence.exact = false;
    const plan = planFor([row]); const pagePlanPath = await writePlan(pageDir, plan);
    const common = { groups: [[row]], query, pageNumber: 1, reportedTotal: 1,
      pageMembershipSha256: plan.page_membership_sha256, pagePlanPath,
      currentSourceUrl: source, checkpointDirectory: pageDir, artifactRoot: temp,
      routeDay: "2026-01-08", enableV27Pilot: true };
    await assert.rejects(() => resolvePremiumV27Page({ ...common,
      headerResolver: async () => ({ method: "header_navigation_v2_6", evidence: { exact: true } }) }),
      /envelope_incomplete/);
    const fallback = await exactHeaderFallback(pageDir, row, plan);
    const accepted = await resolvePremiumV27Page({ ...common, headerResolver: async () => fallback });
    assert.equal(accepted.accepted, true);
    const forged = structuredClone(fallback); forged.evidence.back_url = `https://discord.com/channels/${guild}/${child}`;
    await assert.rejects(() => resolvePremiumV27Page({ ...common, headerResolver: async () => forged }), /strict v2.6 validation/);
    await fs.writeFile(fallback.checkpointPath, "{invalid-json");
    await assert.rejects(() => resolvePremiumV27Page({ ...common, headerResolver: async () => fallback }), /strict v2.6 validation/);
  } finally { await fs.rm(temp, { recursive: true, force: true }); }
});

test("caller subset cannot satisfy immutable full-page partition", async () => {
  const temp = await fs.mkdtemp(nodePath.join(os.tmpdir(), "premium-v27-partition-"));
  try {
    const pageDir = pageDirectory(temp);
    const row1 = accessoryRow();
    const row2 = accessoryRow("1456000000000000010", 2);
    row2.attachments[0].attachment_id = "1456000000000000011";
    row2.attachments[0].url = `https://cdn.discordapp.com/attachments/${child}/1456000000000000011/fixture2.png`;
    const plan = planFor([row1, row2]); const pagePlanPath = await writePlan(pageDir, plan);
    await assert.rejects(() => resolvePremiumV27Page({ groups: [[row1]], query,
      pageNumber: 1, reportedTotal: 2, pageMembershipSha256: plan.page_membership_sha256,
      pagePlanPath, currentSourceUrl: source, checkpointDirectory: pageDir,
      artifactRoot: temp, routeDay: "2026-01-08",
      headerResolver: async () => { throw new Error("must not navigate"); }, enableV27Pilot: true }),
      /exact full-page partition failed/);
    await assert.rejects(() => resolvePremiumV27Page({ groups: [[row1], [row2]], query,
      pageNumber: 1, reportedTotal: undefined, pageMembershipSha256: plan.page_membership_sha256,
      pagePlanPath, currentSourceUrl: source, checkpointDirectory: pageDir,
      artifactRoot: temp, routeDay: "2026-01-08", headerResolver: async () => ({}),
      enableV27Pilot: true }), /exact full-page partition failed/);
    await assert.rejects(() => resolvePremiumV27Page({ groups: [[row1], [row2]], query,
      pageNumber: 1, reportedTotal: 2, pageMembershipSha256: undefined,
      pagePlanPath, currentSourceUrl: source, checkpointDirectory: pageDir,
      artifactRoot: temp, routeDay: "2026-01-08", headerResolver: async () => ({}),
      enableV27Pilot: true }), /exact full-page partition failed/);
    await assert.rejects(() => resolvePremiumV27Page({ groups: [[row1], [row2]], query,
      pageNumber: 1, reportedTotal: 2, pageMembershipSha256: plan.page_membership_sha256,
      pagePlanPath, currentSourceUrl: source, checkpointDirectory: temp,
      artifactRoot: temp, routeDay: "2026-01-08", headerResolver: async () => ({}),
      enableV27Pilot: true }), /exact versioned route\/page root/);
    const partitionInput = (groups) => resolvePremiumV27Page({ groups, query,
      pageNumber: 1, reportedTotal: 2, pageMembershipSha256: plan.page_membership_sha256,
      pagePlanPath, currentSourceUrl: source, checkpointDirectory: pageDir,
      artifactRoot: temp, routeDay: "2026-01-08", headerResolver: async () => ({}),
      enableV27Pilot: true });
    const stringPage = structuredClone(row1); stringPage.page_number = "1";
    await assert.rejects(() => partitionInput([[stringPage], [row2]]), /exact full-page partition failed/);
    const stringIndex = structuredClone(row1); stringIndex.result_index = "1";
    await assert.rejects(() => partitionInput([[stringIndex], [row2]]), /exact full-page partition failed/);
    const wrongTotal = structuredClone(row1); wrongTotal.result_set_size = 1;
    await assert.rejects(() => partitionInput([[wrongTotal], [row2]]), /exact full-page partition failed/);
  } finally { await fs.rm(temp, { recursive: true, force: true }); }
});

test("JS direct predicate rejects Python-contract reply and accessory exploits", () => {
  const baseline = accessoryRow(); const plan = planFor([baseline]);
  const input = (row) => ({ groupRows: [row], query, pageNumber: 1,
    pageMembershipSha256: plan.page_membership_sha256, pagePlanSha256: "b".repeat(64),
    pagePlanBytes: 100, currentSourceUrl: source });
  const missingKey = structuredClone(baseline); delete missingKey.forum_group_membership_key;
  assert.equal(buildPremiumV27DirectEvidence(input(missingKey)).eligible, false);
  const embedConflict = structuredClone(baseline);
  embedConflict.embeds = [{ url: `https://cdn.discordapp.com/attachments/1456000000000000099/1456000000000000098/lookalike.png` }];
  assert.equal(buildPremiumV27DirectEvidence(input(embedConflict)).eligible, false);
  const wrongParent = structuredClone(baseline);
  wrongParent.group_header_parent_forum_channel_id = child;
  assert.equal(buildPremiumV27DirectEvidence(input(wrongParent)).eligible, false);
  const arbitraryLinkMissingAttachments = structuredClone(baseline);
  delete arbitraryLinkMissingAttachments.attachments;
  arbitraryLinkMissingAttachments.links = ["https://example.com/not-an-attachment"];
  assert.equal(buildPremiumV27DirectEvidence(input(arbitraryLinkMissingAttachments)).eligible, false);
  const blankQuery = structuredClone(baseline); blankQuery.search_query = " "; blankQuery.forum_group_membership_key = null;
  assert.equal(buildPremiumV27DirectEvidence({ ...input(blankQuery), query: " " }).eligible, false);
  const reply = structuredClone(baseline); reply.attachments = [];
  Object.assign(reply, { reply_context_present: true, reply_context_scope_exact: true,
    reply_target_owner_scoped: true, reply_target_scope_exact: true,
    reply_to_message_id: "1456000000000000020", reply_to_channel_id: child,
    reply_to_permalink: `https://discord.com/channels/${guild}/${child}/1456000000000000020`,
    reply_to_message_id_source: "owned_reply_context_descendant_content_id",
    reply_target_content_id: "message-content-1456000000000000020",
    reply_to_message_id_conflict: false, reply_to_channel_id_conflict: false,
    reply_target_resolution_status: "exact_target_id",
    reply_target_unavailability_documented: false,
    reply_to_message_id_candidates: [], reply_target_id_candidates: [] });
  assert.equal(buildPremiumV27DirectEvidence(input(reply)).eligible, false);
  reply.reply_to_message_id_candidates = [{ message_id: reply.reply_to_message_id,
    channel_id: null, source: reply.reply_to_message_id_source, owner_scoped: true }];
  reply.reply_target_id_candidates = structuredClone(reply.reply_to_message_id_candidates);
  assert.equal(buildPremiumV27DirectEvidence(input(reply)).eligible, true);
});

test("shared Python/JavaScript attachment-signal fixtures fail closed", async () => {
  const fixtures = JSON.parse(await fs.readFile(new URL("./premium_v2_7_direct_parity_fixtures.json", import.meta.url), "utf8"));
  const baseline = replyRow(); const plan = planFor([baseline]);
  const input = (row) => ({ groupRows: [row], query, pageNumber: 1,
    pageMembershipSha256: plan.page_membership_sha256, pagePlanSha256: "b".repeat(64),
    pagePlanBytes: 100, currentSourceUrl: source });
  assert.equal(buildPremiumV27DirectEvidence(input(baseline)).eligible, true);
  for (const fixture of fixtures.reply_cases) {
    const row = replyRow(); row[fixture.field] = structuredClone(fixture.value);
    assert.equal(buildPremiumV27DirectEvidence(input(row)).eligible, fixture.expected_eligible, fixture.name);
  }
  for (const fixture of fixtures.accessory_cases) {
    const row = accessoryRow();
    if (fixture.delete_ownership_evidence_field) delete row.attachments[0].ownership_evidence[fixture.delete_ownership_evidence_field];
    if (fixture.set_row_field) row[fixture.set_row_field] = structuredClone(fixture.value);
    assert.equal(buildPremiumV27DirectEvidence(input(row)).eligible, fixture.expected_eligible, fixture.name);
  }
});

test("direct checkpoint retry is idempotent and tampering fails closed", async () => {
  const temp = await fs.mkdtemp(nodePath.join(os.tmpdir(), "premium-v27-retry-"));
  try {
    const pageDir = pageDirectory(temp); const row = accessoryRow(); const plan = planFor([row]);
    const pagePlanPath = await writePlan(pageDir, plan);
    const common = { groups: [[row]], query, pageNumber: 1, reportedTotal: 1,
      pageMembershipSha256: plan.page_membership_sha256, pagePlanPath,
      currentSourceUrl: source, checkpointDirectory: pageDir, artifactRoot: temp,
      routeDay: "2026-01-08", headerResolver: async () => { throw new Error("must not navigate"); },
      enableV27Pilot: true };
    const first = await resolvePremiumV27Page(common);
    const key = first.expected_group_evidence_keys[0]; const checkpointPath = first.resolutions[key].checkpoint_path;
    const originalBytes = await fs.readFile(checkpointPath);
    await new Promise((resolve) => setTimeout(resolve, 5));
    const second = await resolvePremiumV27Page(common);
    assert.deepEqual(await fs.readFile(checkpointPath), originalBytes);
    assert.equal(second.resolutions[key].evidence.observed_at_utc, first.resolutions[key].evidence.observed_at_utc);
    await fs.writeFile(checkpointPath, "{not-json\n");
    await assert.rejects(() => resolvePremiumV27Page(common), /Immutable v2\.7 checkpoint conflict.*unreadable JSON/);
    await fs.writeFile(checkpointPath, originalBytes);
    const drifted = JSON.parse(originalBytes.toString("utf8")); drifted.evidence.current_source_url = `https://discord.com/channels/${guild}/${child}`;
    await fs.writeFile(checkpointPath, JSON.stringify(drifted, null, 2) + "\n");
    await assert.rejects(() => resolvePremiumV27Page(common), /Immutable v2\.7 checkpoint conflict/);
  } finally { await fs.rm(temp, { recursive: true, force: true }); }
});

test("partial page failure resumes from an existing direct checkpoint", async () => {
  const temp = await fs.mkdtemp(nodePath.join(os.tmpdir(), "premium-v27-resume-"));
  try {
    const pageDir = pageDirectory(temp); const directRow = accessoryRow();
    const fallbackRow = accessoryRow("1456000000000000010", 2);
    fallbackRow.attachments[0].attachment_id = "1456000000000000011";
    fallbackRow.attachments[0].url = `https://cdn.discordapp.com/attachments/${child}/1456000000000000011/fallback.png`;
    fallbackRow.attachments[0].ownership_evidence.exact = false;
    const plan = planFor([directRow, fallbackRow]); const pagePlanPath = await writePlan(pageDir, plan);
    const common = { groups: [[directRow], [fallbackRow]], query, pageNumber: 1, reportedTotal: 2,
      pageMembershipSha256: plan.page_membership_sha256, pagePlanPath,
      currentSourceUrl: source, checkpointDirectory: pageDir, artifactRoot: temp,
      routeDay: "2026-01-08", enableV27Pilot: true };
    await assert.rejects(() => resolvePremiumV27Page({ ...common,
      headerResolver: async () => { throw new Error("simulated later-group failure"); } }), /simulated later-group failure/);
    const directKey = forumGroupEvidenceKey(query, 1, directRow.forum_group_message_ids);
    const directName = `forum_group_direct_consensus_${directKey.split(":")[1]}.json`;
    const directPath = nodePath.join(pageDir, directName); const beforeResume = await fs.readFile(directPath);
    await new Promise((resolve) => setTimeout(resolve, 5));
    const resumed = await resolvePremiumV27Page({ ...common,
      headerResolver: async (rows) => exactHeaderFallback(pageDir, rows[0], plan) });
    assert.equal(resumed.accepted, true);
    assert.equal(resumed.resolutions[directKey].method, "direct_consensus_v2_7");
    assert.deepEqual(await fs.readFile(directPath), beforeResume);
  } finally { await fs.rm(temp, { recursive: true, force: true }); }
});

test("canonical integration rejects incomplete, coerced, and invalid-day page sets", async () => {
  const temp = await fs.mkdtemp(nodePath.join(os.tmpdir(), "premium-v27-canonical-guard-"));
  try {
    const prefix = { accepted: true, page_number: 1, reported_total: 26 };
    await assert.rejects(() => buildPremiumV27CanonicalNavigationFields({ pageResults: [prefix], artifactRoot: temp, routeDay: "2026-01-08" }), /exact expected page count/);
    await assert.rejects(() => buildPremiumV27CanonicalNavigationFields({ pageResults: [{ ...prefix, page_number: "1", reported_total: 1 }], artifactRoot: temp, routeDay: "2026-01-08" }), /incomplete or non-contiguous/);
    await assert.rejects(() => buildPremiumV27CanonicalNavigationFields({ pageResults: [{ ...prefix, reported_total: 1 }], artifactRoot: temp, routeDay: "not-a-date" }), /route day is invalid or historical/);
  } finally { await fs.rm(temp, { recursive: true, force: true }); }
});

test("close snowflake IDs retain exact ordering and canonical integration is byte-bound", async () => {
  const temp = await fs.mkdtemp(nodePath.join(os.tmpdir(), "premium-v27-integrate-"));
  try {
    const first = accessoryRow("1456000000000000001", 1);
    const second = accessoryRow("1456000000000000002", 2);
    const membership = [first.message_id, second.message_id];
    const key = forumGroupEvidenceKey(query, 1, membership);
    for (const row of [first, second]) { row.forum_group_message_ids = membership; row.forum_group_membership_key = key; }
    second.attachments[0].attachment_id = "1456000000000000003";
    second.attachments[0].url = `https://cdn.discordapp.com/attachments/${child}/1456000000000000003/second.png`;
    const plan = planFor([first, second], [[first, second]]); const pageDir = pageDirectory(temp);
    const pagePlanPath = await writePlan(pageDir, plan);
    const page = await resolvePremiumV27Page({ groups: [[second, first]], query,
      pageNumber: 1, reportedTotal: 2, pageMembershipSha256: plan.page_membership_sha256,
      pagePlanPath, currentSourceUrl: source, checkpointDirectory: pageDir,
      artifactRoot: temp, routeDay: "2026-01-08",
      headerResolver: async () => { throw new Error("unexpected fallback"); }, enableV27Pilot: true });
    const evidence = page.resolutions[key].evidence;
    assert.deepEqual(evidence.candidate_tuples.map((item) => item.owner_message_id), membership);
    const fields = await buildPremiumV27CanonicalNavigationFields({ pageResults: [page], artifactRoot: temp, routeDay: "2026-01-08" });
    assert.deepEqual(Object.keys(fields.forum_group_direct_consensus_exact), [key]);
    assert.equal(fields.forum_group_navigation_checkpoint_count, 1);
    assert.match(fields.forum_group_resolution_source_file_set_sha256, /^[a-f0-9]{64}$/);
  } finally { await fs.rm(temp, { recursive: true, force: true }); }
});
