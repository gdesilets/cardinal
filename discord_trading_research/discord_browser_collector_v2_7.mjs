/**
 * Future-only Premium Journals v2.7 direct-consensus adapter.
 *
 * This module is not imported by discord_browser_collector.mjs.  The existing
 * v2.6 collector remains the live default.  A caller must explicitly import
 * this file and set enableV27Pilot=true before it can choose the direct path.
 */
import crypto from "node:crypto";
import fs from "node:fs/promises";
import nodePath from "node:path";
import { isDeepStrictEqual } from "node:util";
import * as v26 from "./discord_browser_collector.mjs";

export const COLLECTOR_VERSION = "2.7";
export const PROVENANCE_VERSION = "2.7";
export const AUTHORITATIVE_DIRECTORY = "raw/channel_segments_v2_7";
export const CHECKPOINT_DIRECTORY_PREFIX = "raw/premium_journals_v2_7_checkpoints";
export const EVIDENCE_TYPE = "forum_group_direct_candidate_consensus_exact";
const GUILD_ID = "1167376964680691732";
const PREMIUM_ID = "1283941772577472643";
const EXACT_REPLY_SOURCES = new Set([
  "owned_reply_context_descendant_content_id",
  "owned_reply_descendant_message_id",
  "owned_reply_descendant_aria_reference",
  "owned_reply_descendant_data_list_item_id",
  "owned_reply_descendant_data_message_id",
  "owned_reply_permalink",
]);

function ids(value) {
  if (!Array.isArray(value) || !value.length) return null;
  const out = value.map((item) => String(item || ""));
  if (out.some((item) => !/^\d{15,22}$/.test(item)) || new Set(out).size !== out.length) return null;
  return [...out].sort();
}
function compareSnowflake(left, right) {
  const a = /^\d{15,22}$/.test(String(left || "")) ? BigInt(left) : 0n;
  const b = /^\d{15,22}$/.test(String(right || "")) ? BigInt(right) : 0n;
  return a < b ? -1 : a > b ? 1 : 0;
}
function pythonTruthy(value) {
  if (value == null || value === false || value === 0 || value === "") return false;
  if (Array.isArray(value)) return value.length > 0;
  if (typeof value === "object") return Object.keys(value).length > 0;
  return true;
}
function exactRoot(value, parentId) {
  try {
    const url = new URL(String(value || ""));
    const p = url.pathname.split("/").filter(Boolean);
    return url.protocol === "https:" && ["discord.com", "www.discord.com"].includes(url.hostname) &&
      !url.search && !url.hash && p.length === 3 && p[0] === "channels" &&
      p[1] === GUILD_ID && p[2] === parentId ? url.href : null;
  } catch { return null; }
}
function exactPermalink(value, channelId, messageId) {
  try {
    const url = new URL(String(value || "")); const p = url.pathname.split("/").filter(Boolean);
    return url.protocol === "https:" && ["discord.com", "www.discord.com"].includes(url.hostname) &&
      !url.search && !url.hash && p.length === 4 && p[0] === "channels" && p[1] === GUILD_ID &&
      p[2] === channelId && p[3] === messageId;
  } catch { return false; }
}
function exactAttachment(value, attachmentId) {
  try {
    const url = new URL(String(value || "")); const p = url.pathname.split("/").filter(Boolean);
    return url.protocol === "https:" && ["cdn.discordapp.com", "media.discordapp.net"].includes(url.hostname) &&
      !url.search && !url.hash && p.length === 4 && p[0] === "attachments" && /^\d{15,22}$/.test(p[1]) && p[2] === attachmentId && Boolean(p[3]) ? p[1] : null;
  } catch { return null; }
}
function candidateEvidenceValid(row, target, source) {
  const candidates = [
    ...(Array.isArray(row?.reply_to_message_id_candidates) ? row.reply_to_message_id_candidates : []),
    ...(Array.isArray(row?.reply_target_id_candidates) ? row.reply_target_id_candidates : []),
  ].filter((item) => String(item?.message_id || "") === target && String(item?.source || "") === source);
  return candidates.some((item) => {
    const raw = String(item?.raw_value || "");
    if (["owned_reply_descendant_aria_reference", "owned_reply_descendant_data_list_item_id"].includes(source) && item?.owner_scoped !== true) return false;
    if (["owned_reply_descendant_message_id", "owned_reply_descendant_aria_reference"].includes(source)) {
      const found = [...raw.matchAll(/message-(?:content|username|timestamp)-(\d{15,22})/g)].map((match) => match[1]);
      return new Set(found).size === 1 && found[0] === target;
    }
    if (source === "owned_reply_descendant_data_list_item_id") return new RegExp(`^(?:chat-messages___|NO_LIST___|search-result-)${target}(?:[^\\d].*)?$`).test(raw);
    if (source === "owned_reply_descendant_data_message_id") return raw === target;
    if (source === "owned_reply_permalink") return raw === String(row?.reply_to_permalink || "");
    return false;
  });
}

function replyCandidates(rows, errors) {
  const result = []; const channelCandidates = new Set();
  for (const [index, row] of rows.entries()) {
    const prefix = `reply_row_${index + 1}`;
    const owner = String(row?.message_id || ""); const target = String(row?.reply_to_message_id || "");
    const channel = String(row?.reply_to_channel_id || ""); const link = String(row?.reply_to_permalink || "");
    const source = String(row?.reply_to_message_id_source || "");
    const hasSignal = Boolean(target || channel || link || row?.reply_to_message_id_source || row?.reply_target_resolution_status === "exact_target_id");
    if (!hasSignal) continue;
    if (!/^\d{15,22}$/.test(owner) || !/^\d{15,22}$/.test(target) || target === owner || !/^\d{15,22}$/.test(channel) ||
        !exactPermalink(link, channel, target) || row?.reply_context_present !== true ||
        row?.reply_context_scope_exact !== true || row?.reply_target_owner_scoped !== true ||
        row?.reply_target_scope_exact !== true || row?.reply_to_message_id_conflict !== false ||
        row?.reply_to_channel_id_conflict !== false || row?.reply_target_resolution_status !== "exact_target_id" ||
        row?.reply_target_unavailability_documented !== false || !EXACT_REPLY_SOURCES.has(source)) {
      errors.push(`${prefix}_not_exact_owner_scoped`); continue;
    }
    const contentId = String(row?.reply_target_content_id || "");
    if ((contentId && contentId !== `message-content-${target}`) ||
        (source === "owned_reply_context_descendant_content_id" && contentId !== `message-content-${target}`)) {
      errors.push(`${prefix}_content_id_evidence_invalid`);
    }
    if (source !== "owned_reply_context_descendant_content_id" && !candidateEvidenceValid(row, target, source)) errors.push(`${prefix}_row_owned_candidate_evidence_invalid`);
    for (const field of ["reply_to_message_id_candidates", "reply_target_id_candidates"]) {
      const candidates = row?.[field];
      if (!Array.isArray(candidates) || !candidates.length) { errors.push(`${prefix}_${field}_missing`); continue; }
      for (const candidate of candidates) {
        const candidateChannel = String(candidate?.channel_id || "");
        if (String(candidate?.message_id || "") !== target || candidate?.owner_scoped !== true || !EXACT_REPLY_SOURCES.has(String(candidate?.source || ""))) errors.push(`${prefix}_${field}_identity_or_scope_invalid`);
        if (candidateChannel) {
          if (!/^\d{15,22}$/.test(candidateChannel)) errors.push(`${prefix}_${field}_channel_invalid`);
          else channelCandidates.add(candidateChannel);
        }
      }
    }
    channelCandidates.add(channel);
    result.push({ method: "owned_reply_anchor", owner_message_id: owner, target_message_id: target, attachment_id: "", target_url: link, thread_channel_id: channel });
  }
  if (result.length && channelCandidates.size !== 1) errors.push("reply_channel_candidate_count_not_one");
  return result;
}
function attachmentCandidates(rows, errors) {
  const result = [];
  for (const [rowIndex, row] of rows.entries()) {
    const exactPairs = new Set(); const owner = String(row?.message_id || "");
    const attachments = Array.isArray(row?.attachments) ? row.attachments : [];
    if (!Array.isArray(row?.attachments)) errors.push(`attachment_row_${rowIndex + 1}_attachments_not_array`);
    for (const [attachmentIndex, item] of attachments.entries()) {
      const attachmentId = String(item?.attachment_id || "");
      const channel = exactAttachment(item?.url, attachmentId); const proof = item?.ownership_evidence || {};
      if (!/^\d{15,22}$/.test(owner) || !channel || item?.relation_type !== "owned" ||
          item?.ownership_status !== "owned_exact" || item?.dom_relation !== "exact_message_accessories_descendant" ||
          item?.href_in_message_content !== false || proof?.schema_version !== "1.0.0" || proof?.exact !== true || proof?.owner_message_id !== owner ||
          proof?.owner_channel_id !== channel || proof?.source_channel_id !== channel ||
          proof?.dom_relation !== "exact_message_accessories_descendant" ||
          (item?.thread_channel_id != null && String(item.thread_channel_id) !== channel)) {
        errors.push(`attachment_row_${rowIndex + 1}_${attachmentIndex + 1}_not_exact_owned_accessory`); continue;
      }
      exactPairs.add(`${channel}:${attachmentId}`);
      result.push({ method: "owned_attachment_accessory", owner_message_id: owner, target_message_id: "", attachment_id: attachmentId, target_url: String(item.url), thread_channel_id: channel });
    }
    const probes = [];
    if (Array.isArray(row?.links)) probes.push(...row.links);
    for (const field of ["media_assets", "embeds"]) for (const item of Array.isArray(row?.[field]) ? row[field] : []) {
      if (typeof item === "string") probes.push(item);
      else if (item && typeof item === "object") probes.push(item.url, item.src, item.href);
    }
    for (const probe of probes) {
      try {
        const url = new URL(String(probe || "")); const p = url.pathname.split("/").filter(Boolean);
        if (url.protocol === "https:" && ["cdn.discordapp.com", "media.discordapp.net"].includes(url.hostname) && p[0] === "attachments" && /^\d{15,22}$/.test(p[1] || "") && /^\d{15,22}$/.test(p[2] || "") && !exactPairs.has(`${p[1]}:${p[2]}`)) errors.push(`attachment_row_${rowIndex + 1}_content_or_embed_not_owned_accessory`);
      } catch {}
    }
  }
  const channels = new Set(result.map((item) => item.thread_channel_id));
  if (!result.length) errors.push("attachment_candidate_missing");
  if (channels.size !== 1) errors.push("attachment_channel_candidate_count_not_one");
  return result;
}

/** Build only a strict direct proof.  Null means the caller must use v2.6 header navigation. */
export function buildPremiumV27DirectEvidence({ groupRows, query, pageNumber, pageMembershipSha256, pagePlanSha256, pagePlanBytes, currentSourceUrl, parentForumChannelId = PREMIUM_ID, observedAtUtc = new Date().toISOString() } = {}) {
  const errors = []; const membership = ids(groupRows?.[0]?.forum_group_message_ids);
  const expectedKey = v26.forumGroupEvidenceKey(query, pageNumber, membership || []);
  const ownerIds = Array.isArray(groupRows) ? groupRows.map((row) => String(row?.message_id || "")).sort() : [];
  if (typeof query !== "string" || !query.trim() || query !== query.trim() || !Number.isInteger(pageNumber) || pageNumber < 1 || !membership || !Array.isArray(groupRows) || groupRows.length !== membership.length || JSON.stringify(ownerIds) !== JSON.stringify(membership) || new Set(ownerIds).size !== ownerIds.length ||
      groupRows.some((row) => !/^\d{15,22}$/.test(String(row?.message_id || "")) || row?.search_query !== query || row?.page_number !== pageNumber || row?.forum_group_membership_exact !== true || row?.forum_group_membership_key !== expectedKey || JSON.stringify(ids(row?.forum_group_message_ids)) !== JSON.stringify(membership))) errors.push("group_membership_query_page_or_key_not_exact");
  if (!exactRoot(currentSourceUrl, parentForumChannelId)) errors.push("current_source_url_not_exact_authorized_parent");
  if (parentForumChannelId !== PREMIUM_ID) errors.push("parent_forum_channel_not_premium_journals");
  if (!/^[a-f0-9]{64}$/.test(String(pageMembershipSha256)) || !/^[a-f0-9]{64}$/.test(String(pagePlanSha256)) || !Number.isInteger(pagePlanBytes) || pagePlanBytes < 1) errors.push("page_plan_binding_invalid");
  const hasReplySignal = (groupRows || []).some((row) => Boolean(row?.reply_to_message_id || row?.reply_to_channel_id || row?.reply_to_permalink || row?.reply_to_message_id_source || row?.reply_target_resolution_status === "exact_target_id"));
  const hasAttachmentSignal = (groupRows || []).some((row) => ["attachments", "links", "media_assets", "embeds"].some((field) => pythonTruthy(row?.[field])));
  const replyErrors = []; const attachmentErrors = [];
  const reply = replyCandidates(groupRows || [], replyErrors); const accessories = attachmentCandidates(groupRows || [], attachmentErrors);
  if (hasReplySignal) errors.push(...replyErrors);
  if (hasAttachmentSignal) errors.push(...attachmentErrors);
  const candidates = [...(replyErrors.length ? [] : reply), ...(attachmentErrors.length ? [] : accessories)]
    .sort((a, b) => compareSnowflake(a.owner_message_id, b.owner_message_id) || a.method.localeCompare(b.method) || compareSnowflake(a.target_message_id, b.target_message_id) || compareSnowflake(a.attachment_id, b.attachment_id));
  const channels = [...new Set(candidates.map((item) => item.thread_channel_id))].sort(compareSnowflake);
  if (!candidates.length) errors.push("direct_candidate_missing");
  if (channels.length !== 1) errors.push("direct_candidate_channel_count_not_one");
  const child = channels.length === 1 ? channels[0] : "";
  if (child === parentForumChannelId) errors.push("direct_candidate_parent_forum_cannot_be_child");
  for (const row of groupRows || []) {
    if (![null, undefined, parentForumChannelId].includes(row?.group_header_parent_forum_channel_id)) errors.push("group_header_parent_forum_channel_conflict");
    const card = String(row?.group_header_data_list_item_id || "");
    if (!card) continue;
    const match = card.match(/^forum-channel-list-(\d{15,22})___(\d{15,22})$/);
    if (!match || match[1] !== parentForumChannelId || match[2] !== child) errors.push("group_header_card_candidate_conflict");
  }
  const evidence = {
    provenance_version: PROVENANCE_VERSION, schema_version: "1.0.0", evidence_type: EVIDENCE_TYPE,
    evidence_key: expectedKey, query, page_number: pageNumber, group_message_ids: membership || [],
    page_membership_sha256: pageMembershipSha256, page_plan_sha256: pagePlanSha256, page_plan_bytes: pagePlanBytes,
    guild_id: GUILD_ID, parent_forum_channel_id: parentForumChannelId, current_source_url: currentSourceUrl,
    current_source_parent_verified: !errors.includes("current_source_url_not_exact_authorized_parent"), thread_channel_id: child,
    destination_url: `https://discord.com/channels/${GUILD_ID}/${child}`, candidate_tuples: candidates, candidate_count: candidates.length,
    channel_candidates: channels, channel_candidate_count: channels.length, candidate_methods: [...new Set(candidates.map((c) => c.method))].sort(),
    navigation_performed: false, source_scope: "discord_only", outside_sources_used: false, authenticated: true, observed_at_utc: observedAtUtc,
  };
  return { eligible: errors.length === 0, errors: [...new Set(errors)].sort(), thread_channel_id: child || null, evidence };
}

/** A non-eligible group is intentionally sent through the existing v2.6 header route. */
export function selectPremiumV27GroupResolution(input, options = {}) {
  if (options.enableV27Pilot !== true) return { method: "header_navigation_v2_6", reason: "v2_7_pilot_not_explicitly_enabled" };
  const direct = buildPremiumV27DirectEvidence(input);
  return direct.eligible ? { method: "direct_consensus_v2_7", ...direct } : { method: "header_navigation_v2_6", direct_errors: direct.errors };
}

export function buildPremiumV27Checkpoint(evidence, checkpointedAtUtc = new Date().toISOString()) {
  return { schema_version: "1.0.0", artifact_type: "discord_forum_group_direct_consensus_checkpoint", immutable: true, checkpointed_at_utc: checkpointedAtUtc,
    evidence_key: evidence?.evidence_key ?? null, query: evidence?.query ?? null, page_number: evidence?.page_number ?? null, group_message_ids: evidence?.group_message_ids ?? [],
    current_source_url: evidence?.current_source_url ?? null, destination_url: evidence?.destination_url ?? null, thread_channel_id: evidence?.thread_channel_id ?? null,
    page_membership_sha256: evidence?.page_membership_sha256 ?? null, page_plan_sha256: evidence?.page_plan_sha256 ?? null, page_plan_bytes: evidence?.page_plan_bytes ?? null,
    candidate_tuples: evidence?.candidate_tuples ?? [], evidence };
}

export function validatePremiumV27DirectEvidence(evidence, input = {}) {
  const errors = [];
  if (!evidence || typeof evidence !== "object" || Array.isArray(evidence)) return { valid: false, errors: ["v2_7_direct_evidence_missing"] };
  if (!String(evidence.observed_at_utc || "").endsWith("Z") || !Number.isFinite(Date.parse(evidence.observed_at_utc))) errors.push("v2_7_direct_evidence_timestamp_invalid");
  const derived = buildPremiumV27DirectEvidence({ ...input, observedAtUtc: evidence.observed_at_utc });
  if (!derived.eligible) errors.push(...derived.errors.map((item) => `v2_7_direct:${item}`));
  if (!isDeepStrictEqual(evidence, derived.evidence)) errors.push("v2_7_direct_evidence_binding_mismatch");
  return { valid: errors.length === 0, errors: [...new Set(errors)].sort(), thread_channel_id: errors.length ? null : derived.thread_channel_id };
}

export function validatePremiumV27Checkpoint(checkpoint, evidence, input = {}) {
  const errors = []; const direct = validatePremiumV27DirectEvidence(evidence, input);
  if (!checkpoint || typeof checkpoint !== "object" || Array.isArray(checkpoint)) return { valid: false, errors: ["v2_7_checkpoint_missing"], direct };
  if (!String(checkpoint.checkpointed_at_utc || "").endsWith("Z") || !Number.isFinite(Date.parse(checkpoint.checkpointed_at_utc))) errors.push("v2_7_checkpoint_timestamp_invalid");
  const expected = buildPremiumV27Checkpoint(evidence, checkpoint.checkpointed_at_utc);
  if (!isDeepStrictEqual(checkpoint, expected)) errors.push("v2_7_checkpoint_binding_mismatch");
  if (!direct.valid) errors.push(...direct.errors);
  return { valid: errors.length === 0, errors: [...new Set(errors)].sort(), direct };
}

async function readPremiumV27Checkpoint(directory, evidenceKey, groupRows, options = {}) {
  const filename = checkpointFilename(evidenceKey);
  if (!filename) throw new Error("v2.7 checkpoint evidence key invalid");
  const finalPath = nodePath.join(directory, filename);
  let bytes;
  try { bytes = await fs.readFile(finalPath); }
  catch (error) {
    if (error?.code === "ENOENT") return null;
    throw error;
  }
  let checkpoint;
  try { checkpoint = JSON.parse(bytes.toString("utf8")); }
  catch { throw new Error(`Immutable v2.7 checkpoint conflict for ${evidenceKey}: unreadable JSON`); }
  const validation = validatePremiumV27Checkpoint(checkpoint, checkpoint?.evidence, { ...options, groupRows });
  if (!validation.valid) throw new Error(`Immutable v2.7 checkpoint conflict for ${evidenceKey}: ${validation.errors.join(",")}`);
  return { checkpointPath: finalPath, checkpoint, checkpointBytes: bytes, reused: true,
    threadChannelId: validation.direct.thread_channel_id };
}

export async function persistPremiumV27CheckpointExclusive(directory, evidence, groupRows, options = {}) {
  const filename = checkpointFilename(evidence?.evidence_key); if (!filename) throw new Error("v2.7 checkpoint evidence key invalid");
  await fs.mkdir(directory, { recursive: true });
  const reusable = await readPremiumV27Checkpoint(directory, evidence.evidence_key, groupRows, options);
  if (reusable) return reusable;
  const finalPath = nodePath.join(directory, filename); const checkpoint = buildPremiumV27Checkpoint(evidence, options.checkpointedAtUtc);
  const validation = validatePremiumV27Checkpoint(checkpoint, evidence, { ...options, groupRows });
  if (!validation.valid) throw new Error(`New immutable v2.7 checkpoint failed validation: ${validation.errors.join(",")}`);
  const bytes = JSON.stringify(checkpoint, null, 2) + "\n"; const temporary = `${finalPath}.${process.pid}.${crypto.randomUUID()}.tmp`;
  try { await fs.writeFile(temporary, bytes, { flag: "wx" }); await fs.link(temporary, finalPath); await fs.unlink(temporary); return { checkpointPath: finalPath, checkpoint, reused: false }; }
  catch (error) {
    await fs.unlink(temporary).catch(() => {}); if (error?.code !== "EEXIST") throw error;
    const raced = await readPremiumV27Checkpoint(directory, evidence.evidence_key, groupRows, options);
    if (!raced) throw new Error(`Immutable v2.7 checkpoint disappeared during exclusive persistence for ${evidence.evidence_key}`);
    return raced;
  }
}

export function checkpointFilename(evidenceKey) { const suffix = String(evidenceKey || "").match(/^forum-group-navigation:([a-f0-9]{64})$/)?.[1]; return suffix ? `forum_group_direct_consensus_${suffix}.json` : null; }

export function validatePremiumV27PagePartition(groups, pagePlan, expected = {}) {
  const errors = []; const planValidation = v26.validateForumNavigationPagePlan(pagePlan, {
    query: expected.query, pageNumber: expected.pageNumber, reportedTotal: expected.reportedTotal,
    pageMembershipSha256: expected.pageMembershipSha256,
  });
  if (!planValidation.valid) errors.push(...planValidation.errors);
  if (!String(expected.query || "").trim()) errors.push("v2_7_page_query_missing");
  if (!Number.isInteger(expected.pageNumber) || expected.pageNumber < 1) errors.push("v2_7_page_number_invalid");
  if (!Number.isInteger(expected.reportedTotal) || expected.reportedTotal < 1) errors.push("v2_7_page_reported_total_invalid");
  if (!/^[a-f0-9]{64}$/.test(String(expected.pageMembershipSha256 || "")) || pagePlan?.page_membership_sha256 !== expected.pageMembershipSha256) errors.push("v2_7_page_membership_hash_missing_or_mismatch");
  if (!Array.isArray(groups) || !groups.length) return { valid: false, errors: [...new Set([...errors, "v2_7_page_groups_missing"])].sort() };
  const expectedGroupArrays = Array.isArray(pagePlan?.canonical?.groups) ? pagePlan.canonical.groups.map((group) => ids(group?.message_ids)) : [];
  if (expectedGroupArrays.some((value) => !value)) errors.push("v2_7_page_plan_group_membership_invalid");
  if ((pagePlan?.canonical?.groups || []).some((group) => group?.direct_header_button_count !== 1)) errors.push("v2_7_page_plan_header_count_not_exact");
  const expectedKeys = expectedGroupArrays.map((value) => v26.forumGroupEvidenceKey(expected.query, expected.pageNumber, value || [])).sort();
  const observedKeys = []; const observedRows = [];
  for (const groupRows of groups) {
    const membership = ids(groupRows?.[0]?.forum_group_message_ids); const key = v26.forumGroupEvidenceKey(expected.query, expected.pageNumber, membership || []);
    if (!membership || !key || !Array.isArray(groupRows) || groupRows.length !== membership.length) { errors.push("v2_7_page_group_invalid"); continue; }
    const groupOwnerIds = groupRows.map((row) => String(row?.message_id || "")).sort();
    if (JSON.stringify(groupOwnerIds) !== JSON.stringify(membership) || new Set(groupOwnerIds).size !== groupOwnerIds.length) errors.push(`v2_7_page_group_rows_not_exact:${key}`);
    for (const row of groupRows) {
      if (row?.search_query !== expected.query || row?.page_number !== expected.pageNumber || row?.result_set_size !== expected.reportedTotal || !Number.isInteger(row?.result_index) || row?.forum_group_membership_exact !== true || row?.forum_group_membership_key !== key || JSON.stringify(ids(row?.forum_group_message_ids)) !== JSON.stringify(membership)) errors.push(`v2_7_page_group_row_binding_invalid:${key}`);
      observedRows.push({ message_id: String(row?.message_id || ""), result_index: row?.result_index });
    }
    observedKeys.push(key);
  }
  observedKeys.sort();
  if (new Set(observedKeys).size !== observedKeys.length || JSON.stringify(observedKeys) !== JSON.stringify(expectedKeys) || JSON.stringify([...(pagePlan?.expected_group_evidence_keys || [])].sort()) !== JSON.stringify(expectedKeys)) errors.push("v2_7_page_group_partition_key_set_mismatch");
  const canonicalRows = Array.isArray(pagePlan?.canonical?.rows) ? pagePlan.canonical.rows.map((row) => ({ message_id: String(row?.message_id || ""), result_index: row?.result_index })).sort((a, b) => a.result_index - b.result_index || a.message_id.localeCompare(b.message_id)) : [];
  observedRows.sort((a, b) => a.result_index - b.result_index || a.message_id.localeCompare(b.message_id));
  if (new Set(observedRows.map((row) => row.message_id)).size !== observedRows.length || JSON.stringify(observedRows) !== JSON.stringify(canonicalRows)) errors.push("v2_7_page_row_set_not_exact_plan");
  const firstIndex = (expected.pageNumber - 1) * 25 + 1;
  const expectedCount = Math.max(0, Math.min(25, expected.reportedTotal - firstIndex + 1));
  const expectedIndices = Array.from({ length: expectedCount }, (_, index) => firstIndex + index);
  if (canonicalRows.length !== expectedCount || JSON.stringify(canonicalRows.map((row) => row.result_index)) !== JSON.stringify(expectedIndices) || canonicalRows.some((row) => !/^\d{15,22}$/.test(row.message_id) || !Number.isInteger(row.result_index))) errors.push("v2_7_page_plan_row_count_or_indices_invalid");
  if (!exactRoot(expected.currentSourceUrl, expected.parentForumChannelId || PREMIUM_ID)) errors.push("v2_7_page_current_source_invalid");
  return { valid: errors.length === 0, errors: [...new Set(errors)].sort(), expected_group_keys: expectedKeys };
}

async function validatePremiumV27HeaderFallback(fallback, groupRows, context) {
  const errors = [];
  if (fallback?.method !== "header_navigation_v2_6" || !fallback?.evidence || !fallback?.checkpoint || !fallback?.checkpointPath) return { valid: false, errors: ["v2_7_header_fallback_envelope_incomplete"] };
  const key = v26.forumGroupEvidenceKey(context.query, context.pageNumber, ids(groupRows?.[0]?.forum_group_message_ids) || []);
  const filename = v26.forumGroupNavigationCheckpointFilename(key); const expectedPath = nodePath.resolve(context.checkpointDirectory, String(filename || ""));
  if (!filename || nodePath.resolve(String(fallback.checkpointPath)) !== expectedPath) errors.push("v2_7_header_checkpoint_path_mismatch");
  let diskBytes = null; let diskCheckpoint = null;
  try { diskBytes = await fs.readFile(expectedPath); diskCheckpoint = JSON.parse(diskBytes.toString("utf8")); }
  catch { errors.push("v2_7_header_checkpoint_file_missing_or_invalid"); }
  if (diskCheckpoint && (!isDeepStrictEqual(diskCheckpoint, fallback.checkpoint) || !isDeepStrictEqual(diskCheckpoint.evidence, fallback.evidence))) errors.push("v2_7_header_checkpoint_disk_binding_mismatch");
  const validation = v26.validateForumGroupNavigationCheckpoint(fallback.checkpoint, groupRows[0], { parentForumChannelId: PREMIUM_ID, pageMembershipSha256: context.pageMembershipSha256 });
  if (!validation.valid) errors.push(...validation.errors.map((item) => `v2_7_header:${item}`));
  if (fallback.evidence?.source_url !== context.currentSourceUrl || fallback.evidence?.back_url !== context.currentSourceUrl || fallback.evidence?.query !== context.query || Number(fallback.evidence?.page_number) !== Number(context.pageNumber)) errors.push("v2_7_header_source_back_query_or_page_mismatch");
  return { valid: errors.length === 0, errors: [...new Set(errors)].sort(), thread_channel_id: errors.length ? null : validation.evidence_validation.thread_channel_id,
    checkpoint_sha256: diskBytes ? crypto.createHash("sha256").update(diskBytes).digest("hex") : null, checkpoint_bytes: diskBytes?.length || 0, checkpoint_path: expectedPath };
}

/**
 * Resolve one already-observed forum page without a fail-open partial result.
 * ``headerResolver`` is intentionally supplied by the caller so the old v2.6
 * click/back/restore code remains the only implementation of the fallback.
 */
export async function resolvePremiumV27Page({ groups, query, pageNumber, reportedTotal, pageMembershipSha256, pagePlanPath, currentSourceUrl, checkpointDirectory, artifactRoot, routeDay, headerResolver, enableV27Pilot = false } = {}) {
  if (!Array.isArray(groups) || !groups.length) throw new Error("v2.7 page requires exact non-empty groups");
  if (typeof headerResolver !== "function") throw new Error("v2.7 page requires v2.6 header fallback resolver");
  const routeTimestamp = Date.parse(`${routeDay}T00:00:00Z`);
  if (!/^\d{4}-\d{2}-\d{2}$/.test(String(routeDay || "")) || routeDay < "2026-01-08" || !Number.isFinite(routeTimestamp) || new Date(routeTimestamp).toISOString().slice(0, 10) !== routeDay) throw new Error("v2.7 route day is invalid or historical");
  if (!artifactRoot) throw new Error("v2.7 artifact root is required");
  const expectedCheckpointDirectory = nodePath.resolve(artifactRoot, CHECKPOINT_DIRECTORY_PREFIX, routeDay, `page_${String(pageNumber).padStart(3, "0")}`);
  if (nodePath.resolve(checkpointDirectory) !== expectedCheckpointDirectory) throw new Error("v2.7 checkpoint directory is not the exact versioned route/page root");
  const exactPlanPath = nodePath.resolve(checkpointDirectory, "page_plan.json");
  if (nodePath.resolve(pagePlanPath) !== exactPlanPath) throw new Error("v2.7 page plan path is not the exact page checkpoint plan");
  const pageBytes = await fs.readFile(pagePlanPath); const pagePlanSha256 = crypto.createHash("sha256").update(pageBytes).digest("hex");
  let pagePlan = null; try { pagePlan = JSON.parse(pageBytes.toString("utf8")); } catch { throw new Error("v2.7 page plan is not readable JSON"); }
  const pagePlanBytes = pageBytes.length; const complete = new Map();
  const partition = validatePremiumV27PagePartition(groups, pagePlan, { query, pageNumber, reportedTotal, pageMembershipSha256, currentSourceUrl, parentForumChannelId: PREMIUM_ID });
  if (!partition.valid) throw new Error(`v2.7 exact full-page partition failed: ${partition.errors.join(",")}`);
  for (const groupRows of groups) {
    const input = { groupRows, query, pageNumber, pageMembershipSha256, pagePlanSha256, pagePlanBytes, currentSourceUrl };
    const key = v26.forumGroupEvidenceKey(query, pageNumber, ids(groupRows?.[0]?.forum_group_message_ids) || []);
    if (!key) throw new Error("v2.7 group membership key invalid");
    const existing = enableV27Pilot === true
      ? await readPremiumV27Checkpoint(checkpointDirectory, key, groupRows, input)
      : null;
    if (existing) {
      complete.set(key, { method: "direct_consensus_v2_7", evidence_key: key, page_number: Number(pageNumber), thread_channel_id: existing.threadChannelId,
        current_source_url: currentSourceUrl, page_plan_path: exactPlanPath, page_membership_sha256: pageMembershipSha256,
        page_plan_sha256: pagePlanSha256, page_plan_bytes: pagePlanBytes, checkpoint_path: nodePath.resolve(existing.checkpointPath),
        checkpoint_sha256: crypto.createHash("sha256").update(existing.checkpointBytes).digest("hex"), checkpoint_bytes: existing.checkpointBytes.length,
        evidence: existing.checkpoint.evidence });
      continue;
    }
    const selection = selectPremiumV27GroupResolution(input, { enableV27Pilot });
    if (selection.method === "direct_consensus_v2_7") {
      const checkpoint = await persistPremiumV27CheckpointExclusive(checkpointDirectory, selection.evidence, groupRows, input);
      const checkpointBytes = checkpoint.checkpointBytes || await fs.readFile(checkpoint.checkpointPath);
      complete.set(key, { method: selection.method, evidence_key: key, page_number: Number(pageNumber), thread_channel_id: selection.thread_channel_id,
        current_source_url: currentSourceUrl, page_plan_path: exactPlanPath, page_membership_sha256: pageMembershipSha256,
        page_plan_sha256: pagePlanSha256, page_plan_bytes: pagePlanBytes, checkpoint_path: nodePath.resolve(checkpoint.checkpointPath),
        checkpoint_sha256: crypto.createHash("sha256").update(checkpointBytes).digest("hex"), checkpoint_bytes: checkpointBytes.length,
        evidence: checkpoint.checkpoint.evidence });
    } else {
      const fallback = await headerResolver(groupRows, { query, pageNumber, pageMembershipSha256, pagePlanSha256, pagePlanBytes, currentSourceUrl, directErrors: selection.direct_errors || [] });
      const headerValidation = await validatePremiumV27HeaderFallback(fallback, groupRows, { query, pageNumber, pageMembershipSha256, pagePlanSha256, pagePlanBytes, currentSourceUrl, checkpointDirectory });
      if (!headerValidation.valid) throw new Error(`v2.7 header fallback failed strict v2.6 validation for ${key}: ${headerValidation.errors.join(",")}`);
      complete.set(key, { method: "header_navigation_v2_6", evidence_key: key, page_number: Number(pageNumber), thread_channel_id: headerValidation.thread_channel_id,
        current_source_url: currentSourceUrl, page_plan_path: exactPlanPath, page_membership_sha256: pageMembershipSha256,
        page_plan_sha256: pagePlanSha256, page_plan_bytes: pagePlanBytes, checkpoint_path: headerValidation.checkpoint_path,
        checkpoint_sha256: headerValidation.checkpoint_sha256, checkpoint_bytes: headerValidation.checkpoint_bytes,
        evidence: fallback.evidence });
    }
  }
  if (complete.size !== partition.expected_group_keys.length || partition.expected_group_keys.some((key) => !complete.has(key))) throw new Error("v2.7 page resolution is incomplete; page remains unaccepted");
  return { accepted: true, page_number: Number(pageNumber), reported_total: Number(reportedTotal),
    page_plan_path: exactPlanPath, page_membership_sha256: pageMembershipSha256,
    page_plan_sha256: pagePlanSha256, page_plan_bytes: pagePlanBytes, expected_group_evidence_keys: partition.expected_group_keys,
    resolutions: Object.fromEntries(complete) };
}

/** Convert fully accepted page results into the exact canonical navigation fields. */
export async function buildPremiumV27CanonicalNavigationFields({ pageResults, artifactRoot, routeDay } = {}) {
  if (!Array.isArray(pageResults) || !pageResults.length || !artifactRoot) throw new Error("v2.7 canonical integration requires accepted pages and artifact root");
  const routeTimestamp = Date.parse(`${routeDay}T00:00:00Z`);
  if (!/^\d{4}-\d{2}-\d{2}$/.test(String(routeDay || "")) || routeDay < "2026-01-08" || !Number.isFinite(routeTimestamp) || new Date(routeTimestamp).toISOString().slice(0, 10) !== routeDay) throw new Error("v2.7 canonical integration route day is invalid or historical");
  const reportedTotal = pageResults[0]?.reported_total;
  if (!Number.isInteger(reportedTotal) || reportedTotal < 1) throw new Error("v2.7 canonical integration reported total is invalid");
  const expectedPageCount = Math.ceil(reportedTotal / 25);
  if (pageResults.length !== expectedPageCount) throw new Error("v2.7 canonical integration does not contain the exact expected page count");
  const ordered = [...pageResults].sort((a, b) => a?.page_number - b?.page_number);
  if (ordered.some((page, index) => page?.accepted !== true || !Number.isInteger(page?.page_number) || page.page_number !== index + 1 || page?.reported_total !== reportedTotal)) throw new Error("v2.7 canonical integration pages are incomplete or non-contiguous");
  const direct = {}, header = {}, methods = {}, records = {}, plans = {}, sources = [];
  const expectedRoot = nodePath.resolve(artifactRoot, CHECKPOINT_DIRECTORY_PREFIX, routeDay);
  for (const page of ordered) {
    const planPath = nodePath.resolve(page.page_plan_path);
    if (!planPath.startsWith(expectedRoot + nodePath.sep)) throw new Error("v2.7 canonical integration plan escaped the versioned day root");
    const planBytes = await fs.readFile(planPath); const planSha = crypto.createHash("sha256").update(planBytes).digest("hex");
    const plan = JSON.parse(planBytes.toString("utf8"));
    if (planSha !== page.page_plan_sha256 || planBytes.length !== page.page_plan_bytes || plan.page_membership_sha256 !== page.page_membership_sha256) throw new Error("v2.7 canonical integration page-plan bytes drifted");
    const keys = Object.keys(page.resolutions || {}).sort();
    if (JSON.stringify(keys) !== JSON.stringify([...(page.expected_group_evidence_keys || [])].sort())) throw new Error("v2.7 canonical integration resolution key set incomplete");
    plans[String(page.page_number)] = { page_number: page.page_number, page_membership_sha256: page.page_membership_sha256,
      message_count: plan.expected_message_count, group_count: plan.expected_group_count,
      group_evidence_keys: [...page.expected_group_evidence_keys].sort(), all_rows_exact: true };
    sources.push({ role: "forum_navigation_page_plan", path: nodePath.relative(artifactRoot, planPath).split(nodePath.sep).join("/"), sha256: planSha, bytes: planBytes.length });
    for (const [key, rawRecord] of Object.entries(page.resolutions)) {
      if (methods[key]) throw new Error(`v2.7 canonical integration duplicate group key ${key}`);
      const record = { ...rawRecord,
        page_plan_path: nodePath.relative(artifactRoot, rawRecord.page_plan_path).split(nodePath.sep).join("/"),
        checkpoint_path: nodePath.relative(artifactRoot, rawRecord.checkpoint_path).split(nodePath.sep).join("/") };
      const checkpointAbsolute = nodePath.resolve(rawRecord.checkpoint_path);
      if (!checkpointAbsolute.startsWith(expectedRoot + nodePath.sep)) throw new Error("v2.7 canonical integration checkpoint escaped the versioned day root");
      const checkpointBytes = await fs.readFile(checkpointAbsolute); const checkpointSha = crypto.createHash("sha256").update(checkpointBytes).digest("hex");
      if (checkpointSha !== record.checkpoint_sha256 || checkpointBytes.length !== record.checkpoint_bytes) throw new Error("v2.7 canonical integration checkpoint bytes drifted");
      methods[key] = record.method; records[key] = record;
      if (record.method === "direct_consensus_v2_7") direct[key] = record.evidence;
      else if (record.method === "header_navigation_v2_6") header[key] = record.evidence;
      else throw new Error("v2.7 canonical integration resolution method invalid");
      sources.push({ role: record.method === "direct_consensus_v2_7" ? "forum_group_direct_consensus_checkpoint" : "forum_group_header_navigation_checkpoint",
        path: record.checkpoint_path, sha256: checkpointSha, bytes: checkpointBytes.length });
    }
  }
  sources.sort((a, b) => a.path.localeCompare(b.path) || a.role.localeCompare(b.role));
  const sourceHashPayload = sources.map((item) => ({ bytes: item.bytes, path: item.path, role: item.role, sha256: item.sha256 }));
  return { forum_group_direct_consensus_exact: direct, forum_group_header_navigation_exact: header,
    forum_group_resolution_methods: methods, forum_group_resolution_records: records,
    forum_group_navigation_page_plans: plans, forum_group_navigation_checkpoint_count: Object.keys(methods).length,
    forum_group_resolution_source_files: sources,
    forum_group_resolution_source_file_set_sha256: crypto.createHash("sha256").update(JSON.stringify(sourceHashPayload)).digest("hex") };
}
