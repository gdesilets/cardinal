import crypto from "node:crypto";
import fs from "node:fs/promises";
import nodePath from "node:path";
import { isDeepStrictEqual } from "node:util";

const GUILD_ID = "1167376964680691732";
const COLLECTOR_VERSION = "2.6";
const SEARCH_OBSERVATION_LIMIT = 8;
const REQUIRED_STABLE_EMPTY_OBSERVATIONS = 3;
const REQUIRED_STABLE_BOTTOM_OBSERVATIONS = 2;
const EXECUTED_COMMAND_AUTHOR_ID = "1211781489931452447";
const EXECUTED_COMMAND_CLASS_TOKEN = "executedCommand_c19a55";

export class DiscordSearchStateError extends Error {
  constructor(message, code = "search_state_unresolved") {
    super(message);
    this.name = "DiscordSearchStateError";
    this.code = code;
  }
}

export class DiscordResumeNavigationPending extends Error {
  constructor(currentPage, targetPage, maxSteps) {
    super(
      `Resume navigation paused at page ${currentPage} before target page ${targetPage} after ${maxSteps} bounded steps`,
    );
    this.name = "DiscordResumeNavigationPending";
    this.code = "resume_navigation_pending";
    this.currentPage = currentPage;
    this.targetPage = targetPage;
    this.maxSteps = maxSteps;
  }
}

export class DiscordExtractionBatchPending extends Error {
  constructor(pageNumber, totalPages, maxPages) {
    super(
      `Extraction batch paused after checkpointing page ${pageNumber} of ${totalPages} at the ${maxPages}-page call bound`,
    );
    this.name = "DiscordExtractionBatchPending";
    this.code = "extraction_batch_pending";
    this.pageNumber = pageNumber;
    this.totalPages = totalPages;
    this.maxPages = maxPages;
  }
}

export class DiscordForumNavigationBatchPending extends Error {
  constructor(pageNumber, completedGroups, totalGroups, maxGroupsPerCall) {
    super(
      `Forum navigation paused after checkpointing ${completedGroups} of ${totalGroups} groups ` +
        `on page ${pageNumber} at the ${maxGroupsPerCall}-group call bound`,
    );
    this.name = "DiscordForumNavigationBatchPending";
    this.code = "forum_navigation_batch_pending";
    this.pageNumber = pageNumber;
    this.completedGroups = completedGroups;
    this.totalGroups = totalGroups;
    this.maxGroupsPerCall = maxGroupsPerCall;
  }
}

export class DiscordPageValidationError extends Error {
  constructor(pageNumber, validation, attempts) {
    super(
      `Page ${pageNumber} extraction failed validation after ${attempts} attempts: ` +
        `captured=${validation.captured_count}/${validation.expected_count}, ` +
        `missing=${validation.missing_indices.slice(0, 20).join(",")}, ` +
        `unexpected=${validation.unexpected_indices.slice(0, 20).join(",")}, ` +
        `duplicate_ids=${validation.duplicate_message_ids.slice(0, 20).join(",")}, ` +
        `overlap_ids=${validation.overlap_message_ids.slice(0, 20).join(",")}`,
    );
    this.name = "DiscordPageValidationError";
    this.code = "page_validation_failed";
    this.pageNumber = pageNumber;
    this.validation = validation;
    this.attempts = attempts;
  }
}

export function classifySearchPanelText(value) {
  const text = String(value || "").trim();
  if (
    /dropped the magnifying glass|try searching again|something went wrong|too many requests|rate.?limit|temporarily unavailable/i.test(
      text,
    )
  ) {
    return "error";
  }
  if (/searching(?:\u2026|\.\.\.)?|loading/i.test(text)) return "pending";
  if (/no results/i.test(text)) return "empty_candidate";
  return "unknown";
}

function isIsoTimestamp(value) {
  return typeof value === "string" && value.endsWith("Z") && Number.isFinite(Date.parse(value));
}

export function validateCompletionEvidence(evidence, query, total, pages) {
  const errors = [];
  if (!evidence || typeof evidence !== "object") return { valid: false, errors: ["completion_evidence_missing"] };
  if (evidence.schema_version !== "1.0.0") errors.push("completion_evidence_schema_invalid");
  if (evidence.query !== query) errors.push("completion_evidence_query_mismatch");
  if (evidence.reported_total !== total) errors.push("completion_evidence_total_mismatch");
  if (evidence.reported_pages !== pages) errors.push("completion_evidence_pages_mismatch");
  const submission = evidence.search_submission;
  if (!submission || typeof submission !== "object") {
    errors.push("search_submission_evidence_missing");
  } else {
    if (submission.query !== query) errors.push("search_submission_query_mismatch");
    if (!isIsoTimestamp(submission.submitted_at_utc || submission.observed_at_utc)) {
      errors.push("search_submission_timestamp_invalid");
    }
  }
  if (total === 0) {
    if (evidence.terminal_state !== "stable_empty") errors.push("terminal_state_not_stable_empty");
    if (submission?.mode !== "fresh" || submission?.submission_count !== 1) {
      errors.push("stable_empty_requires_one_fresh_submission");
    }
    const stableEmpty = evidence.stable_empty;
    const observations = Array.isArray(stableEmpty?.observations) ? stableEmpty.observations : [];
    if (stableEmpty?.required_observations !== REQUIRED_STABLE_EMPTY_OBSERVATIONS) {
      errors.push("stable_empty_required_count_invalid");
    }
    if (observations.length !== REQUIRED_STABLE_EMPTY_OBSERVATIONS) {
      errors.push("stable_empty_observation_count_invalid");
    }
    observations.forEach((observation, index) => {
      if (observation?.sequence !== index + 1) errors.push("stable_empty_sequence_invalid");
      if (observation?.state !== "empty_candidate") errors.push("stable_empty_state_invalid");
      if (observation?.visible_result_count !== 0) errors.push("stable_empty_visible_count_nonzero");
      if (!isIsoTimestamp(observation?.observed_at_utc)) errors.push("stable_empty_timestamp_invalid");
      if (!/no results/i.test(String(observation?.panel_text || ""))) {
        errors.push("stable_empty_panel_text_invalid");
      }
    });
  } else {
    if (evidence.terminal_state !== "stable_bottom") errors.push("terminal_state_not_stable_bottom");
    const stableBottom = evidence.stable_bottom;
    const observations = Array.isArray(stableBottom?.observations) ? stableBottom.observations : [];
    if (stableBottom?.required_observations !== REQUIRED_STABLE_BOTTOM_OBSERVATIONS) {
      errors.push("stable_bottom_required_count_invalid");
    }
    if (observations.length !== REQUIRED_STABLE_BOTTOM_OBSERVATIONS) {
      errors.push("stable_bottom_observation_count_invalid");
    }
    const expectedFirst = (pages - 1) * 25 + 1;
    const expectedVisible = total - expectedFirst + 1;
    observations.forEach((observation, index) => {
      if (observation?.sequence !== index + 1) errors.push("stable_bottom_sequence_invalid");
      if (!isIsoTimestamp(observation?.observed_at_utc)) errors.push("stable_bottom_timestamp_invalid");
      if (observation?.query !== query) errors.push("stable_bottom_query_mismatch");
      if (observation?.current_page !== pages) errors.push("stable_bottom_page_mismatch");
      if (observation?.first_result_index !== expectedFirst) errors.push("stable_bottom_first_index_mismatch");
      if (observation?.last_result_index !== total) errors.push("stable_bottom_last_index_mismatch");
      if (observation?.visible_result_count !== expectedVisible) errors.push("stable_bottom_visible_count_mismatch");
      if (observation?.result_set_size !== total) errors.push("stable_bottom_total_mismatch");
      if (observation?.has_enabled_next !== false) errors.push("stable_bottom_next_disabled_not_proven");
    });
  }
  return { valid: errors.length === 0, errors: Array.from(new Set(errors)) };
}

export async function observeStableBottom(tab, query, total, pages, options = {}) {
  if (!Number.isInteger(total) || total <= 0 || !Number.isInteger(pages) || pages <= 0) {
    throw new Error("Stable-bottom observation requires positive total and page counts");
  }
  const delayMs = Number(options.stableBottomObservationDelayMs ?? 500);
  if (!Number.isFinite(delayMs) || delayMs < 0) throw new Error("stableBottomObservationDelayMs must be non-negative");
  const observations = [];
  for (let sequence = 1; sequence <= REQUIRED_STABLE_BOTTOM_OBSERVATIONS; sequence += 1) {
    if (sequence > 1 && delayMs > 0) await tab.playwright.waitForTimeout(delayMs);
    await tab.playwright.domSnapshot();
    const observed = await tab.playwright.evaluate(() => {
      const observationKind = "stable_bottom_dom_observation";
      const searchBox = document.querySelector('[role="combobox"][aria-label="Search"]');
      const observedQuery = searchBox
        ? String(searchBox.value || searchBox.getAttribute("value") || searchBox.textContent || "").trim()
        : "";
      const region = document.querySelector('[aria-label="Search Results"]');
      const items = Array.from(region?.querySelectorAll('[role="listitem"]') || []);
      const indices = items
        .map((item) => Number(item.getAttribute("aria-posinset") || 0))
        .filter((value) => Number.isInteger(value) && value > 0)
        .sort((a, b) => a - b);
      const totals = Array.from(
        new Set(
          items
            .map((item) => Number(item.getAttribute("aria-setsize") || 0))
            .filter((value) => Number.isInteger(value) && value >= 0),
        ),
      );
      const nextControls = Array.from(document.querySelectorAll('button,[role="button"]')).filter((node) => {
        const label = `${node.getAttribute("aria-label") || ""} ${node.textContent || ""}`.trim();
        return /^next(?:\s|$)/i.test(label);
      });
      const hasEnabledNext = nextControls.some(
        (node) =>
          !node.hasAttribute("disabled") &&
          node.getAttribute("aria-disabled") !== "true" &&
          !node.disabled,
      );
      const firstResultIndex = indices[0] || 0;
      return {
        observation_kind: observationKind,
        query: observedQuery,
        visible_result_count: indices.length,
        first_result_index: firstResultIndex,
        last_result_index: indices.at(-1) || 0,
        current_page: firstResultIndex > 0 ? Math.floor((firstResultIndex - 1) / 25) + 1 : 0,
        result_set_size: totals.length === 1 ? totals[0] : null,
        result_set_size_candidates: totals,
        has_enabled_next: hasEnabledNext,
        panel_text: (region?.innerText || "").slice(0, 300),
      };
    });
    observations.push({ sequence, observed_at_utc: new Date().toISOString(), ...observed });
  }
  const stableBottom = {
    required_observations: REQUIRED_STABLE_BOTTOM_OBSERVATIONS,
    observations,
  };
  const evidence = {
    schema_version: "1.0.0",
    query,
    reported_total: total,
    reported_pages: pages,
    terminal_state: "stable_bottom",
    search_submission: options.searchSubmission || null,
    search_observations: Array.isArray(options.searchObservations) ? options.searchObservations : [],
    stable_empty: null,
    stable_bottom: stableBottom,
  };
  const validation = validateCompletionEvidence(evidence, query, total, pages);
  if (!validation.valid) {
    throw new DiscordSearchStateError(
      `Stable-bottom evidence failed validation: ${validation.errors.join(",")}`,
      "stable_bottom_unverified",
    );
  }
  return evidence;
}

export function isThrottleLikeError(error) {
  const text = `${error?.code || ""} ${error?.message || error || ""}`;
  return /search_(?:error|state_unresolved)|searching|magnifying glass|try searching again|rate.?limit|too many requests|temporarily unavailable|selector deadline exceeded|waitfor\(visible\).*timed out|no numbered page controls|search page navigation lost results/i.test(
    text,
  );
}

export function choosePaginationStep(currentPage, targetPage, pageNumbers) {
  const visible = Array.from(new Set(pageNumbers || []))
    .filter((page) => Number.isInteger(page) && page > 0 && page !== currentPage)
    .sort((a, b) => a - b);
  if (visible.includes(targetPage)) return targetPage;
  const direction = Math.sign(targetPage - currentPage);
  const adjacent = currentPage + direction;
  if (visible.includes(adjacent)) return adjacent;
  const directional = visible.filter((page) => (direction > 0 ? page > currentPage : page < currentPage));
  if (directional.length === 0) return null;
  return direction > 0 ? directional[0] : directional.at(-1);
}

export function choosePaginationControl(currentPage, targetPage, pageNumbers, hasNext, hasBack) {
  const visible = new Set(pageNumbers || []);
  if (visible.has(targetPage)) {
    return { nextPage: targetPage, accessibleName: `Page ${targetPage}`, kind: "numbered" };
  }
  if (targetPage > currentPage && hasNext) {
    return { nextPage: currentPage + 1, accessibleName: "Next", kind: "adjacent" };
  }
  if (targetPage < currentPage && hasBack) {
    return { nextPage: currentPage - 1, accessibleName: "Back", kind: "adjacent" };
  }
  return null;
}

function segmentPaths(segment, outputDirectory, options = {}) {
  const stem = `${options.prefix || "primary"}_${segment.start}_${segment.end}`;
  return {
    stem,
    partialPath: `${outputDirectory}/${stem}.partial.json`,
    finalPath: `${outputDirectory}/${stem}.json`,
  };
}

async function readJsonIfPresent(path) {
  try {
    return JSON.parse(await fs.readFile(path, "utf8"));
  } catch (error) {
    if (error?.code === "ENOENT") return null;
    throw error;
  }
}

async function writeJsonAtomic(path, payload) {
  const temporaryPath = `${path}.next-${Date.now()}-${Math.random().toString(16).slice(2)}`;
  try {
    await fs.writeFile(temporaryPath, JSON.stringify(payload));
    await fs.rename(temporaryPath, path);
  } catch (error) {
    await fs.unlink(temporaryPath).catch(() => {});
    throw error;
  }
}

function completionEvidenceSidecarPath(artifactPath) {
  return String(artifactPath).replace(/\.json$/i, ".completion-evidence.json");
}

async function readJsonFileState(filePath) {
  try {
    return { exists: true, value: JSON.parse(await fs.readFile(filePath, "utf8")) };
  } catch (error) {
    if (error?.code === "ENOENT") return { exists: false, value: null };
    throw error;
  }
}

async function resolveExistingCompletionEvidence(existingFinal, finalPath) {
  const sidecarPath = completionEvidenceSidecarPath(finalPath);
  const sidecarState = await readJsonFileState(sidecarPath);
  if (existingFinal?.completion_evidence && typeof existingFinal.completion_evidence === "object") {
    if (sidecarState.exists) {
      throw new Error(`Inline and sidecar completion evidence are ambiguous: ${finalPath}`);
    }
    return existingFinal.completion_evidence;
  }
  if (!sidecarState.exists) return null;
  const sidecar = sidecarState.value;
  const validBinding =
    sidecar &&
    typeof sidecar === "object" &&
    sidecar.artifact_type === "discord_segment_completion_evidence_sidecar" &&
    sidecar.schema_version === "1.0.0" &&
    sidecar.source_artifact_path === nodePath.basename(String(finalPath)) &&
    sidecar.source_artifact_sha256 === (await sha256File(finalPath)) &&
    sidecar.guild_id === existingFinal.guild_id &&
    sidecar.requested_container &&
    typeof sidecar.requested_container === "object" &&
    !Array.isArray(sidecar.requested_container) &&
    existingFinal.requested_container &&
    typeof existingFinal.requested_container === "object" &&
    !Array.isArray(existingFinal.requested_container) &&
    isDeepStrictEqual(sidecar.requested_container, existingFinal.requested_container) &&
    isDeepStrictEqual(sidecar.segment, existingFinal.segment) &&
    sidecar.reported_total === existingFinal.reported_total &&
    sidecar.reported_pages === existingFinal.reported_pages;
  if (!validBinding) {
    throw new Error(`Completion-evidence sidecar binding is invalid: ${sidecarPath}`);
  }
  return sidecar.completion_evidence;
}

async function summarizeExistingComplete(existingFinal, segment, finalPath, options = {}) {
  if (!existingFinal) return null;
  const existingRows = Array.isArray(existingFinal.messages) ? existingFinal.messages : [];
  const existingTotal = Number(existingFinal.reported_total);
  const existingValidation = validateRows(existingRows, Number.isInteger(existingTotal) ? existingTotal : 0);
  const existingPages = Number(existingFinal.reported_pages || Math.ceil(existingTotal / 25));
  const completionEvidence = await resolveExistingCompletionEvidence(existingFinal, finalPath);
  const completionValidation = validateCompletionEvidence(
    completionEvidence,
    segment.query,
    existingTotal,
    existingPages,
  );
  const requested = existingFinal.requested_container || {};
  const forumNavigationCompatible =
    !requiresForumGroupNavigationEvidence(options) ||
    existingRows.every((row) => rowHasExactForumGroupNavigationEvidence(row, options));
  const compatible =
    existingFinal.complete === true &&
    existingFinal.segment?.start === segment.start &&
    existingFinal.segment?.end === segment.end &&
    existingFinal.segment?.query === segment.query &&
    existingFinal.segment?.timezone === segment.timezone &&
    Number.isInteger(existingTotal) &&
    existingTotal >= 0 &&
    existingRows.length === existingTotal &&
    existingValidation.unique === existingTotal &&
    existingValidation.gaps.length === 0 &&
    completionValidation.valid &&
    forumNavigationCompatible &&
    (!options.channelId || requested.channel_id === options.channelId) &&
    (!options.channelName || requested.channel_name === options.channelName);
  if (!compatible) {
    throw new Error(`Refusing to overwrite incompatible complete artifact: ${finalPath}`);
  }
  return {
    start: segment.start,
    end: segment.end,
    reported: existingTotal,
    captured: existingRows.length,
    unique: existingValidation.unique,
    gaps: existingValidation.gaps.length,
    pages: existingPages,
    finalPath,
    skipped_existing_complete: true,
  };
}

function isoDay(date) {
  return date.toISOString().slice(0, 10);
}

export function makeSegments(
  startIso,
  endIso,
  spanDays = 7,
  queryPrefix = "in:premium-journals",
  timezone = "America/Chicago",
) {
  const start = new Date(`${startIso}T00:00:00Z`);
  const end = new Date(`${endIso}T00:00:00Z`);
  const segments = [];
  for (let cursor = start; cursor <= end; cursor = new Date(cursor.getTime() + spanDays * 86400000)) {
    const segmentEnd = new Date(Math.min(cursor.getTime() + (spanDays - 1) * 86400000, end.getTime()));
    const after = new Date(cursor.getTime() - 86400000);
    const before = new Date(segmentEnd.getTime() + 86400000);
    segments.push({
      start: isoDay(cursor),
      end: isoDay(segmentEnd),
      query: `${queryPrefix} after:${isoDay(after)} before:${isoDay(before)}`.trim(),
      timezone,
    });
  }
  return segments;
}

export function deriveDiscordSnowflakeFields(row) {
  let snowflakeTimestampUtc = null;
  let timestampDiscrepancyMs = null;
  try {
    const snowflakeMs = Number((BigInt(String(row?.message_id || "")) >> 22n) + 1420070400000n);
    if (Number.isFinite(snowflakeMs)) {
      snowflakeTimestampUtc = new Date(snowflakeMs).toISOString();
      const displayedMs = Date.parse(String(row?.timestamp_utc || ""));
      if (Number.isFinite(displayedMs)) timestampDiscrepancyMs = displayedMs - snowflakeMs;
    }
  } catch {}
  return {
    ...row,
    snowflake_timestamp_utc: snowflakeTimestampUtc,
    timestamp_discrepancy_ms: timestampDiscrepancyMs,
  };
}

export function deriveDiscordSystemEventFields(row, channelKind = null) {
  const messageId = /^\d{15,22}$/.test(String(row?.message_id || ""))
    ? String(row.message_id)
    : null;
  const exactChannelKind = String(channelKind || row?.collection_channel_kind || "");
  const contentLines = String(row?.content_text || "")
    .split(/\r?\n/)
    .map((value) => value.trim())
    .filter(Boolean);
  const duplicatedStageSpeakerLabel = Boolean(
    contentLines.length >= 4 &&
      contentLines[0] &&
      contentLines[0] === contentLines[1],
  );
  const eventLine = contentLines[duplicatedStageSpeakerLabel ? 2 : 1] || "";
  const stageEventMatch = eventLine.match(/^(?:(started|ended)\s+(.+)|is now a speaker\.)$/i);
  const pollClosedMatch = (contentLines[0] || "").match(/^.+['’]s poll .+ has closed\.$/i);
  const pollResultsPresent =
    (contentLines.some((line) => /^The results?\b/i.test(line)) &&
      contentLines.some((line) => /^\d+(?:\.\d+)?%$/.test(line))) ||
    contentLines.some((line) => /^Winning answer • \d+(?:\.\d+)?%$/i.test(line));
  const labelledBy = String(row?.article_aria_labelledby || "").trim();
  const allowedLabelSets = new Set([
    `message-content-${messageId}`,
    `message-content-${messageId} message-accessories-${messageId}`,
  ]);
  const timestampUtc = String(row?.timestamp_utc || "");
  const snowflakeTimestampUtc = String(row?.snowflake_timestamp_utc || "");
  const exactPinnedMessageGrammar = Boolean(
    contentLines.length === 5 &&
      contentLines[0].length >= 1 &&
      contentLines[0].length <= 80 &&
      contentLines[1] === "pinned a message to this channel. See all pinned messages." &&
      contentLines[2] === "\u2014" &&
      /^\d{1,2}\/\d{1,2}\/\d{2}, \d{1,2}:\d{2} (?:AM|PM)$/.test(contentLines[3]) &&
      /^(?:Monday|Tuesday|Wednesday|Thursday|Friday|Saturday|Sunday), (?:January|February|March|April|May|June|July|August|September|October|November|December) \d{1,2}, \d{4} at \d{1,2}:\d{2} (?:AM|PM)$/.test(
        contentLines[4],
      ),
  );
  const exactPinnedMessageSystemEvent = Boolean(
    messageId &&
      exactChannelKind === "text channel" &&
      !String(row?.author || "").trim() &&
      !String(row?.author_id || "").trim() &&
      row?.article_id === `search-result-${messageId}` &&
      row?.content_scope_exact === true &&
      row?.timestamp_scope_exact === false &&
      labelledBy === `message-content-${messageId}` &&
      exactPinnedMessageGrammar &&
      Number.isInteger(row?.row_owned_time_count) &&
      row.row_owned_time_count === 1 &&
      !String(row?.row_owned_time_element_id || "").trim() &&
      String(row?.row_owned_time_datetime || "") === timestampUtc &&
      timestampUtc &&
      timestampUtc === snowflakeTimestampUtc &&
      row?.timestamp_discrepancy_ms === 0,
  );
  const exactStageSystemEvent = Boolean(
    messageId &&
      exactChannelKind === "stage channel" &&
      !String(row?.author || "").trim() &&
      !String(row?.author_id || "").trim() &&
      row?.content_scope_exact === true &&
      row?.timestamp_scope_exact === false &&
      allowedLabelSets.has(labelledBy) &&
      contentLines.length >= (duplicatedStageSpeakerLabel ? 4 : 3) &&
      contentLines[0] &&
      (stageEventMatch || (pollClosedMatch && pollResultsPresent)) &&
      timestampUtc &&
      timestampUtc === snowflakeTimestampUtc &&
      row?.timestamp_discrepancy_ms === 0,
  );
  if (!exactStageSystemEvent && !exactPinnedMessageSystemEvent) {
    return {
      ...row,
      discord_system_event_exact: false,
      discord_system_event_type: null,
      timestamp_exact_fallback_source: null,
    };
  }
  if (exactPinnedMessageSystemEvent) {
    return {
      ...row,
      message_kind: "discord_pinned_message_system_event",
      discord_system_event_exact: true,
      discord_system_event_type: "message_pinned",
      timestamp_exact_fallback_source: "discord_snowflake_exact_pinned_message_system_event",
    };
  }
  const eventType = pollClosedMatch
    ? "poll_closed"
    : stageEventMatch[1]
      ? `stage_${stageEventMatch[1].toLowerCase()}`
      : "stage_speaker_added";
  return {
    ...row,
    message_kind: pollClosedMatch ? "discord_poll_system_event" : "discord_stage_system_event",
    discord_system_event_exact: true,
    discord_system_event_type: eventType,
    timestamp_exact_fallback_source: "discord_snowflake_exact_stage_system_event",
  };
}

export function deriveDiscordAuthorFields(row) {
  const avatarUrl = String(row?.author_avatar_url || "").trim();
  const standardAvatarMatch = avatarUrl.match(/\/avatars\/(\d{15,22})\/[^/?#]+/);
  const guildAvatarMatch = avatarUrl.match(
    /\/guilds\/(\d{15,22})\/users\/(\d{15,22})\/avatars\/[^/?#]+/,
  );
  const existingAuthorId = /^\d{15,22}$/.test(String(row?.author_id || ""))
    ? String(row.author_id)
    : null;
  const exactUsernameAuthorId =
    existingAuthorId && row?.author_id_source === "exact_username_bound_data_user_id"
      ? existingAuthorId
      : null;
  const authorIdConflict = row?.author_id_conflict === true;
  const selectedAuthorId = authorIdConflict
    ? null
    : exactUsernameAuthorId || standardAvatarMatch?.[1] || guildAvatarMatch?.[2] || existingAuthorId;
  return {
    ...row,
    author_id: selectedAuthorId,
    author_avatar_guild_id: guildAvatarMatch?.[1] || null,
    author_id_source: authorIdConflict
      ? null
      : exactUsernameAuthorId
        ? "exact_username_bound_data_user_id"
        : standardAvatarMatch
          ? "owner_scoped_avatar_cdn_path"
          : guildAvatarMatch
            ? "owner_scoped_guild_avatar_cdn_path"
            : existingAuthorId
              ? String(row?.author_id_source || "legacy_exact_author_id")
              : null,
  };
}

export function deriveDiscordAttachmentFields(row, ownerChannelId = null) {
  const exactOwnerChannelId = /^\d{15,22}$/.test(String(ownerChannelId || ""))
    ? String(ownerChannelId)
    : null;
  const ownerMessageId = /^\d{15,22}$/.test(String(row?.message_id || ""))
    ? String(row.message_id)
    : null;
  const attachments = Array.isArray(row?.attachments)
    ? row.attachments.map((attachment) => {
        if (!attachment || typeof attachment !== "object") return attachment;
        const sourceChannelId = /^\d{15,22}$/.test(String(attachment.thread_channel_id || ""))
          ? String(attachment.thread_channel_id)
          : null;
        const domRelation = String(attachment.dom_relation || "unresolved");
        let relationType = "unresolved";
        let ownershipStatus = "unresolved";
        let basis = "exact_message_ownership_not_proven";
        let exact = false;
        if (exactOwnerChannelId && sourceChannelId && sourceChannelId !== exactOwnerChannelId) {
          relationType = "embedded_external";
          ownershipStatus = "non_owned_exact";
          basis = "discord_cdn_source_channel_differs_from_exact_message_container";
          exact = true;
        } else if (
          exactOwnerChannelId &&
          sourceChannelId === exactOwnerChannelId &&
          domRelation === "exact_message_accessories_descendant" &&
          attachment.href_in_message_content === false
        ) {
          relationType = "owned";
          ownershipStatus = "owned_exact";
          basis = "exact_message_accessories_descendant_and_matching_cdn_channel";
          exact = true;
        } else if (
          domRelation === "message_content_link" ||
          domRelation === "embed_descendant" ||
          attachment.href_in_message_content === true
        ) {
          relationType = "embedded_external";
          ownershipStatus = "non_owned_exact";
          basis = "row_owned_content_or_embed_link_not_uploaded_attachment";
          exact = true;
        }
        return {
          ...attachment,
          relation_type: relationType,
          ownership_status: ownershipStatus,
          ownership_evidence: {
            schema_version: "1.0.0",
            exact,
            basis,
            owner_message_id: ownerMessageId,
            owner_channel_id: exactOwnerChannelId,
            source_channel_id: sourceChannelId,
            dom_relation: domRelation,
          },
        };
      })
    : row?.attachments;
  return { ...row, attachments };
}

function normalizedForumGroupMessageIds(value) {
  if (!Array.isArray(value) || value.length === 0) return null;
  const ids = value.map((item) => String(item || ""));
  if (ids.some((item) => !/^\d{15,22}$/.test(item))) return null;
  const unique = Array.from(new Set(ids));
  if (unique.length !== ids.length) return null;
  return unique.sort();
}

export function forumGroupEvidenceKey(query, pageNumber, messageIds) {
  const exactQuery = String(query || "").trim();
  const exactPage = Number(pageNumber);
  const exactMessageIds = normalizedForumGroupMessageIds(messageIds);
  if (!exactQuery || !Number.isInteger(exactPage) || exactPage < 1 || !exactMessageIds) return null;
  const fingerprint = JSON.stringify({
    query: exactQuery,
    page_number: exactPage,
    group_message_ids: exactMessageIds,
  });
  return `forum-group-navigation:${crypto.createHash("sha256").update(fingerprint).digest("hex")}`;
}

export function forumGroupMembershipSha256(query, pageNumber, messageIds) {
  const exactQuery = String(query || "").trim();
  const exactPage = Number(pageNumber);
  const exactMessageIds = normalizedForumGroupMessageIds(messageIds);
  if (!exactQuery || !Number.isInteger(exactPage) || exactPage < 1 || !exactMessageIds) return null;
  return crypto
    .createHash("sha256")
    .update(
      JSON.stringify({
        query: exactQuery,
        page_number: exactPage,
        group_message_ids: exactMessageIds,
      }),
    )
    .digest("hex");
}

export function deriveDiscordForumGroupMembershipFields(row) {
  const messageIds = normalizedForumGroupMessageIds(row?.forum_group_message_ids);
  const messageId = /^\d{15,22}$/.test(String(row?.message_id || ""))
    ? String(row.message_id)
    : null;
  const exact = Boolean(
    row?.forum_group_membership_exact === true &&
      messageIds &&
      messageId &&
      messageIds.includes(messageId),
  );
  return {
    ...row,
    forum_group_message_ids: messageIds || [],
    forum_group_membership_exact: exact,
    forum_group_membership_key: exact
      ? forumGroupEvidenceKey(row?.search_query, row?.page_number, messageIds)
      : null,
  };
}

function parseExactDiscordThreadDestination(value) {
  try {
    const parsed = new URL(String(value || ""));
    const pathParts = parsed.pathname.split("/").filter(Boolean);
    if (
      parsed.protocol !== "https:" ||
      !["discord.com", "www.discord.com"].includes(parsed.hostname) ||
      parsed.search ||
      parsed.hash ||
      pathParts.length !== 3 ||
      pathParts[0] !== "channels" ||
      !/^\d{15,22}$/.test(pathParts[1]) ||
      !/^\d{15,22}$/.test(pathParts[2])
    ) {
      return null;
    }
    return { guild_id: pathParts[1], thread_channel_id: pathParts[2] };
  } catch {
    return null;
  }
}

function parseExactDiscordGuildNavigationUrl(value) {
  try {
    const parsed = new URL(String(value || ""));
    const pathParts = parsed.pathname.split("/").filter(Boolean);
    if (
      parsed.protocol !== "https:" ||
      !["discord.com", "www.discord.com"].includes(parsed.hostname) ||
      parsed.search ||
      parsed.hash ||
      ![3, 4].includes(pathParts.length) ||
      pathParts[0] !== "channels" ||
      !/^\d{15,22}$/.test(pathParts[1]) ||
      !/^\d{15,22}$/.test(pathParts[2]) ||
      (pathParts.length === 4 && !/^\d{15,22}$/.test(pathParts[3]))
    ) {
      return null;
    }
    return {
      guild_id: pathParts[1],
      channel_id: pathParts[2],
      message_id: pathParts[3] || null,
      url: parsed.href,
    };
  } catch {
    return null;
  }
}

export function buildForumGroupHeaderNavigationEvidence({
  query,
  pageNumber,
  messageIds,
  parentForumChannelId,
  sourceUrl,
  destinationUrl,
  backUrl,
  restoredQuery,
  restoredPageNumber,
  restoredGroupMessageIds,
  preNavigationPageMembershipSha256,
  restoredPageMembershipSha256,
  observedAtUtc = new Date().toISOString(),
  headerMatchCount = 1,
  headerButtonMatchCount = 1,
  returnStateVerified = true,
} = {}) {
  const normalizedMessageIds = normalizedForumGroupMessageIds(messageIds) || [];
  const normalizedRestoredMessageIds =
    normalizedForumGroupMessageIds(restoredGroupMessageIds) || [];
  const exactSourceUrl = String(sourceUrl || "");
  const exactBackUrl = String(backUrl || "");
  const destination = parseExactDiscordThreadDestination(destinationUrl);
  const source = parseExactDiscordGuildNavigationUrl(exactSourceUrl);
  const back = parseExactDiscordGuildNavigationUrl(exactBackUrl);
  const exactParentForumChannelId = String(parentForumChannelId || "");
  return {
    schema_version: "1.1.0",
    evidence_type: "forum_group_header_navigation_exact",
    evidence_key: forumGroupEvidenceKey(query, pageNumber, normalizedMessageIds),
    guild_id: GUILD_ID,
    parent_forum_channel_id: String(parentForumChannelId || "") || null,
    query: String(query || "").trim(),
    page_number: Number(pageNumber),
    group_message_ids: normalizedMessageIds,
    navigation_trigger: "unique_direct_child_role_button_click",
    header_match_count: Number(headerMatchCount),
    header_button_match_count: Number(headerButtonMatchCount),
    source_url: exactSourceUrl,
    source_parent_forum_channel_id: source?.channel_id || null,
    source_parent_forum_verified: Boolean(
      source &&
        source.guild_id === GUILD_ID &&
        source.channel_id === exactParentForumChannelId &&
        source.message_id === null,
    ),
    destination_url: String(destinationUrl || ""),
    destination_guild_id: destination?.guild_id || null,
    thread_channel_id: destination?.thread_channel_id || null,
    destination_verified: Boolean(
      destination && destination.guild_id === GUILD_ID && destination.thread_channel_id,
    ),
    back_url: exactBackUrl,
    back_parent_forum_verified: Boolean(
      back &&
        back.guild_id === GUILD_ID &&
        back.channel_id === exactParentForumChannelId &&
        back.message_id === null,
    ),
    source_url_restored: Boolean(exactSourceUrl && exactBackUrl === exactSourceUrl),
    restored_query: String(restoredQuery || "").trim(),
    restored_page_number: Number(restoredPageNumber),
    restored_group_message_ids: normalizedRestoredMessageIds,
    restored_group_membership_sha256: forumGroupMembershipSha256(
      restoredQuery,
      restoredPageNumber,
      normalizedRestoredMessageIds,
    ),
    pre_navigation_page_membership_sha256:
      String(preNavigationPageMembershipSha256 || "") || null,
    restored_page_membership_sha256:
      String(restoredPageMembershipSha256 || "") || null,
    page_plan_verified: Boolean(
      /^[a-f0-9]{64}$/.test(String(preNavigationPageMembershipSha256 || "")) &&
        restoredPageMembershipSha256 === preNavigationPageMembershipSha256,
    ),
    return_state_verified: returnStateVerified === true,
    observed_at_utc: observedAtUtc,
    authenticated: true,
    source_scope: "discord_only",
    outside_sources_used: false,
  };
}

export function validateForumGroupHeaderNavigationEvidence(evidence, row, options = {}) {
  const errors = [];
  const expected = deriveDiscordForumGroupMembershipFields(row || {});
  const expectedKey = expected.forum_group_membership_key;
  const expectedMessageIds = expected.forum_group_message_ids;
  const parentForumChannelId = String(
    options.parentForumChannelId || evidence?.parent_forum_channel_id || "",
  );
  const evidenceMessageIds = normalizedForumGroupMessageIds(evidence?.group_message_ids);
  const restoredMessageIds = normalizedForumGroupMessageIds(
    evidence?.restored_group_message_ids,
  );
  if (!evidence || typeof evidence !== "object" || Array.isArray(evidence)) {
    return { valid: false, errors: ["forum_navigation_evidence_missing"], evidence_key: expectedKey };
  }
  if (evidence.schema_version !== "1.1.0") errors.push("forum_navigation_schema_invalid");
  if (evidence.evidence_type !== "forum_group_header_navigation_exact") {
    errors.push("forum_navigation_type_invalid");
  }
  if (!expected.forum_group_membership_exact || !expectedKey) {
    errors.push("forum_group_row_membership_not_exact");
  }
  if (evidence.evidence_key !== expectedKey) errors.push("forum_navigation_evidence_key_mismatch");
  if (evidence.query !== expected.search_query) errors.push("forum_navigation_query_mismatch");
  if (Number(evidence.page_number) !== Number(expected.page_number)) {
    errors.push("forum_navigation_page_mismatch");
  }
  if (
    !evidenceMessageIds ||
    JSON.stringify(evidenceMessageIds) !== JSON.stringify(expectedMessageIds)
  ) {
    errors.push("forum_navigation_group_membership_mismatch");
  }
  if (evidence.navigation_trigger !== "unique_direct_child_role_button_click") {
    errors.push("forum_navigation_trigger_invalid");
  }
  if (evidence.header_match_count !== 1 || evidence.header_button_match_count !== 1) {
    errors.push("forum_navigation_header_not_unique");
  }
  if (evidence.authenticated !== true || evidence.source_scope !== "discord_only") {
    errors.push("forum_navigation_not_authenticated_discord_only");
  }
  if (evidence.outside_sources_used !== false) errors.push("forum_navigation_outside_source_flag_invalid");
  if (evidence.return_state_verified !== true) errors.push("forum_navigation_return_state_unverified");
  const source = parseExactDiscordGuildNavigationUrl(evidence.source_url);
  const back = parseExactDiscordGuildNavigationUrl(evidence.back_url);
  if (!source || source.guild_id !== GUILD_ID) errors.push("forum_navigation_source_url_invalid");
  if (!back || back.guild_id !== GUILD_ID) errors.push("forum_navigation_back_url_invalid");
  if (
    !/^\d{15,22}$/.test(parentForumChannelId) ||
    !source ||
    source.channel_id !== parentForumChannelId ||
    source.message_id !== null ||
    evidence.source_parent_forum_channel_id !== parentForumChannelId ||
    evidence.source_parent_forum_verified !== true
  ) {
    errors.push("forum_navigation_source_not_parent_forum");
  }
  if (
    !back ||
    back.channel_id !== parentForumChannelId ||
    back.message_id !== null ||
    evidence.back_parent_forum_verified !== true
  ) {
    errors.push("forum_navigation_back_not_parent_forum");
  }
  if (
    evidence.source_url_restored !== true ||
    !source ||
    !back ||
    evidence.back_url !== evidence.source_url
  ) {
    errors.push("forum_navigation_source_url_not_restored");
  }
  if (evidence.restored_query !== expected.search_query) {
    errors.push("forum_navigation_restored_query_mismatch");
  }
  if (Number(evidence.restored_page_number) !== Number(expected.page_number)) {
    errors.push("forum_navigation_restored_page_mismatch");
  }
  if (
    !restoredMessageIds ||
    JSON.stringify(restoredMessageIds) !== JSON.stringify(expectedMessageIds)
  ) {
    errors.push("forum_navigation_restored_membership_mismatch");
  }
  if (
    evidence.restored_group_membership_sha256 !==
    forumGroupMembershipSha256(
      expected.search_query,
      expected.page_number,
      expectedMessageIds,
    )
  ) {
    errors.push("forum_navigation_restored_membership_hash_mismatch");
  }
  if (
    !/^[a-f0-9]{64}$/.test(String(evidence.pre_navigation_page_membership_sha256 || "")) ||
    evidence.restored_page_membership_sha256 !==
      evidence.pre_navigation_page_membership_sha256 ||
    evidence.page_plan_verified !== true
  ) {
    errors.push("forum_navigation_page_membership_hash_mismatch");
  }
  if (
    options.pageMembershipSha256 &&
    evidence.pre_navigation_page_membership_sha256 !== options.pageMembershipSha256
  ) {
    errors.push("forum_navigation_page_plan_binding_mismatch");
  }
  if (!Number.isFinite(Date.parse(String(evidence.observed_at_utc || "")))) {
    errors.push("forum_navigation_observed_at_invalid");
  }
  const destination = parseExactDiscordThreadDestination(evidence.destination_url);
  if (!destination) {
    errors.push("forum_navigation_destination_url_invalid");
  } else {
    if (destination.guild_id !== GUILD_ID || evidence.guild_id !== GUILD_ID) {
      errors.push("forum_navigation_destination_guild_mismatch");
    }
    if (
      evidence.destination_guild_id !== destination.guild_id ||
      evidence.thread_channel_id !== destination.thread_channel_id ||
      evidence.destination_verified !== true
    ) {
      errors.push("forum_navigation_destination_fields_mismatch");
    }
    if (
      !/^\d{15,22}$/.test(parentForumChannelId) ||
      evidence.parent_forum_channel_id !== parentForumChannelId
    ) {
      errors.push("forum_navigation_parent_forum_mismatch");
    } else if (destination.thread_channel_id === parentForumChannelId) {
      errors.push("forum_navigation_destination_is_parent_forum");
    }
  }
  return {
    valid: errors.length === 0,
    errors: Array.from(new Set(errors)),
    evidence_key: expectedKey,
    thread_channel_id: errors.length === 0 ? destination?.thread_channel_id || null : null,
  };
}

export function forumGroupNavigationCheckpointFilename(evidenceKey) {
  const match = String(evidenceKey || "").match(/^forum-group-navigation:([a-f0-9]{64})$/);
  return match ? `forum_group_navigation_${match[1]}.json` : null;
}

export function buildForumGroupNavigationCheckpoint(evidence) {
  return {
    schema_version: "1.0.0",
    artifact_type: "discord_forum_group_navigation_checkpoint",
    evidence_key: evidence?.evidence_key || null,
    query: evidence?.query || null,
    page_number: Number(evidence?.page_number),
    group_message_ids: normalizedForumGroupMessageIds(evidence?.group_message_ids) || [],
    source_url: evidence?.source_url || null,
    destination_url: evidence?.destination_url || null,
    thread_channel_id: evidence?.thread_channel_id || null,
    back_url: evidence?.back_url || null,
    restored_group_membership_sha256:
      evidence?.restored_group_membership_sha256 || null,
    pre_navigation_page_membership_sha256:
      evidence?.pre_navigation_page_membership_sha256 || null,
    restored_page_membership_sha256:
      evidence?.restored_page_membership_sha256 || null,
    checkpointed_at_utc: new Date().toISOString(),
    immutable: true,
    evidence,
  };
}

export function validateForumGroupNavigationCheckpoint(checkpoint, row, options = {}) {
  const errors = [];
  const evidenceValidation = validateForumGroupHeaderNavigationEvidence(
    checkpoint?.evidence,
    row,
    options,
  );
  if (!checkpoint || typeof checkpoint !== "object" || Array.isArray(checkpoint)) {
    return {
      valid: false,
      errors: ["forum_navigation_checkpoint_missing"],
      evidence_validation: evidenceValidation,
    };
  }
  if (checkpoint.schema_version !== "1.0.0") {
    errors.push("forum_navigation_checkpoint_schema_invalid");
  }
  if (checkpoint.artifact_type !== "discord_forum_group_navigation_checkpoint") {
    errors.push("forum_navigation_checkpoint_type_invalid");
  }
  if (checkpoint.immutable !== true) errors.push("forum_navigation_checkpoint_not_immutable");
  if (!Number.isFinite(Date.parse(String(checkpoint.checkpointed_at_utc || "")))) {
    errors.push("forum_navigation_checkpoint_timestamp_invalid");
  }
  const evidence = checkpoint.evidence || {};
  const bindings = [
    ["evidence_key", evidence.evidence_key],
    ["query", evidence.query],
    ["page_number", evidence.page_number],
    ["source_url", evidence.source_url],
    ["destination_url", evidence.destination_url],
    ["thread_channel_id", evidence.thread_channel_id],
    ["back_url", evidence.back_url],
    ["restored_group_membership_sha256", evidence.restored_group_membership_sha256],
    [
      "pre_navigation_page_membership_sha256",
      evidence.pre_navigation_page_membership_sha256,
    ],
    ["restored_page_membership_sha256", evidence.restored_page_membership_sha256],
  ];
  for (const [field, expectedValue] of bindings) {
    if (checkpoint[field] !== expectedValue) {
      errors.push(`forum_navigation_checkpoint_${field}_mismatch`);
    }
  }
  const checkpointMessageIds = normalizedForumGroupMessageIds(checkpoint.group_message_ids);
  const evidenceMessageIds = normalizedForumGroupMessageIds(evidence.group_message_ids);
  if (
    !checkpointMessageIds ||
    !evidenceMessageIds ||
    JSON.stringify(checkpointMessageIds) !== JSON.stringify(evidenceMessageIds)
  ) {
    errors.push("forum_navigation_checkpoint_group_membership_mismatch");
  }
  if (!forumGroupNavigationCheckpointFilename(checkpoint.evidence_key)) {
    errors.push("forum_navigation_checkpoint_evidence_key_invalid");
  }
  if (!evidenceValidation.valid) {
    errors.push(...evidenceValidation.errors.map((error) => `evidence:${error}`));
  }
  if (
    options.pageMembershipSha256 &&
    checkpoint.pre_navigation_page_membership_sha256 !== options.pageMembershipSha256
  ) {
    errors.push("forum_navigation_checkpoint_page_plan_mismatch");
  }
  return {
    valid: errors.length === 0,
    errors: Array.from(new Set(errors)),
    evidence_validation: evidenceValidation,
  };
}

export async function readForumGroupNavigationCheckpoint(directory, evidenceKey, row, options = {}) {
  if (!directory) return null;
  const filename = forumGroupNavigationCheckpointFilename(evidenceKey);
  if (!filename) throw forumNavigationEvidenceError("Forum checkpoint evidence key is invalid");
  const checkpointPath = nodePath.join(directory, filename);
  const checkpoint = await readJsonIfPresent(checkpointPath);
  if (!checkpoint) return null;
  const validation = validateForumGroupNavigationCheckpoint(checkpoint, row, options);
  if (!validation.valid) {
    throw forumNavigationEvidenceError(
      `Existing immutable forum checkpoint failed validation: ${validation.errors.join(",")}`,
    );
  }
  return { checkpointPath, checkpoint, reused: true };
}

export async function persistForumGroupNavigationCheckpoint(directory, evidence, row, options = {}) {
  if (!directory) return null;
  await fs.mkdir(directory, { recursive: true });
  const filename = forumGroupNavigationCheckpointFilename(evidence?.evidence_key);
  if (!filename) throw forumNavigationEvidenceError("Forum checkpoint evidence key is invalid");
  const checkpointPath = nodePath.join(directory, filename);
  const checkpoint = buildForumGroupNavigationCheckpoint(evidence);
  const validation = validateForumGroupNavigationCheckpoint(checkpoint, row, options);
  if (!validation.valid) {
    throw forumNavigationEvidenceError(
      `New immutable forum checkpoint failed validation: ${validation.errors.join(",")}`,
    );
  }
  try {
    await writeJsonExclusiveAtomic(checkpointPath, checkpoint);
  } catch (error) {
    if (error?.code !== "EEXIST") throw error;
    const existing = await readForumGroupNavigationCheckpoint(
      directory,
      evidence.evidence_key,
      row,
      options,
    );
    if (!existing || !isDeepStrictEqual(existing.checkpoint.evidence, evidence)) {
      throw forumNavigationEvidenceError(
        `Immutable forum checkpoint conflict for evidence key ${evidence.evidence_key}`,
      );
    }
    return existing;
  }
  return { checkpointPath, checkpoint, reused: false };
}

export function attachForumGroupHeaderNavigationEvidence(row, evidenceMap, options = {}) {
  const withMembership = deriveDiscordForumGroupMembershipFields(row || {});
  const key = withMembership.forum_group_membership_key;
  const evidence = key
    ? evidenceMap instanceof Map
      ? evidenceMap.get(key)
      : evidenceMap && typeof evidenceMap === "object"
        ? evidenceMap[key]
        : null
    : null;
  const validation = validateForumGroupHeaderNavigationEvidence(evidence, withMembership, options);
  return {
    ...withMembership,
    forum_group_navigation_evidence_key: key,
    forum_group_navigation_evidence: evidence || null,
    forum_group_navigation_validation: validation,
  };
}

function requiresForumGroupNavigationEvidence(options = {}) {
  return (
    options.channelKind === "forum channel" &&
    options.captureForumGroupNavigationEvidence !== false
  );
}

function rowHasExactForumGroupNavigationEvidence(row, options = {}) {
  const validation = validateForumGroupHeaderNavigationEvidence(
    row?.forum_group_navigation_evidence,
    row,
    { parentForumChannelId: options.channelId },
  );
  return Boolean(
    validation.valid &&
      row?.thread_channel_id_exact === true &&
      String(row?.inferred_thread_channel_id || "") === validation.thread_channel_id &&
      row?.thread_channel_id_conflict !== true &&
      [
        "forum_group_header_data_list_item_id",
        "forum_group_header_navigation_exact",
      ].includes(String(row?.thread_channel_id_source || "")),
  );
}

function forumGroupNavigationEvidenceMapFromRows(rows, options = {}) {
  const evidenceMap = {};
  for (const row of rows) {
    if (!rowHasExactForumGroupNavigationEvidence(row, options)) continue;
    const key = String(row.forum_group_navigation_evidence_key || "");
    const evidence = row.forum_group_navigation_evidence;
    if (!key || !evidence) continue;
    if (evidenceMap[key] && !isDeepStrictEqual(evidenceMap[key], evidence)) {
      throw forumNavigationEvidenceError(`Conflicting forum navigation evidence for key ${key}`);
    }
    evidenceMap[key] = evidence;
  }
  return evidenceMap;
}

function forumNavigationPagePlansFromRows(rows, options = {}) {
  const pages = {};
  const pageNumbers = Array.from(
    new Set(rows.map((row) => Number(row?.page_number)).filter((page) => Number.isInteger(page))),
  ).sort((left, right) => left - right);
  for (const pageNumber of pageNumbers) {
    const pageRows = rows.filter((row) => Number(row?.page_number) === pageNumber);
    const exactRows = pageRows.filter((row) => rowHasExactForumGroupNavigationEvidence(row, options));
    if (exactRows.length !== pageRows.length) continue;
    const evidenceMap = forumGroupNavigationEvidenceMapFromRows(pageRows, options);
    const hashes = Array.from(
      new Set(
        Object.values(evidenceMap).map(
          (evidence) => evidence.pre_navigation_page_membership_sha256,
        ),
      ),
    );
    if (hashes.length !== 1 || !/^[a-f0-9]{64}$/.test(String(hashes[0] || ""))) {
      throw forumNavigationEvidenceError(`Conflicting forum page plans on page ${pageNumber}`);
    }
    pages[String(pageNumber)] = {
      page_number: pageNumber,
      page_membership_sha256: hashes[0],
      message_count: pageRows.length,
      group_count: Object.keys(evidenceMap).length,
      group_evidence_keys: Object.keys(evidenceMap).sort(),
      all_rows_exact: true,
    };
  }
  return pages;
}

export function deriveDiscordThreadFields(row) {
  const groupHeaderDataId = String(row?.group_header_data_list_item_id || "").trim();
  const groupMatch = groupHeaderDataId.match(
    /^forum-channel-list-(\d{15,22})___(\d{15,22})$/,
  );
  const replyPermalink = String(row?.reply_to_permalink || "").trim();
  const replyMatch = replyPermalink.match(
    /\/channels\/(\d{15,22})\/(\d{15,22})\/(\d{15,22})(?:[/?#]|$)/,
  );
  const attachmentChannelId = Array.isArray(row?.attachments)
    ? row.attachments.find(
        (item) =>
          item?.ownership_status === "owned_exact" &&
          /^\d{15,22}$/.test(String(item?.thread_channel_id || "")),
      )
        ?.thread_channel_id || null
    : null;
  const legacyChannelId = /^\d{15,22}$/.test(String(row?.inferred_thread_channel_id || ""))
    ? String(row.inferred_thread_channel_id)
    : null;
  const navigationChannelId = row?.forum_group_navigation_validation?.valid === true
    ? String(row.forum_group_navigation_validation.thread_channel_id || "")
    : null;
  const candidates = [
    groupMatch
      ? {
          channel_id: groupMatch[2],
          source: "forum_group_header_data_list_item_id",
        }
      : null,
    /^\d{15,22}$/.test(navigationChannelId)
      ? {
          channel_id: navigationChannelId,
          source: "forum_group_header_navigation_exact",
        }
      : null,
    replyMatch
      ? {
          channel_id: replyMatch[2],
          source: "owned_reply_permalink",
        }
      : null,
    attachmentChannelId
      ? {
          channel_id: String(attachmentChannelId),
          source: "attachment_cdn_path_unverified",
        }
      : null,
    legacyChannelId
      ? {
          channel_id: legacyChannelId,
          source: String(row?.thread_channel_id_source || "legacy_inferred_container_id"),
        }
      : null,
  ].filter(Boolean);
  const selected = candidates[0] || null;
  const distinctCandidateIds = Array.from(new Set(candidates.map((candidate) => candidate.channel_id)));
  const selectedChannelId = selected?.channel_id || null;
  const messageId = /^\d{15,22}$/.test(String(row?.message_id || "")) ? String(row.message_id) : null;
  return {
    ...row,
    group_header_parent_forum_channel_id: groupMatch ? groupMatch[1] : null,
    reply_to_channel_id: replyMatch ? replyMatch[2] : null,
    reply_to_message_id: replyMatch ? replyMatch[3] : row?.reply_to_message_id || null,
    inferred_thread_channel_id: selectedChannelId,
    thread_channel_id_source: selected?.source || null,
    thread_channel_id_exact: Boolean(
      selected &&
        [
          "forum_group_header_data_list_item_id",
          "forum_group_header_navigation_exact",
          "owned_reply_permalink",
        ].includes(selected.source),
    ),
    thread_channel_id_candidates: candidates,
    thread_channel_id_conflict: distinctCandidateIds.length > 1,
    inferred_permalink:
      selectedChannelId && messageId
        ? `https://discord.com/channels/${GUILD_ID}/${selectedChannelId}/${messageId}`
        : null,
  };
}

export function deriveDiscordReplyFields(row, ownerChannelId = null) {
  const alreadyDerived = Object.prototype.hasOwnProperty.call(
    row || {},
    "reply_target_resolution_status",
  );
  const scopedContentMatch = String(row?.reply_target_content_id || "").match(
    /^message-content-(\d{15,22})$/,
  );
  const ownerMessageId = /^\d{15,22}$/.test(String(row?.message_id || ""))
    ? String(row.message_id)
    : null;
  // Only these collector-owned sources may establish a reply target.  In
  // particular, arbitrary IDs placed on a search-result descendant must not
  // be promoted into a reply relation merely because they look like Discord
  // snowflakes.
  const exactSources = new Set([
    "owned_reply_context_descendant_content_id",
    "owned_reply_descendant_message_id",
    "owned_reply_descendant_aria_reference",
    "owned_reply_descendant_data_list_item_id",
    "owned_reply_descendant_data_message_id",
    "owned_reply_permalink",
    "legacy_exact_reply_target_id",
  ]);
  const candidates = [];
  const addCandidate = (
    messageId,
    channelId,
    source,
    rawValue = null,
    trusted = false,
    ownerScoped = false,
  ) => {
    if (!trusted || !exactSources.has(source)) return;
    const exactMessageId = /^\d{15,22}$/.test(String(messageId || "")) ? String(messageId) : null;
    if (!exactMessageId || exactMessageId === ownerMessageId) return;
    const exactChannelId = /^\d{15,22}$/.test(String(channelId || "")) ? String(channelId) : null;
    const candidate = {
      message_id: exactMessageId,
      channel_id: exactChannelId,
      source: String(source || "exact_reply_target_id"),
      raw_value: rawValue == null ? null : String(rawValue).slice(0, 500),
      owner_scoped: ownerScoped === true,
    };
    if (
      !candidates.some(
        (item) =>
          item.message_id === candidate.message_id &&
          item.channel_id === candidate.channel_id &&
          item.source === candidate.source,
      )
    ) {
      candidates.push(candidate);
    }
  };
  if (scopedContentMatch) {
    addCandidate(
      scopedContentMatch[1],
      null,
      "owned_reply_context_descendant_content_id",
      row.reply_target_content_id,
      true,
      true,
    );
  }
  const existingCandidates = [
    ...(Array.isArray(row?.reply_to_message_id_candidates)
      ? row.reply_to_message_id_candidates
      : []),
    ...(Array.isArray(row?.reply_target_id_candidates)
      ? row.reply_target_id_candidates
      : []),
  ];
  const ownerScopedReplyEvidence =
    row?.reply_target_owner_scoped === true || row?.reply_context_scope_exact === true;
  const sourcesRequiringOwnerScope = new Set([
    "owned_reply_descendant_aria_reference",
    "owned_reply_descendant_data_list_item_id",
  ]);
  for (const candidate of existingCandidates) {
    if (!candidate || typeof candidate !== "object") continue;
    const sourceRequiresOwnerScope = sourcesRequiringOwnerScope.has(candidate.source);
    const candidateIsOwnerScoped =
      candidate.owner_scoped === true || ownerScopedReplyEvidence;
    addCandidate(
      candidate.message_id,
      candidate.channel_id,
      candidate.source,
      candidate.raw_value,
      exactSources.has(candidate.source) && (!sourceRequiresOwnerScope || candidateIsOwnerScoped),
      candidateIsOwnerScoped,
    );
  }
  const replyPermalink = alreadyDerived ? "" : String(row?.reply_to_permalink || "");
  const replyPermalinkMatch = replyPermalink.match(
    /\/channels\/(\d{15,22})\/(\d{15,22})\/(\d{15,22})(?:[/?#]|$)/,
  );
  if (replyPermalinkMatch) {
    addCandidate(
      replyPermalinkMatch[3],
      replyPermalinkMatch[2],
      "owned_reply_permalink",
      replyPermalink,
      true,
      true,
    );
  }
  if (!alreadyDerived && /^\d{15,22}$/.test(String(row?.reply_to_message_id || ""))) {
    addCandidate(
      row.reply_to_message_id,
      row.reply_to_channel_id,
      row.reply_to_message_id_source || "legacy_exact_reply_target_id",
      true,
      false,
    );
  }
  // ARIA and data-list evidence is exact only when extraction proved that the
  // evidence node belongs to this row's exact reply-context container.  This
  // deliberately does not trust similarly shaped attributes from an unknown
  // or merely nearby search-result descendant.
  if (ownerScopedReplyEvidence) {
    for (const ariaValue of [row?.reply_target_aria_labelledby, row?.reply_target_aria_describedby]) {
      const rawValue = String(ariaValue || "");
      for (const match of rawValue.matchAll(/message-(?:content|username|timestamp)-(\d{15,22})/g)) {
        addCandidate(
          match[1],
          null,
          "owned_reply_descendant_aria_reference",
          rawValue,
          true,
          true,
        );
      }
    }
    const dataListItemId = String(row?.reply_target_data_list_item_id || "");
    const dataListMatch = dataListItemId.match(
      /(?:chat-messages___|NO_LIST___|search-result-)(\d{15,22})(?:$|[^\d])/,
    );
    if (dataListMatch) {
      addCandidate(
        dataListMatch[1],
        null,
        "owned_reply_descendant_data_list_item_id",
        dataListItemId,
        true,
        true,
      );
    }
  }
  const distinctMessageIds = Array.from(new Set(candidates.map((candidate) => candidate.message_id)));
  const replyTargetConflict = distinctMessageIds.length > 1;
  const targetMessageId = distinctMessageIds.length === 1 ? distinctMessageIds[0] : null;
  const exactOwnerChannelId = /^\d{15,22}$/.test(String(ownerChannelId || ""))
    ? String(ownerChannelId)
    : null;
  const selectedCandidates = candidates.filter((candidate) => candidate.message_id === targetMessageId);
  const distinctChannelIds = Array.from(
    new Set(selectedCandidates.map((candidate) => candidate.channel_id).filter(Boolean)),
  );
  const replyChannelConflict = distinctChannelIds.length > 1;
  const targetChannelId = replyChannelConflict
    ? null
    : distinctChannelIds[0] || (targetMessageId ? exactOwnerChannelId : null);
  const source = targetMessageId
    ? selectedCandidates.find((candidate) => candidate.message_id === targetMessageId)?.source || null
    : null;
  const replyContext = String(row?.reply_context || row?.reply_to_content || "").trim();
  const replyContextPresent = row?.reply_context_present === true || Boolean(replyContext);
  const rawReplyCandidateArraysEmpty = [
    row?.reply_to_message_id_candidates,
    row?.reply_target_id_candidates,
  ].every((value) => !Array.isArray(value) || value.length === 0);
  const noExactTargetEvidence =
    !targetMessageId &&
    candidates.length === 0 &&
    rawReplyCandidateArraysEmpty &&
    !replyTargetConflict &&
    !replyChannelConflict &&
    !String(row?.reply_target_content_id || "").trim() &&
    !String(row?.reply_target_aria_labelledby || "").trim() &&
    !String(row?.reply_target_aria_describedby || "").trim() &&
    !String(row?.reply_target_data_list_item_id || "").trim() &&
    !replyPermalink;
  const replyContextLines = replyContext
    .split(/\r?\n/)
    .map((value) => value.trim())
    .filter(Boolean);
  const exactDynoCommandContext = Boolean(
    noExactTargetEvidence &&
      String(row?.author_id || "") === "155149108183695360" &&
      row?.content_scope_exact === true &&
      !String(row?.content_text || "").trim() &&
      replyContextLines.length === 3 &&
      replyContextLines[0] &&
      replyContextLines[1] === "used" &&
      replyContextLines[2],
  );
  const exactExecutedCommandContext = Boolean(
    noExactTargetEvidence &&
      /^\d{15,22}$/.test(String(ownerMessageId || "")) &&
      row?.article_id === `search-result-${ownerMessageId}` &&
      row?.reply_context_article_binding_exact === true &&
      row?.reply_context_owner_message_id === ownerMessageId &&
      row?.reply_context_dom_tag === "DIV" &&
      row?.reply_context_executed_command_exact === true &&
      row?.reply_context_aria_hidden === true &&
      row?.reply_context_scope_exact === false &&
      row?.author_verified_app_exact === true &&
      String(row?.author || "").trim() === "Wordle" &&
      String(row?.author_id || "") === EXECUTED_COMMAND_AUTHOR_ID &&
      row?.author_id_conflict === false &&
      new Set(["exact_username_bound_data_user_id", "owner_scoped_avatar_cdn_path"]).has(
        String(row?.author_id_source || ""),
      ) &&
      row?.content_scope_exact === true &&
      row?.reply_context_present === true &&
      row?.reply_target_owner_scoped === false &&
      String(row?.reply_target_content_text || "") === "" &&
      String(row?.reply_to_content || "").split(/\r?\n/).map((line) => line.trim()).filter(Boolean)
        .join("\n") === replyContextLines.join("\n") &&
      String(row?.reply_context_dom_class || "").split(/\s+/).includes(
        EXECUTED_COMMAND_CLASS_TOKEN,
      ) &&
      (() => {
        const articleLabelTokens = String(row?.article_aria_labelledby || "")
          .split(/\s+/)
          .filter(Boolean);
        const referencedIds = Array.from(
          String(row?.article_aria_labelledby || "").matchAll(
            /message-(?:reply-context|username|content|accessories|timestamp)-(\d{15,22})/g,
          ),
          (match) => match[1],
        );
        return [
          `message-username-${ownerMessageId}`,
          `message-content-${ownerMessageId}`,
          `message-timestamp-${ownerMessageId}`,
        ].every((token) => articleLabelTokens.includes(token)) &&
          new Set(referencedIds).size === 1 &&
          referencedIds[0] === ownerMessageId;
      })() &&
      replyContextLines.length === 3 &&
      Boolean(replyContextLines[0]) &&
      replyContextLines[1] === "used" &&
      replyContextLines[2] === "Play",
  );
  const resolutionStatus = targetMessageId
    ? "exact_target_id"
    : !replyContextPresent
      ? "not_applicable"
      : exactDynoCommandContext
        ? "discord_dyno_command_context_without_reply_target"
        : exactExecutedCommandContext
          ? "discord_executed_command_context_without_reply_target"
      : noExactTargetEvidence && /^Message could not be loaded$/i.test(replyContext)
        ? "discord_message_not_loaded"
        : noExactTargetEvidence && replyContextLines.includes("Click to see attachment")
          ? "discord_attachment_preview_without_exact_target_id"
          : noExactTargetEvidence && replyContextLines.includes("Click to see sticker")
            ? "discord_sticker_preview_without_exact_target_id"
            : noExactTargetEvidence && replyContext.toLowerCase() === "click to see voice message"
              ? "discord_voice_message_preview_without_exact_target_id"
            : "unresolved_without_exact_target_id";
  return {
    ...row,
    reply_to_message_id: targetMessageId || null,
    reply_to_message_id_source: source,
    reply_to_channel_id: targetChannelId || null,
    reply_to_permalink:
      targetMessageId && targetChannelId
        ? `https://discord.com/channels/${GUILD_ID}/${targetChannelId}/${targetMessageId}`
        : null,
    reply_target_scope_exact: Boolean(targetMessageId && exactSources.has(source)),
    reply_to_message_id_candidates: candidates,
    reply_to_message_id_conflict: replyTargetConflict,
    reply_to_channel_id_conflict: replyChannelConflict,
    reply_target_resolution_status: resolutionStatus,
    reply_context_non_reply_exact: exactDynoCommandContext || exactExecutedCommandContext,
    reply_context_non_reply_type: exactDynoCommandContext
      ? "discord_dyno_command_invocation"
      : exactExecutedCommandContext
        ? "discord_application_command_invocation"
        : null,
    reply_target_unavailability_documented: new Set([
      "discord_message_not_loaded",
      "discord_attachment_preview_without_exact_target_id",
      "discord_sticker_preview_without_exact_target_id",
      "discord_voice_message_preview_without_exact_target_id",
      "discord_dyno_command_context_without_reply_target",
      "discord_executed_command_context_without_reply_target",
    ]).has(resolutionStatus),
  };
}

async function extractPage(tab, pageNumber, query) {
  const browserRows = await tab.playwright.evaluate(
    (arg) => {
      const region = document.querySelector('[aria-label="Search Results"]');
      if (!region) return [];
      const rows = [];
      const groups = Array.from(region.querySelectorAll('[role="group"]'));
      for (const group of groups) {
        const groupLabel = group.getAttribute("aria-label") || "";
        const groupHeader = group.querySelector(':scope > [role="button"]');
        const groupHeaderText = (groupHeader?.innerText || "").trim();
        const groupHeaderDataListItemId = groupHeader?.getAttribute("data-list-item-id") || null;
        const splitAt = groupLabel.lastIndexOf(", ");
        const threadTitle = splitAt >= 0 ? groupLabel.slice(0, splitAt) : groupLabel;
        const parentChannel = splitAt >= 0 ? groupLabel.slice(splitAt + 2) : "";
        const items = Array.from(group.querySelectorAll(':scope > [role="listitem"]'));
        const groupMessageIds = items.map((groupItem) => {
          const groupArticle = groupItem.querySelector(
            '[role="article"][data-list-item-id^="NO_LIST___"]',
          );
          const groupDataId = groupArticle?.getAttribute("data-list-item-id") || "";
          return groupDataId.replace("NO_LIST___", "") ||
            (groupArticle?.id || "").replace("search-result-", "");
        });
        const forumGroupMembershipExact = Boolean(
          groupMessageIds.length === items.length &&
            groupMessageIds.length > 0 &&
            groupMessageIds.every((value) => /^\d{15,22}$/.test(value)) &&
            new Set(groupMessageIds).size === groupMessageIds.length,
        );
        for (const item of items) {
          const article = item.querySelector('[role="article"][data-list-item-id^="NO_LIST___"]');
          if (!article) continue;
          const dataId = article.getAttribute("data-list-item-id") || "";
          const messageId = dataId.replace("NO_LIST___", "") || (article.id || "").replace("search-result-", "");
          const exactUsernameRoot = article.querySelector(`#message-username-${messageId}`);
          const usernameNode =
            exactUsernameRoot?.querySelector("[data-text]") ||
            exactUsernameRoot?.querySelector('[role="button"]') ||
            article.querySelector('[id^="message-username-"] [data-text]') ||
            article.querySelector('[id^="message-username-"] [role="button"]');
          const authorBoundNodes = [];
          if (exactUsernameRoot) {
            authorBoundNodes.push(exactUsernameRoot, ...Array.from(exactUsernameRoot.querySelectorAll("*")));
            for (
              let ancestor = exactUsernameRoot.parentElement;
              ancestor && ancestor !== article;
              ancestor = ancestor.parentElement
            ) {
              authorBoundNodes.push(ancestor);
            }
          }
          const authorIdCandidates = Array.from(
            new Set(
              authorBoundNodes
                .map((node) => String(node.getAttribute?.("data-user-id") || ""))
                .filter((value) => /^\d{15,22}$/.test(value)),
            ),
          );
          const exactAuthorId = authorIdCandidates.length === 1 ? authorIdCandidates[0] : null;
          const belongsToCurrentMessage = (node) =>
            !node.closest('[id^="message-reply-context-"], [class*="repliedMessage"]');
          const exactTimeRoot = article.querySelector(`#message-timestamp-${messageId}`);
          const rowOwnedTimeNodes = Array.from(article.querySelectorAll("time[datetime]")).filter(
            belongsToCurrentMessage,
          );
          const soleRowOwnedTimeNode = rowOwnedTimeNodes.length === 1 ? rowOwnedTimeNodes[0] : null;
          const timeNode =
            (exactTimeRoot?.matches("time[datetime]") ? exactTimeRoot : exactTimeRoot?.querySelector("time[datetime]")) ||
            rowOwnedTimeNodes[0] ||
            null;
          const contentNode =
            article.querySelector(`#message-content-${messageId}`) || article.querySelector('[id^="message-content-"]');
          const replyNode =
            article.querySelector(`#message-reply-context-${messageId}`) ||
            article.querySelector('[id^="message-reply-context-"], [class*="repliedMessage"]');
          const replyContextScopeExact =
            replyNode?.getAttribute("id") === `message-reply-context-${messageId}`;
          const replyContextClass = String(replyNode?.getAttribute("class") || "").trim();
          const replyContextAriaHidden = replyNode?.getAttribute("aria-hidden") === "true";
          const articleId = String(article.getAttribute("id") || "");
          const articleAriaLabelledby = String(
            article.getAttribute("aria-labelledby") || "",
          );
          const articleAriaTokens = articleAriaLabelledby.split(/\s+/).filter(Boolean);
          const articleMessageReferenceIds = Array.from(
            articleAriaLabelledby.matchAll(
              /message-(?:reply-context|username|content|accessories|timestamp)-(\d{15,22})/g,
            ),
            (match) => match[1],
          );
          const replyContextArticleBindingExact = Boolean(
            /^\d{15,22}$/.test(messageId) &&
              dataId === `NO_LIST___${messageId}` &&
              articleId === `search-result-${messageId}` &&
              [
                `message-username-${messageId}`,
                `message-content-${messageId}`,
                `message-timestamp-${messageId}`,
              ].every((token) => articleAriaTokens.includes(token)) &&
              new Set(articleMessageReferenceIds).size === 1 &&
              articleMessageReferenceIds[0] === messageId &&
              replyNode?.closest('[role="article"]') === article,
          );
          const replyContextExecutedCommandExact = Boolean(
            replyContextArticleBindingExact &&
              replyNode?.tagName === "DIV" &&
              replyContextClass.split(/\s+/).includes(EXECUTED_COMMAND_CLASS_TOKEN) &&
              replyContextAriaHidden,
          );
          const authorVerifiedAppExact = Boolean(
            exactUsernameRoot?.querySelector('[aria-label="Verified App"]'),
          );
          const isOwnerScopedReplyEvidence = (node) =>
            Boolean(replyContextScopeExact && node?.closest?.('[id^="message-reply-context-"], [class*="repliedMessage"]') === replyNode);
          const replyLinks = Array.from(replyNode?.querySelectorAll('a[href*="/channels/"]') || []).filter(
            isOwnerScopedReplyEvidence,
          );
          const replyLink = replyLinks[0] || null;
          const replyHref = replyLink?.href || "";
          const replyPermalinkMatch = replyHref.match(
            /\/channels\/(\d{15,22})\/(\d{15,22})\/(\d{15,22})(?:[/?#]|$)/,
          );
          const replyTargetContentNode = Array.from(
            replyNode?.querySelectorAll('[id^="message-content-"]') || [],
          ).find(isOwnerScopedReplyEvidence) || null;
          const replyTargetContentId = replyTargetContentNode?.getAttribute("id") || null;
          const replyTargetContentMatch = String(replyTargetContentId || "").match(
            /^message-content-(\d{15,22})$/,
          );
          const replyTargetIdCandidates = [];
          const addReplyTargetCandidate = (candidateId, source, rawValue, channelId = null) => {
            const exactId = String(candidateId || "");
            if (!/^\d{15,22}$/.test(exactId) || exactId === messageId) return;
            const candidate = {
              message_id: exactId,
              channel_id: /^\d{15,22}$/.test(String(channelId || "")) ? String(channelId) : null,
              source,
              raw_value: String(rawValue || "").slice(0, 500),
              owner_scoped: replyContextScopeExact,
            };
            if (
              !replyTargetIdCandidates.some(
                (item) =>
                  item.message_id === candidate.message_id &&
                  item.channel_id === candidate.channel_id &&
                  item.source === candidate.source,
              )
            ) {
              replyTargetIdCandidates.push(candidate);
            }
          };
          for (const link of replyLinks) {
            const href = String(link.href || "");
            const match = href.match(
              /\/channels\/(\d{15,22})\/(\d{15,22})\/(\d{15,22})(?:[/?#]|$)/,
            );
            if (match) addReplyTargetCandidate(match[3], "owned_reply_permalink", href, match[2]);
          }
          const replyEvidenceNodes = replyNode
            ? [replyNode, ...Array.from(replyNode.querySelectorAll("*"))].filter(isOwnerScopedReplyEvidence)
            : [];
          const replyTargetAriaLabelledby = replyEvidenceNodes
            .map((node) => String(node.getAttribute?.("aria-labelledby") || ""))
            .find((value) => /message-(?:content|username|timestamp)-\d{15,22}/.test(value)) || null;
          const replyTargetAriaDescribedby = replyEvidenceNodes
            .map((node) => String(node.getAttribute?.("aria-describedby") || ""))
            .find((value) => /message-(?:content|username|timestamp)-\d{15,22}/.test(value)) || null;
          const replyTargetDataListItemId = replyEvidenceNodes
            .map((node) => String(node.getAttribute?.("data-list-item-id") || ""))
            .find((value) => /(?:chat-messages___|NO_LIST___|search-result-)\d{15,22}(?:$|[^\d])/.test(value)) || null;
          for (const node of replyEvidenceNodes) {
            for (const attributeName of ["id", "aria-labelledby", "aria-describedby"]) {
              const value = String(node.getAttribute?.(attributeName) || "");
              for (const match of value.matchAll(/message-(?:content|username|timestamp)-(\d{15,22})/g)) {
                addReplyTargetCandidate(
                  match[1],
                  attributeName === "id"
                    ? "owned_reply_descendant_message_id"
                    : "owned_reply_descendant_aria_reference",
                  value,
                );
              }
            }
            const dataListItemId = String(node.getAttribute?.("data-list-item-id") || "");
            const dataListMatch = dataListItemId.match(
              /(?:chat-messages___|NO_LIST___|search-result-)(\d{15,22})(?:$|[^\d])/,
            );
            if (dataListMatch) {
              addReplyTargetCandidate(
                dataListMatch[1],
                "owned_reply_descendant_data_list_item_id",
                dataListItemId,
              );
            }
            const dataMessageId = String(node.getAttribute?.("data-message-id") || "");
            if (/^\d{15,22}$/.test(dataMessageId)) {
              addReplyTargetCandidate(
                dataMessageId,
                "owned_reply_descendant_data_message_id",
                dataMessageId,
              );
            }
          }
          const ownerAvatarUrl = Array.from(article.querySelectorAll("img[src]"))
            .filter(belongsToCurrentMessage)
            .map((image) => image.src || image.getAttribute("src") || "")
            .find(
              (url) =>
                /\/avatars\/\d{15,22}\//.test(url) ||
                /\/guilds\/\d{15,22}\/users\/\d{15,22}\/avatars\//.test(url),
            ) || null;
          const linkNodes = Array.from(article.querySelectorAll("a[href]")).filter(
            belongsToCurrentMessage,
          );
          const links = linkNodes.map((anchor) => anchor.href).filter(Boolean);
          const attachmentLinkEvidence = new Map();
          for (const anchor of linkNodes) {
            const url = String(anchor.href || "").split("?")[0];
            if (!url.includes("/attachments/")) continue;
            const inContent = Boolean(anchor.closest(`#message-content-${messageId}`));
            const inAccessories = Boolean(anchor.closest(`#message-accessories-${messageId}`));
            const inEmbed = Boolean(
              anchor.closest('[class*="embed" i], [aria-label*="embed" i], [data-list-item-id*="embed" i]'),
            );
            const previous = attachmentLinkEvidence.get(url) || {
              in_content: false,
              in_accessories: false,
              in_embed: false,
            };
            attachmentLinkEvidence.set(url, {
              in_content: previous.in_content || inContent,
              in_accessories: previous.in_accessories || inAccessories,
              in_embed: previous.in_embed || inEmbed,
            });
          }
          const cleanAttachments = Array.from(attachmentLinkEvidence.keys());
          const attachments = cleanAttachments.map((url) => {
            const match = url.match(/\/attachments\/(\d+)\/(\d+)\/([^/?#]+)/);
            const evidence = attachmentLinkEvidence.get(url) || {};
            const domRelation = evidence.in_content
              ? "message_content_link"
              : evidence.in_embed
                ? "embed_descendant"
                : evidence.in_accessories
                  ? "exact_message_accessories_descendant"
                  : "article_link_unresolved";
            return {
              url,
              thread_channel_id: match ? match[1] : null,
              attachment_id: match ? match[2] : null,
              filename: match ? match[3] : null,
              dom_relation: domRelation,
              href_in_message_content: Boolean(evidence.in_content),
            };
          });
          const imageAlt = Array.from(article.querySelectorAll("img[alt]"))
            .filter(belongsToCurrentMessage)
            .map((image) => image.getAttribute("alt"))
            .filter((value) => value && value.trim());
          const mediaAssets = Array.from(article.querySelectorAll("img[src], video[src], source[src]"))
            .filter(belongsToCurrentMessage)
            .map((media) => ({
              tag: media.tagName.toLowerCase(),
              src: (media.getAttribute("src") || "").split("?")[0],
              alt: (media.getAttribute("alt") || "").trim() || null,
            }))
            .filter((media) => media.src.includes("/attachments/"));
          const reactions = Array.from(article.querySelectorAll('button[aria-label*="reaction"]'))
            .filter(belongsToCurrentMessage)
            .map((button) => ({
              aria_label: button.getAttribute("aria-label") || "",
              text: (button.innerText || "").trim(),
              pressed: button.getAttribute("aria-pressed") === "true",
            }));
          const referencedUserIds = Array.from(
            new Set(
              Array.from(article.querySelectorAll("[data-user-id]"))
                .filter(belongsToCurrentMessage)
                .map((node) => node.getAttribute("data-user-id"))
                .filter(Boolean),
            ),
          );
          const visibleText = (article.innerText || "").trim();
          const contentText = (contentNode?.innerText || "").trim();
          const threadChannelId = attachments.find((item) => item.thread_channel_id)?.thread_channel_id || null;
          rows.push({
            message_id: messageId,
            result_index: Number(item.getAttribute("aria-posinset") || 0),
            result_set_size: Number(item.getAttribute("aria-setsize") || 0),
            result_listitem_id: item.getAttribute("id") || null,
            article_id: article.getAttribute("id") || null,
            article_aria_labelledby: article.getAttribute("aria-labelledby") || null,
            page_number: arg.pageNumber,
            thread_title: threadTitle,
            parent_channel: parentChannel,
            group_label: groupLabel,
            group_header_text: groupHeaderText,
            group_header_data_list_item_id: groupHeaderDataListItemId,
            // Give every row its own membership array. The Chrome bridge can
            // otherwise preserve a shared array only on the first serialized
            // row and emit empty arrays for the remaining rows in the group.
            forum_group_message_ids: [...groupMessageIds],
            forum_group_membership_exact: forumGroupMembershipExact,
            author: (usernameNode?.getAttribute("data-text") || usernameNode?.innerText || "").trim(),
            author_id: exactAuthorId,
            author_id_source: exactAuthorId ? "exact_username_bound_data_user_id" : null,
            author_id_candidates: authorIdCandidates,
            author_id_conflict: authorIdCandidates.length > 1,
            author_avatar_url: ownerAvatarUrl ? ownerAvatarUrl.split("?")[0] : null,
            timestamp_utc: timeNode?.getAttribute("datetime") || null,
            timestamp_scope_exact: Boolean(exactTimeRoot),
            row_owned_time_count: rowOwnedTimeNodes.length,
            row_owned_time_datetime: soleRowOwnedTimeNode?.getAttribute("datetime") || null,
            row_owned_time_element_id: soleRowOwnedTimeNode?.getAttribute("id") || null,
            displayed_time: (timeNode?.innerText || "").trim(),
            content_text: contentText,
            visible_text: visibleText,
            content_present: Boolean(contentText),
            content_scope_exact: Boolean(article.querySelector(`#message-content-${messageId}`)),
            reply_context: (replyNode?.innerText || "").trim(),
            reply_to_permalink: replyHref || null,
            reply_to_message_id: replyTargetContentMatch
              ? replyTargetContentMatch[1]
              : replyPermalinkMatch
                ? replyPermalinkMatch[3]
                : null,
            reply_target_id_candidates: replyTargetIdCandidates,
            reply_target_content_id: replyTargetContentId,
            reply_target_content_text: (replyTargetContentNode?.innerText || "").trim(),
            reply_target_aria_labelledby: replyTargetAriaLabelledby,
            reply_target_aria_describedby: replyTargetAriaDescribedby,
            reply_target_data_list_item_id: replyTargetDataListItemId,
            reply_target_owner_scoped: replyContextScopeExact,
            reply_context_scope_exact: replyContextScopeExact,
            reply_context_dom_class: replyContextClass || null,
            reply_context_dom_tag: replyNode?.tagName || null,
            reply_context_aria_hidden: replyContextAriaHidden,
            reply_context_article_binding_exact: replyContextArticleBindingExact,
            reply_context_owner_message_id: replyContextExecutedCommandExact
              ? messageId
              : null,
            reply_context_executed_command_exact: replyContextExecutedCommandExact,
            author_verified_app_exact: authorVerifiedAppExact,
            reply_to_content: (replyNode?.innerText || "").trim(),
            reply_context_present: Boolean(replyNode),
            edited: visibleText.includes("(edited)"),
            is_op: Boolean(article.querySelector('[aria-label="OP"]')),
            image_alt: imageAlt,
            media_assets: mediaAssets,
            reactions,
            referenced_user_ids: referencedUserIds,
            attachments,
            links: Array.from(new Set(links.filter((url) => !url.startsWith("data:")))),
            inferred_thread_channel_id: threadChannelId,
            inferred_permalink: null,
            search_query: arg.query,
          });
        }
      }
      return rows;
    },
    { pageNumber, query },
  );
  return browserRows.map((row) => {
    const withThread = deriveDiscordThreadFields(
      deriveDiscordForumGroupMembershipFields(
        deriveDiscordAuthorFields(deriveDiscordSnowflakeFields(row)),
      ),
    );
    return deriveDiscordReplyFields(withThread, withThread.inferred_thread_channel_id);
  });
}

function forumNavigationEvidenceError(message) {
  return new DiscordSearchStateError(message, "forum_group_navigation_evidence_failed");
}

async function waitForExactForumThreadDestination(tab, parentForumChannelId, options = {}) {
  const attempts = Number(options.forumNavigationObservationAttempts ?? 20);
  const delayMs = Number(options.forumNavigationObservationDelayMs ?? 250);
  for (let attempt = 0; attempt < attempts; attempt += 1) {
    await tab.playwright.domSnapshot();
    const href = await tab.playwright.evaluate(() => window.location.href);
    const destination = parseExactDiscordThreadDestination(href);
    if (
      destination &&
      destination.guild_id === GUILD_ID &&
      destination.thread_channel_id !== parentForumChannelId
    ) {
      return String(href);
    }
    if (attempt + 1 < attempts && delayMs > 0) {
      await tab.playwright.waitForTimeout(delayMs);
    }
  }
  throw forumNavigationEvidenceError("Forum group header did not reach an exact Discord thread URL");
}

async function waitForRestoredForumSearchGroup(
  tab,
  query,
  pageNumber,
  messageIds,
  sourceUrl,
  options = {},
) {
  const attempts = Number(options.forumNavigationObservationAttempts ?? 20);
  const delayMs = Number(options.forumNavigationObservationDelayMs ?? 250);
  for (let attempt = 0; attempt < attempts; attempt += 1) {
    await tab.playwright.domSnapshot();
    const state = await tab.playwright.evaluate(
      (arg) => {
        const normalizedIds = (value) =>
          Array.from(new Set(value.map((item) => String(item || "")))).sort();
        const membershipFor = (group) =>
          normalizedIds(
            Array.from(group.querySelectorAll(':scope > [role="listitem"]')).map((item) => {
              const article = item.querySelector(
                '[role="article"][data-list-item-id^="NO_LIST___"]',
              );
              return (article?.getAttribute("data-list-item-id") || "").replace("NO_LIST___", "") ||
                (article?.id || "").replace("search-result-", "");
            }),
          );
        const expected = normalizedIds(arg.messageIds);
        const region = document.querySelector('[aria-label="Search Results"]');
        const groups = Array.from(region?.querySelectorAll('[role="group"]') || []);
        const matchingGroupMessageIds = groups
          .map((group) => membershipFor(group))
          .filter((messageIds) => JSON.stringify(messageIds) === JSON.stringify(expected));
        const searchBox = document.querySelector('[role="combobox"][aria-label="Search"]');
        const queryValue = searchBox
          ? String(
              searchBox.value || searchBox.getAttribute("value") || searchBox.textContent || "",
            ).trim()
          : "";
        const first = region?.querySelector('[role="listitem"]') || null;
        const firstIndex = first ? Number(first.getAttribute("aria-posinset") || 0) : 0;
        return {
          query: queryValue,
          page_number: firstIndex > 0 ? Math.floor((firstIndex - 1) / 25) + 1 : 0,
          matching_group_count: matchingGroupMessageIds.length,
          matching_group_message_ids:
            matchingGroupMessageIds.length === 1 ? matchingGroupMessageIds[0] : [],
          current_url: String(window.location.href || ""),
        };
      },
      { messageIds },
    );
    if (
      state?.query === query &&
      state?.page_number === pageNumber &&
      state?.matching_group_count === 1 &&
      state?.current_url === sourceUrl
    ) {
      return state;
    }
    if (attempt + 1 < attempts && delayMs > 0) {
      await tab.playwright.waitForTimeout(delayMs);
    }
  }
  throw forumNavigationEvidenceError(
    "Browser Back did not restore the exact source URL, Discord search query, page, and group membership",
  );
}

export async function collectForumGroupHeaderNavigationEvidence(
  tab,
  pageRows,
  query,
  pageNumber,
  options = {},
) {
  const parentForumChannelId = String(options.channelId || "");
  if (!/^\d{15,22}$/.test(parentForumChannelId)) {
    throw forumNavigationEvidenceError("Exact parent forum channel ID is required");
  }
  const checkpointDirectory =
    String(options.forumGroupNavigationCheckpointDirectory || "").trim() || null;
  if (!checkpointDirectory) {
    throw new Error("Forum navigation requires forumGroupNavigationCheckpointDirectory");
  }
  const pagePlan = options.forumNavigationPagePlan;
  const reportedTotal = Number(options.forumNavigationReportedTotal);
  const pagePlanValidation = validateForumNavigationPagePlan(pagePlan, {
    query,
    pageNumber,
    reportedTotal,
  });
  if (!pagePlanValidation.valid) {
    throw forumNavigationEvidenceError(
      `Forum page plan failed validation: ${pagePlanValidation.errors.join(",")}`,
    );
  }
  const pageCheckpointDirectory =
    String(options.forumNavigationPageCheckpointDirectory || "").trim() ||
    nodePath.join(checkpointDirectory, `page_${String(pageNumber).padStart(3, "0")}`);
  const maxGroupsPerCall = Number(
    options.maxForumNavigationGroupsPerCall ?? Number.POSITIVE_INFINITY,
  );
  if (
    !(
      maxGroupsPerCall === Number.POSITIVE_INFINITY ||
      (Number.isInteger(maxGroupsPerCall) && maxGroupsPerCall > 0)
    )
  ) {
    throw new Error("maxForumNavigationGroupsPerCall must be a positive integer or Infinity");
  }
  const groups = new Map();
  for (const originalRow of pageRows) {
    const row = deriveDiscordForumGroupMembershipFields(originalRow);
    if (
      row.search_query !== query ||
      Number(row.page_number) !== Number(pageNumber) ||
      !row.forum_group_membership_exact ||
      !row.forum_group_membership_key
    ) {
      throw forumNavigationEvidenceError(
        `Exact forum group membership is missing for message ${row.message_id || "unknown"}`,
      );
    }
    const existing = groups.get(row.forum_group_membership_key);
    if (
      existing &&
      JSON.stringify(existing) !== JSON.stringify(row.forum_group_message_ids)
    ) {
      throw forumNavigationEvidenceError("Forum group membership key collision detected");
    }
    groups.set(row.forum_group_membership_key, row.forum_group_message_ids);
  }

  const pageSourceUrl = await tab.playwright.evaluate(() => String(window.location.href || ""));
  const parsedPageSource = parseExactDiscordGuildNavigationUrl(pageSourceUrl);
  if (
    !parsedPageSource ||
    parsedPageSource.guild_id !== GUILD_ID ||
    parsedPageSource.channel_id !== parentForumChannelId ||
    parsedPageSource.message_id !== null ||
    (options.forumNavigationExpectedSourceUrl &&
      pageSourceUrl !== options.forumNavigationExpectedSourceUrl)
  ) {
    throw forumNavigationEvidenceError(
      "Forum page source URL is not the exact authorized parent forum surface",
    );
  }

  const evidenceMap = {};
  let newlyCheckpointedGroups = 0;
  for (const [evidenceKey, messageIds] of groups) {
    const probeRow = pageRows.find((row) =>
      messageIds.includes(String(row.message_id || "")),
    );
    const existingCheckpoint = await readForumGroupNavigationCheckpoint(
      pageCheckpointDirectory,
      evidenceKey,
      probeRow,
      {
        parentForumChannelId,
        pageMembershipSha256: pagePlan.page_membership_sha256,
      },
    );
    if (existingCheckpoint) {
      evidenceMap[evidenceKey] = existingCheckpoint.checkpoint.evidence;
      continue;
    }
    if (newlyCheckpointedGroups >= maxGroupsPerCall) {
      throw new DiscordForumNavigationBatchPending(
        pageNumber,
        Object.keys(evidenceMap).length,
        groups.size,
        maxGroupsPerCall,
      );
    }
    await tab.playwright.domSnapshot();
    const marked = await tab.playwright.evaluate(
      (arg) => {
        const normalizedIds = (value) =>
          Array.from(new Set(value.map((item) => String(item || "")))).sort();
        const membershipFor = (group) =>
          normalizedIds(
            Array.from(group.querySelectorAll(':scope > [role="listitem"]')).map((item) => {
              const article = item.querySelector(
                '[role="article"][data-list-item-id^="NO_LIST___"]',
              );
              return (article?.getAttribute("data-list-item-id") || "").replace("NO_LIST___", "") ||
                (article?.id || "").replace("search-result-", "");
            }),
          );
        const expected = normalizedIds(arg.messageIds);
        const region = document.querySelector('[aria-label="Search Results"]');
        const groups = Array.from(region?.querySelectorAll('[role="group"]') || []);
        const matches = groups.filter(
          (group) => JSON.stringify(membershipFor(group)) === JSON.stringify(expected),
        );
        const headers = matches.flatMap((group) =>
          Array.from(group.querySelectorAll(':scope > [role="button"]')),
        );
        return {
          group_match_count: matches.length,
          header_button_match_count: headers.length,
          query: String(
            document.querySelector('[role="combobox"][aria-label="Search"]')?.value ||
              document
                .querySelector('[role="combobox"][aria-label="Search"]')
                ?.getAttribute("value") ||
              document.querySelector('[role="combobox"][aria-label="Search"]')
                ?.textContent ||
              "",
          ).trim(),
          page_number: (() => {
            const first = region?.querySelector('[role="listitem"]') || null;
            const firstIndex = first ? Number(first.getAttribute("aria-posinset") || 0) : 0;
            return firstIndex > 0 ? Math.floor((firstIndex - 1) / 25) + 1 : 0;
          })(),
          current_url: String(window.location.href || ""),
        };
      },
      { messageIds },
    );
    if (
      marked?.group_match_count !== 1 ||
      marked?.header_button_match_count !== 1 ||
      marked?.query !== query ||
      Number(marked?.page_number) !== Number(pageNumber) ||
      marked?.current_url !== pageSourceUrl
    ) {
      throw forumNavigationEvidenceError(
        `Forum group pre-navigation state was not exact for evidence key ${evidenceKey}`,
      );
    }
    // The Chrome bridge exposes DOM nodes read-only inside evaluate(), so a
    // temporary marker attribute cannot be attached. Address the already
    // validated unique group through one exact member message instead.
    const anchorMessageId = messageIds[0];
    const header = tab.playwright.locator(
      `[aria-label="Search Results"] [role="group"]:has(> [role="listitem"] ` +
        `[role="article"][data-list-item-id="NO_LIST___${anchorMessageId}"]) > [role="button"]`,
    );
    if ((await header.count()) !== 1) {
      throw forumNavigationEvidenceError(
        `Marked forum group header was not uniquely addressable for evidence key ${evidenceKey}`,
      );
    }

    let destinationUrl = null;
    let restored = false;
    let restoredState = null;
    let headerClicked = false;
    const sourceUrl = pageSourceUrl;
    try {
      await header.click();
      headerClicked = true;
      destinationUrl = await waitForExactForumThreadDestination(
        tab,
        parentForumChannelId,
        options,
      );
      await tab.playwright.goBack();
      restoredState = await waitForRestoredForumSearchGroup(
        tab,
        query,
        pageNumber,
        messageIds,
        sourceUrl,
        options,
      );
      const restoredObservation = await observeForumPreNavigationMembership(
        tab,
        query,
        pageNumber,
      );
      const restoredPageValidation = validateForumPreNavigationMembership(
        restoredObservation,
        pageRows,
        query,
        pageNumber,
        reportedTotal,
      );
      const restoredPageMembershipSha256 = restoredPageValidation.valid
        ? forumPageMembershipSha256(
            query,
            pageNumber,
            reportedTotal,
            restoredPageValidation.canonical,
          )
        : null;
      if (
        !restoredPageValidation.valid ||
        restoredPageMembershipSha256 !== pagePlan.page_membership_sha256
      ) {
        throw forumNavigationEvidenceError(
          "Browser Back did not restore the immutable full-page membership plan",
        );
      }
      restoredState.restored_page_membership_sha256 = restoredPageMembershipSha256;
      restored = true;
    } finally {
      const currentUrl = await tab.playwright.evaluate(() => window.location.href).catch(() => null);
      if (!restored && headerClicked && currentUrl && currentUrl !== sourceUrl) {
        await tab.playwright.goBack().catch(() => {});
        await waitForRestoredForumSearchGroup(
          tab,
          query,
          pageNumber,
          messageIds,
          sourceUrl,
          options,
        ).catch(() => {});
      }
    }
    const evidence = buildForumGroupHeaderNavigationEvidence({
      query,
      pageNumber,
      messageIds,
      parentForumChannelId,
      sourceUrl,
      destinationUrl,
      backUrl: restoredState?.current_url || null,
      restoredQuery: restoredState?.query || null,
      restoredPageNumber: restoredState?.page_number || null,
      restoredGroupMessageIds: restoredState?.matching_group_message_ids || null,
      preNavigationPageMembershipSha256: pagePlan.page_membership_sha256,
      restoredPageMembershipSha256:
        restoredState?.restored_page_membership_sha256 || null,
      headerMatchCount: marked.group_match_count,
      headerButtonMatchCount: marked.header_button_match_count,
      returnStateVerified: restored,
    });
    const validation = validateForumGroupHeaderNavigationEvidence(evidence, probeRow, {
      parentForumChannelId,
      pageMembershipSha256: pagePlan.page_membership_sha256,
    });
    if (!validation.valid || evidence.evidence_key !== evidenceKey) {
      throw forumNavigationEvidenceError(
        `Forum navigation evidence failed validation: ${validation.errors.join(",")}`,
      );
    }
    await persistForumGroupNavigationCheckpoint(
      pageCheckpointDirectory,
      evidence,
      probeRow,
      {
        parentForumChannelId,
        pageMembershipSha256: pagePlan.page_membership_sha256,
      },
    );
    evidenceMap[evidenceKey] = evidence;
    newlyCheckpointedGroups += 1;
  }
  return evidenceMap;
}

async function readActiveSearchState(tab) {
  return await tab.playwright.evaluate(() => {
    const searchBox = document.querySelector('[role="combobox"][aria-label="Search"]');
    const queryValue = searchBox
      ? String(searchBox.value || searchBox.getAttribute("value") || searchBox.textContent || "").trim()
      : "";
    const region = document.querySelector('[aria-label="Search Results"]');
    const first = region?.querySelector('[role="listitem"]') || null;
    const firstIndex = first ? Number(first.getAttribute("aria-posinset") || 0) : 0;
    const total = first ? Number(first.getAttribute("aria-setsize") || 0) : 0;
    return {
      query: queryValue,
      currentPage: firstIndex > 0 ? Math.floor((firstIndex - 1) / 25) + 1 : 0,
      total,
      visible: region ? region.querySelectorAll('[role="listitem"]').length : 0,
      status: (region?.innerText || "").slice(0, 300),
    };
  });
}

async function beginSearch(tab, query, options = {}) {
  await tab.playwright.domSnapshot();
  let searchBox = tab.playwright.getByRole("combobox", { name: "Search", exact: true });
  let searchBoxCount = await searchBox.count();
  // Discord can report DOMContentLoaded before its channel header is mounted.
  // Re-observe the page between bounded retries so a freshly reloaded slice
  // does not fail merely because the Search control is still rendering.
  for (let attempt = 0; searchBoxCount === 0 && attempt < 4; attempt += 1) {
    await tab.playwright.waitForTimeout(1500);
    await tab.playwright.domSnapshot();
    searchBox = tab.playwright.getByRole("combobox", { name: "Search", exact: true });
    searchBoxCount = await searchBox.count();
  }
  if (searchBoxCount !== 1) throw new Error(`Search box count ${searchBoxCount}`);
  if (options.reuseActiveSearch === true) {
    const active = await readActiveSearchState(tab);
    if (active.query === query && active.currentPage > 0 && active.total > 0) {
      const observedAtUtc = new Date().toISOString();
      return {
        ...active,
        reused_active_search: true,
        search_submission: {
          mode: "reuse_active_positive",
          query,
          submission_count: 0,
          submitted_at_utc: null,
          observed_at_utc: observedAtUtc,
        },
        search_observations: [
          {
            sequence: 1,
            observed_at_utc: observedAtUtc,
            state: "positive",
            visible_result_count: active.visible,
            reported_total: active.total,
            current_page: active.currentPage,
            panel_text: active.status || "",
          },
        ],
      };
    }
  }
  const oldFirstId = await tab.playwright.evaluate(() => {
    const element = document.querySelector(
      '[aria-label="Search Results"] [role="listitem"] [data-list-item-id^="NO_LIST___"]',
    );
    return element ? (element.getAttribute("data-list-item-id") || "").replace("NO_LIST___", "") : null;
  });
  await searchBox.fill(query);
  await tab.playwright.domSnapshot();
  const filledBox = tab.playwright.getByRole("combobox", { name: "Search", exact: true });
  const filledBoxCount = await filledBox.count();
  if (filledBoxCount !== 1) throw new Error(`Search box changed to count ${filledBoxCount}`);
  const submittedAtUtc = new Date().toISOString();
  await filledBox.press("Enter");
  if (oldFirstId) {
    await tab.playwright
      .locator(`#search-result-${oldFirstId}`)
      .waitFor({ state: "detached", timeoutMs: 15000 })
      .catch(() => {});
  }
  let firstItemCount = 0;
  let lastStatus = "";
  let stableEmptyObservations = 0;
  let stableEmptyEvidence = [];
  const searchObservations = [];
  for (let attempt = 0; attempt < SEARCH_OBSERVATION_LIMIT && firstItemCount === 0; attempt += 1) {
    await tab.playwright.waitForTimeout(1500);
    await tab.playwright.domSnapshot();
    const firstItem = tab.playwright.locator(
      '[aria-label="Search Results"] [role="listitem"][aria-posinset="1"]',
    );
    firstItemCount = await firstItem.count();
    if (firstItemCount === 1) break;
    lastStatus = await tab.playwright.evaluate(() => {
      const region = document.querySelector('[aria-label="Search Results"]');
      return (region?.innerText || "").slice(0, 300);
    });
    const state = classifySearchPanelText(lastStatus);
    const observation = {
      sequence: searchObservations.length + 1,
      observed_at_utc: new Date().toISOString(),
      state,
      visible_result_count: firstItemCount,
      panel_text: lastStatus,
    };
    searchObservations.push(observation);
    if (state === "error") {
      throw new DiscordSearchStateError(`Discord search error state: ${lastStatus}`, "search_error");
    }
    if (state === "empty_candidate") {
      stableEmptyObservations += 1;
      stableEmptyEvidence.push({
        ...observation,
        sequence: stableEmptyObservations,
      });
      if (stableEmptyObservations >= REQUIRED_STABLE_EMPTY_OBSERVATIONS) {
        return {
          total: 0,
          visible: 0,
          empty: true,
          empty_observations: stableEmptyObservations,
          currentPage: 0,
          reused_active_search: false,
          search_submission: {
            mode: "fresh",
            query,
            submission_count: 1,
            submitted_at_utc: submittedAtUtc,
          },
          search_observations: searchObservations,
          stable_empty_observations: stableEmptyEvidence,
        };
      }
    } else {
      stableEmptyObservations = 0;
      stableEmptyEvidence = [];
    }
  }
  if (firstItemCount !== 1) {
    throw new DiscordSearchStateError(
      `Search produced no result row after ${SEARCH_OBSERVATION_LIMIT} observations: ${lastStatus}`,
      "search_state_unresolved",
    );
  }
  const result = await tab.playwright.evaluate(() => {
    const first = document.querySelector('[aria-label="Search Results"] [role="listitem"]');
    return {
      total: first ? Number(first.getAttribute("aria-setsize") || 0) : 0,
      visible: document.querySelectorAll('[aria-label="Search Results"] [role="listitem"]').length,
      currentPage: first
        ? Math.floor((Number(first.getAttribute("aria-posinset") || 1) - 1) / 25) + 1
        : 0,
      status: (document.querySelector('[aria-label="Search Results"]')?.innerText || "").slice(0, 300),
    };
  });
  const observedAtUtc = new Date().toISOString();
  searchObservations.push({
    sequence: searchObservations.length + 1,
    observed_at_utc: observedAtUtc,
    state: "positive",
    visible_result_count: result.visible,
    reported_total: result.total,
    current_page: result.currentPage,
    panel_text: result.status || "",
  });
  return {
    ...result,
    reused_active_search: false,
    search_submission: {
      mode: "fresh",
      query,
      submission_count: 1,
      submitted_at_utc: submittedAtUtc,
    },
    search_observations: searchObservations,
  };
}

export async function countSearch(tab, query) {
  return await beginSearch(tab, query);
}

async function sha256File(path) {
  return crypto.createHash("sha256").update(await fs.readFile(path)).digest("hex");
}

async function writeJsonExclusiveAtomic(filePath, payload) {
  const temporaryPath = `${filePath}.next-${Date.now()}-${Math.random().toString(16).slice(2)}`;
  try {
    await fs.writeFile(temporaryPath, JSON.stringify(payload), { flag: "wx" });
    await fs.link(temporaryPath, filePath);
  } finally {
    await fs.unlink(temporaryPath).catch(() => {});
  }
}

export async function verifySegmentCompletionEvidence(tab, artifactPath, options = {}) {
  let sourceBytes;
  let artifact;
  try {
    sourceBytes = await fs.readFile(artifactPath);
    artifact = JSON.parse(sourceBytes.toString("utf8"));
  } catch (error) {
    if (error?.code === "ENOENT") {
      throw new Error("Completion-evidence sidecars require an existing declared-complete segment artifact");
    }
    throw error;
  }
  if (!artifact || artifact.complete !== true || !artifact.segment?.query) {
    throw new Error("Completion-evidence sidecars require an existing declared-complete segment artifact");
  }
  if (
    !artifact.requested_container ||
    typeof artifact.requested_container !== "object" ||
    Array.isArray(artifact.requested_container)
  ) {
    throw new Error("Source artifact requested_container must be an object");
  }
  const sourceArtifactSha256 = crypto.createHash("sha256").update(sourceBytes).digest("hex");
  const rows = Array.isArray(artifact.messages) ? artifact.messages : [];
  const reportedTotal = Number(artifact.reported_total);
  const reportedPages = Number(artifact.reported_pages);
  if (
    !Number.isInteger(reportedTotal) ||
    reportedTotal < 0 ||
    !Number.isInteger(reportedPages) ||
    reportedPages !== Math.ceil(reportedTotal / 25) ||
    rows.length !== reportedTotal
  ) {
    throw new Error("Source artifact is not structurally eligible for completion-evidence revalidation");
  }
  const search = await beginSearch(tab, artifact.segment.query, { reuseActiveSearch: false });
  if (search.total !== reportedTotal) {
    throw new Error(
      `Fresh revalidation total drifted: artifact=${reportedTotal}, observed=${search.total}`,
    );
  }
  let completionEvidence;
  if (reportedTotal === 0) {
    completionEvidence = {
      schema_version: "1.0.0",
      query: artifact.segment.query,
      reported_total: 0,
      reported_pages: 0,
      terminal_state: "stable_empty",
      search_submission: search.search_submission || null,
      search_observations: Array.isArray(search.search_observations)
        ? search.search_observations
        : [],
      stable_empty: {
        required_observations: REQUIRED_STABLE_EMPTY_OBSERVATIONS,
        observations: Array.isArray(search.stable_empty_observations)
          ? search.stable_empty_observations
          : [],
      },
      stable_bottom: null,
    };
  } else {
    if (reportedPages > 1) {
      await gotoSearchPage(
        tab,
        reportedPages,
        reportedPages,
        options.pageDelayMs ?? 1200,
        {
          maxSteps: options.resumeNavigationMaxSteps ?? 80,
          visibleTimeoutMs: options.searchPageVisibleTimeoutMs ?? 30000,
        },
      );
    }
    completionEvidence = await observeStableBottom(
      tab,
      artifact.segment.query,
      reportedTotal,
      reportedPages,
      {
        stableBottomObservationDelayMs: options.stableBottomObservationDelayMs,
        searchSubmission: search.search_submission,
        searchObservations: search.search_observations,
      },
    );
  }
  const validation = validateCompletionEvidence(
    completionEvidence,
    artifact.segment.query,
    reportedTotal,
    reportedPages,
  );
  if (!validation.valid) {
    throw new Error(`Completion-evidence sidecar validation failed: ${validation.errors.join(",")}`);
  }
  const sidecarPath =
    options.sidecarPath || completionEvidenceSidecarPath(artifactPath);
  if ((await sha256File(artifactPath)) !== sourceArtifactSha256) {
    throw new Error("Source artifact changed during completion-evidence revalidation");
  }
  const sidecar = {
    artifact_type: "discord_segment_completion_evidence_sidecar",
    schema_version: "1.0.0",
    created_at_utc: new Date().toISOString(),
    source_artifact_path: nodePath.basename(String(artifactPath)),
    source_artifact_sha256: sourceArtifactSha256,
    guild_id: artifact.guild_id || GUILD_ID,
    requested_container: artifact.requested_container || null,
    segment: artifact.segment,
    reported_total: reportedTotal,
    reported_pages: reportedPages,
    completion_evidence: completionEvidence,
  };
  try {
    await writeJsonExclusiveAtomic(sidecarPath, sidecar);
  } catch (error) {
    if (error?.code === "EEXIST") {
      throw new Error(`Refusing to overwrite completion-evidence sidecar: ${sidecarPath}`);
    }
    throw error;
  }
  return { sidecarPath, sidecar };
}

function validateRows(rows, total) {
  const unique = new Set(rows.map((row) => row.message_id)).size;
  const indices = new Set(rows.map((row) => row.result_index));
  const gaps = [];
  for (let index = 1; index <= total; index += 1) {
    if (!indices.has(index)) gaps.push(index);
  }
  return { unique, gaps };
}

export function validateExtractedPage(pageRows, pageNumber, total, existingRows = []) {
  const expectedStart = (pageNumber - 1) * 25 + 1;
  const expectedEnd = Math.min(total, pageNumber * 25);
  const expectedIndices =
    expectedEnd >= expectedStart
      ? Array.from({ length: expectedEnd - expectedStart + 1 }, (_, index) => expectedStart + index)
      : [];
  const capturedIndices = pageRows.map((row) => Number(row.result_index || 0));
  const capturedIndexSet = new Set(capturedIndices);
  const expectedIndexSet = new Set(expectedIndices);
  const capturedIds = pageRows.map((row) => String(row.message_id || ""));
  const duplicateMessageIds = Array.from(
    capturedIds.reduce((counts, id) => counts.set(id, (counts.get(id) || 0) + 1), new Map()),
  )
    .filter(([id, count]) => id && count > 1)
    .map(([id]) => id);
  const existingIds = new Set(existingRows.map((row) => String(row.message_id || "")).filter(Boolean));
  const existingIndices = new Set(existingRows.map((row) => Number(row.result_index || 0)).filter((value) => value > 0));
  const validation = {
    page_number: pageNumber,
    expected_count: expectedIndices.length,
    captured_count: pageRows.length,
    missing_indices: expectedIndices.filter((index) => !capturedIndexSet.has(index)),
    unexpected_indices: capturedIndices.filter((index) => !expectedIndexSet.has(index)),
    duplicate_message_ids: duplicateMessageIds,
    overlap_message_ids: Array.from(new Set(capturedIds.filter((id) => id && existingIds.has(id)))),
    overlap_indices: Array.from(new Set(capturedIndices.filter((index) => existingIndices.has(index)))),
  };
  return {
    ...validation,
    valid:
      validation.captured_count === validation.expected_count &&
      validation.missing_indices.length === 0 &&
      validation.unexpected_indices.length === 0 &&
      validation.duplicate_message_ids.length === 0 &&
      validation.overlap_message_ids.length === 0 &&
      validation.overlap_indices.length === 0,
  };
}

function exactSearchResultMessageId(value) {
  const match = String(value || "").match(/^NO_LIST___(\d{15,22})$/);
  return match ? match[1] : null;
}

async function observeForumPreNavigationMembership(tab, query, pageNumber) {
  return await tab.playwright.evaluate(
    (arg) => {
      const region = document.querySelector('[aria-label="Search Results"]');
      const searchBox = document.querySelector('[role="combobox"][aria-label="Search"]');
      const first = region?.querySelector('[role="listitem"]') || null;
      const groups = Array.from(region?.querySelectorAll('[role="group"]') || []).map((group) => {
        const headers = Array.from(group.querySelectorAll(':scope > [role="button"]'));
        const items = Array.from(group.querySelectorAll(':scope > [role="listitem"]')).map((item) => {
          const articles = Array.from(
            item.querySelectorAll(':scope [role="article"][data-list-item-id^="NO_LIST___"]'),
          );
          const article = articles.length === 1 ? articles[0] : null;
          const dataListItemId = article?.getAttribute("data-list-item-id") || null;
          const messageId = String(dataListItemId || "").replace("NO_LIST___", "") || null;
          return {
            listitem_id: item.getAttribute("id") || null,
            result_index: Number(item.getAttribute("aria-posinset") || 0),
            article_count: articles.length,
            article_id: article?.getAttribute("id") || null,
            article_data_list_item_id: dataListItemId,
            message_id: messageId,
            article_closest_group_is_owner: article?.closest('[role="group"]') === group,
          };
        });
        return {
          direct_header_button_count: headers.length,
          direct_listitem_count: items.length,
          items,
        };
      });
      return {
        query: String(
          searchBox?.value || searchBox?.getAttribute("value") || searchBox?.textContent || "",
        ).trim(),
        page_number: first ? Math.floor((Number(first.getAttribute("aria-posinset") || 1) - 1) / 25) + 1 : 0,
        groups,
      };
    },
    { query, pageNumber },
  );
}

export function validateForumPreNavigationMembership(
  observation,
  pageRows,
  query,
  pageNumber,
  total,
) {
  const errors = [];
  const expectedCount = Math.max(0, Math.min(25, Number(total) - (Number(pageNumber) - 1) * 25));
  const groups = Array.isArray(observation?.groups) ? observation.groups : [];
  if (observation?.query !== query) errors.push("forum_pre_navigation_query_mismatch");
  if (Number(observation?.page_number) !== Number(pageNumber)) {
    errors.push("forum_pre_navigation_page_mismatch");
  }
  if (groups.length === 0 && expectedCount > 0) errors.push("forum_pre_navigation_groups_missing");

  const observedRows = [];
  const groupSummaries = [];
  for (const [groupIndex, group] of groups.entries()) {
    const items = Array.isArray(group?.items) ? group.items : [];
    if (Number(group?.direct_header_button_count) !== 1) {
      errors.push(`forum_pre_navigation_header_not_unique:${groupIndex + 1}`);
    }
    if (Number(group?.direct_listitem_count) !== items.length) {
      errors.push(`forum_pre_navigation_direct_listitem_count_mismatch:${groupIndex + 1}`);
    }
    if (items.length === 0) errors.push(`forum_pre_navigation_group_empty:${groupIndex + 1}`);
    const messageIds = [];
    for (const item of items) {
      const messageId = exactSearchResultMessageId(item?.article_data_list_item_id);
      if (Number(item?.article_count) !== 1) {
        errors.push(`forum_pre_navigation_article_count_invalid:${groupIndex + 1}`);
      }
      if (!messageId) errors.push(`forum_pre_navigation_article_message_id_invalid:${groupIndex + 1}`);
      if (item?.article_id !== (messageId ? `search-result-${messageId}` : null)) {
        errors.push(`forum_pre_navigation_article_id_mismatch:${groupIndex + 1}`);
      }
      if (item?.article_closest_group_is_owner !== true) {
        errors.push(`forum_pre_navigation_article_group_owner_mismatch:${groupIndex + 1}`);
      }
      if (!Number.isInteger(Number(item?.result_index)) || Number(item?.result_index) < 1) {
        errors.push(`forum_pre_navigation_result_index_invalid:${groupIndex + 1}`);
      }
      if (messageId) {
        messageIds.push(messageId);
        observedRows.push({ message_id: messageId, result_index: Number(item.result_index) });
      }
    }
    if (new Set(messageIds).size !== messageIds.length) {
      errors.push(`forum_pre_navigation_group_message_ids_not_unique:${groupIndex + 1}`);
    }
    groupSummaries.push({
      message_ids: messageIds.slice().sort(),
      direct_header_button_count: Number(group?.direct_header_button_count),
    });
  }

  if (observedRows.length !== expectedCount) {
    errors.push("forum_pre_navigation_page_row_count_mismatch");
  }
  if (new Set(observedRows.map((row) => row.message_id)).size !== observedRows.length) {
    errors.push("forum_pre_navigation_page_message_ids_not_unique");
  }
  const pageRowMap = new Map(pageRows.map((row) => [String(row?.message_id || ""), row]));
  if (pageRowMap.size !== pageRows.length || pageRows.length !== expectedCount) {
    errors.push("forum_pre_navigation_extracted_page_row_count_mismatch");
  }
  for (const observed of observedRows) {
    const row = pageRowMap.get(observed.message_id);
    if (!row) {
      errors.push("forum_pre_navigation_dom_row_missing_from_extraction");
      continue;
    }
    if (Number(row.result_index) !== observed.result_index) {
      errors.push("forum_pre_navigation_result_index_mismatch");
    }
    const membership = normalizedForumGroupMessageIds(row.forum_group_message_ids);
    const expectedGroup = groupSummaries.find((group) => group.message_ids.includes(observed.message_id));
    if (
      row.forum_group_membership_exact !== true ||
      !membership ||
      !expectedGroup ||
      JSON.stringify(membership) !== JSON.stringify(expectedGroup.message_ids)
    ) {
      errors.push("forum_pre_navigation_extracted_membership_mismatch");
    }
  }
  for (const row of pageRows) {
    if (!observedRows.some((observed) => observed.message_id === String(row?.message_id || ""))) {
      errors.push("forum_pre_navigation_extracted_row_missing_from_dom");
    }
  }
  const canonical = {
    groups: groupSummaries,
    rows: observedRows
      .map((row) => ({ ...row }))
      .sort((left, right) => left.result_index - right.result_index || left.message_id.localeCompare(right.message_id)),
  };
  return {
    valid: errors.length === 0,
    errors: Array.from(new Set(errors)),
    expected_count: expectedCount,
    observed_count: observedRows.length,
    canonical,
  };
}

export function forumPreNavigationMembershipSignature(validation) {
  if (!validation?.valid) return null;
  return JSON.stringify(validation.canonical);
}

export function forumPageMembershipSha256(query, pageNumber, reportedTotal, canonical) {
  const exactQuery = String(query || "").trim();
  const exactPage = Number(pageNumber);
  const exactTotal = Number(reportedTotal);
  if (
    !exactQuery ||
    !Number.isInteger(exactPage) ||
    exactPage < 1 ||
    !Number.isInteger(exactTotal) ||
    exactTotal < 1 ||
    !canonical ||
    !Array.isArray(canonical.groups) ||
    !Array.isArray(canonical.rows)
  ) {
    return null;
  }
  return crypto
    .createHash("sha256")
    .update(
      JSON.stringify({
        query: exactQuery,
        page_number: exactPage,
        reported_total: exactTotal,
        canonical,
      }),
    )
    .digest("hex");
}

export function buildForumNavigationPagePlan({
  query,
  pageNumber,
  reportedTotal,
  canonical,
  observedAtUtc = new Date().toISOString(),
} = {}) {
  const groups = Array.isArray(canonical?.groups) ? canonical.groups : [];
  const expectedEvidenceKeys = groups
    .map((group) => forumGroupEvidenceKey(query, pageNumber, group?.message_ids))
    .filter(Boolean)
    .sort();
  return {
    schema_version: "1.0.0",
    artifact_type: "discord_forum_navigation_page_plan",
    query: String(query || "").trim(),
    page_number: Number(pageNumber),
    reported_total: Number(reportedTotal),
    page_membership_sha256: forumPageMembershipSha256(
      query,
      pageNumber,
      reportedTotal,
      canonical,
    ),
    expected_group_count: groups.length,
    expected_message_count: Array.isArray(canonical?.rows) ? canonical.rows.length : 0,
    expected_group_evidence_keys: expectedEvidenceKeys,
    canonical,
    observed_at_utc: observedAtUtc,
    immutable: true,
  };
}

export function validateForumNavigationPagePlan(plan, expected = {}) {
  const errors = [];
  if (!plan || typeof plan !== "object" || Array.isArray(plan)) {
    return { valid: false, errors: ["forum_navigation_page_plan_missing"] };
  }
  if (plan.schema_version !== "1.0.0") errors.push("forum_navigation_page_plan_schema_invalid");
  if (plan.artifact_type !== "discord_forum_navigation_page_plan") {
    errors.push("forum_navigation_page_plan_type_invalid");
  }
  if (plan.immutable !== true) errors.push("forum_navigation_page_plan_not_immutable");
  if (!Number.isFinite(Date.parse(String(plan.observed_at_utc || "")))) {
    errors.push("forum_navigation_page_plan_timestamp_invalid");
  }
  const recomputedHash = forumPageMembershipSha256(
    plan.query,
    plan.page_number,
    plan.reported_total,
    plan.canonical,
  );
  if (!recomputedHash || plan.page_membership_sha256 !== recomputedHash) {
    errors.push("forum_navigation_page_plan_hash_invalid");
  }
  const expectedKeys = (Array.isArray(plan.canonical?.groups) ? plan.canonical.groups : [])
    .map((group) => forumGroupEvidenceKey(plan.query, plan.page_number, group?.message_ids))
    .filter(Boolean)
    .sort();
  if (
    JSON.stringify(plan.expected_group_evidence_keys) !== JSON.stringify(expectedKeys) ||
    plan.expected_group_count !== expectedKeys.length ||
    plan.expected_message_count !== (Array.isArray(plan.canonical?.rows) ? plan.canonical.rows.length : 0)
  ) {
    errors.push("forum_navigation_page_plan_bindings_invalid");
  }
  for (const [field, value] of [
    ["query", expected.query],
    ["page_number", expected.pageNumber],
    ["reported_total", expected.reportedTotal],
    ["page_membership_sha256", expected.pageMembershipSha256],
  ]) {
    if (value !== undefined && plan[field] !== value) {
      errors.push(`forum_navigation_page_plan_${field}_mismatch`);
    }
  }
  return { valid: errors.length === 0, errors: Array.from(new Set(errors)) };
}

export async function persistForumNavigationPagePlan(baseDirectory, plan) {
  if (!baseDirectory) throw new Error("Forum navigation page plans require a checkpoint directory");
  const validation = validateForumNavigationPagePlan(plan);
  if (!validation.valid) {
    throw forumNavigationEvidenceError(
      `New immutable forum page plan failed validation: ${validation.errors.join(",")}`,
    );
  }
  const pageDirectory = nodePath.join(
    baseDirectory,
    `page_${String(plan.page_number).padStart(3, "0")}`,
  );
  const pagePlanPath = nodePath.join(pageDirectory, "page_plan.json");
  await fs.mkdir(pageDirectory, { recursive: true });
  try {
    await writeJsonExclusiveAtomic(pagePlanPath, plan);
    return { pageDirectory, pagePlanPath, pagePlan: plan, reused: false };
  } catch (error) {
    if (error?.code !== "EEXIST") throw error;
    const existing = await readJsonIfPresent(pagePlanPath);
    const existingValidation = validateForumNavigationPagePlan(existing, {
      query: plan.query,
      pageNumber: plan.page_number,
      reportedTotal: plan.reported_total,
      pageMembershipSha256: plan.page_membership_sha256,
    });
    if (!existingValidation.valid) {
      throw forumNavigationEvidenceError(
        `Existing immutable forum page plan failed validation: ${existingValidation.errors.join(",")}`,
      );
    }
    return { pageDirectory, pagePlanPath, pagePlan: existing, reused: true };
  }
}

export function validateForumPageNavigationCoverage(
  pageRows,
  evidenceMap,
  pagePlan,
  options = {},
) {
  const errors = [];
  const planValidation = validateForumNavigationPagePlan(pagePlan);
  if (!planValidation.valid) errors.push(...planValidation.errors);
  const expectedKeys = Array.isArray(pagePlan?.expected_group_evidence_keys)
    ? [...pagePlan.expected_group_evidence_keys].sort()
    : [];
  const actualKeys = evidenceMap instanceof Map
    ? Array.from(evidenceMap.keys()).sort()
    : evidenceMap && typeof evidenceMap === "object"
      ? Object.keys(evidenceMap).sort()
      : [];
  if (JSON.stringify(actualKeys) !== JSON.stringify(expectedKeys)) {
    errors.push("forum_navigation_page_evidence_key_set_mismatch");
  }
  for (const evidenceKey of expectedKeys) {
    const evidence = evidenceMap instanceof Map
      ? evidenceMap.get(evidenceKey)
      : evidenceMap?.[evidenceKey];
    const probeRow = pageRows.find(
      (row) => deriveDiscordForumGroupMembershipFields(row).forum_group_membership_key === evidenceKey,
    );
    const validation = validateForumGroupHeaderNavigationEvidence(evidence, probeRow, {
      parentForumChannelId: options.parentForumChannelId,
      pageMembershipSha256: pagePlan?.page_membership_sha256,
    });
    if (!validation.valid) {
      errors.push(...validation.errors.map((error) => `${evidenceKey}:${error}`));
    }
  }
  return {
    valid: errors.length === 0,
    errors: Array.from(new Set(errors)),
    expected_group_count: expectedKeys.length,
    validated_group_count: expectedKeys.filter((key) => actualKeys.includes(key)).length,
    page_membership_sha256: pagePlan?.page_membership_sha256 || null,
  };
}

export async function extractPageValidated(tab, pageNumber, query, total, existingRows, options = {}) {
  const retries = Number(options.pageValidationRetries ?? 2);
  const retryDelayMs = Number(options.pageValidationRetryDelayMs ?? 1500);
  if (!Number.isInteger(retries) || retries < 0) throw new Error("pageValidationRetries must be a non-negative integer");
  if (!Number.isFinite(retryDelayMs) || retryDelayMs < 0) {
    throw new Error("pageValidationRetryDelayMs must be non-negative");
  }
  let lastValidation = null;
  for (let attempt = 0; attempt <= retries; attempt += 1) {
    const isForum = options.channelKind === "forum channel";
    const requiredSamples = isForum ? 2 : 1;
    const samples = [];
    for (let sampleNumber = 0; sampleNumber < requiredSamples; sampleNumber += 1) {
      await tab.playwright.domSnapshot();
      const pageRows = options.extractPageForTesting
        ? await options.extractPageForTesting(tab, pageNumber, query, sampleNumber, attempt)
        : await extractPage(tab, pageNumber, query);
      const extracted = validateExtractedPage(pageRows, pageNumber, total, existingRows);
      let forumPreNavigation = null;
      if (isForum) {
        const observation = options.observeForumPreNavigationForTesting
          ? await options.observeForumPreNavigationForTesting(
              tab,
              query,
              pageNumber,
              sampleNumber,
              attempt,
            )
          : await observeForumPreNavigationMembership(tab, query, pageNumber);
        forumPreNavigation = validateForumPreNavigationMembership(
          observation,
          pageRows,
          query,
          pageNumber,
          total,
        );
      }
      samples.push({ pageRows, extracted, forumPreNavigation });
      if (!extracted.valid || (forumPreNavigation && !forumPreNavigation.valid)) break;
    }
    const completedSamples = samples.length === requiredSamples;
    const stableForumSamples =
      !isForum ||
      (completedSamples &&
        samples.every((sample) => sample.forumPreNavigation?.valid) &&
        new Set(
          samples.map((sample) => forumPreNavigationMembershipSignature(sample.forumPreNavigation)),
        ).size === 1);
    const selected = samples.at(-1) || null;
    lastValidation = {
      ...(selected?.extracted || validateExtractedPage([], pageNumber, total, existingRows)),
      forum_pre_navigation: selected?.forumPreNavigation || null,
      forum_samples_required: requiredSamples,
      forum_samples_captured: samples.length,
      forum_samples_stable: stableForumSamples,
      valid: Boolean(selected?.extracted?.valid && stableForumSamples),
    };
    const pageRows = selected?.pageRows || [];
    if (lastValidation.valid) {
      if (
        isForum &&
        options.captureForumGroupNavigationEvidence !== false
      ) {
        const checkpointDirectory = String(
          options.forumGroupNavigationCheckpointDirectory || "",
        ).trim();
        if (!checkpointDirectory) {
          throw new Error(
            "Forum navigation requires forumGroupNavigationCheckpointDirectory",
          );
        }
        const pagePlanCandidate = buildForumNavigationPagePlan({
          query,
          pageNumber,
          reportedTotal: total,
          canonical: selected.forumPreNavigation.canonical,
        });
        const pagePlanState = await persistForumNavigationPagePlan(
          checkpointDirectory,
          pagePlanCandidate,
        );
        const evidenceMap = options.forumGroupNavigationEvidenceMap ||
          (await collectForumGroupHeaderNavigationEvidence(
            tab,
            pageRows,
            query,
            pageNumber,
            {
              ...options,
              forumNavigationReportedTotal: total,
              forumNavigationPagePlan: pagePlanState.pagePlan,
              forumNavigationPageCheckpointDirectory: pagePlanState.pageDirectory,
            },
          ));
        const pageCoverage = validateForumPageNavigationCoverage(
          pageRows,
          evidenceMap,
          pagePlanState.pagePlan,
          { parentForumChannelId: options.channelId },
        );
        if (!pageCoverage.valid) {
          throw forumNavigationEvidenceError(
            `Forum page evidence is incomplete: ${pageCoverage.errors.join(",")}`,
          );
        }
        const resolvedRows = pageRows.map((row) => {
          const attached = attachForumGroupHeaderNavigationEvidence(row, evidenceMap, {
            parentForumChannelId: options.channelId,
          });
          const withThread = deriveDiscordThreadFields(attached);
          return deriveDiscordReplyFields(withThread, withThread.inferred_thread_channel_id);
        });
        const unresolved = resolvedRows.filter(
          (row) =>
            row.forum_group_navigation_validation?.valid !== true ||
            row.thread_channel_id_exact !== true ||
            ![
              "forum_group_header_data_list_item_id",
              "forum_group_header_navigation_exact",
            ].includes(row.thread_channel_id_source),
        );
        if (unresolved.length > 0) {
          throw forumNavigationEvidenceError(
            `Exact forum group navigation did not resolve ${unresolved.length} page rows`,
          );
        }
        return resolvedRows;
      }
      return pageRows;
    }
    if (attempt < retries && retryDelayMs > 0) await tab.playwright.waitForTimeout(retryDelayMs);
  }
  throw new DiscordPageValidationError(pageNumber, lastValidation, retries + 1);
}

async function gotoSearchPage(tab, targetPage, totalPages, stepDelayMs = 0, options = {}) {
  if (targetPage < 1 || targetPage > totalPages) {
    throw new Error(`Target page ${targetPage} outside 1-${totalPages}`);
  }
  const maxSteps = Number(options.maxSteps ?? 80);
  const visibleTimeoutMs = Number(options.visibleTimeoutMs ?? 30000);
  if (!Number.isInteger(maxSteps) || maxSteps < 1) {
    throw new Error("resumeNavigationMaxSteps must be a positive integer");
  }
  if (!Number.isFinite(visibleTimeoutMs) || visibleTimeoutMs <= 0) {
    throw new Error("searchPageVisibleTimeoutMs must be positive");
  }
  let lastPage = 0;
  for (let navigationStep = 0; navigationStep < maxSteps; navigationStep += 1) {
    await tab.playwright.domSnapshot();
    const state = await tab.playwright.evaluate(() => {
      const first = document.querySelector('[aria-label="Search Results"] [role="listitem"]');
      const firstIndex = first ? Number(first.getAttribute("aria-posinset") || 0) : 0;
      const pageNumbers = Array.from(
        document.querySelectorAll(
          '[aria-label="Search Results"] ~ navigation button, [aria-label="Search Results"] ~ navigation [role="button"], [aria-label="Search Results"] navigation button, [aria-label="Search Results"] navigation [role="button"]',
        ),
      )
        .map((button) => (button.getAttribute("aria-label") || "").match(/^Page (\d+)$/))
        .filter(Boolean)
        .map((match) => Number(match[1]));
      if (pageNumbers.length === 0) {
        for (const button of Array.from(document.querySelectorAll('button,[role="button"]'))) {
          const label = button.getAttribute("aria-label") || "";
          const match = label.match(/^Page (\d+)$/);
          if (match) pageNumbers.push(Number(match[1]));
        }
      }
      if (pageNumbers.length === 0) {
        const pagination = Array.from(document.querySelectorAll("nav")).find((nav) => {
          const buttonText = Array.from(nav.querySelectorAll('button,[role="button"]')).map((button) =>
            (button.innerText || "").trim(),
          );
          return buttonText.includes("Back") && buttonText.includes("Next");
        });
        if (pagination) {
          for (const button of Array.from(pagination.querySelectorAll('button,[role="button"]'))) {
            const text = (button.innerText || "").trim();
            if (/^\d+$/.test(text)) pageNumbers.push(Number(text));
          }
        }
      }
      return {
        currentPage: firstIndex > 0 ? Math.floor((firstIndex - 1) / 25) + 1 : 0,
        pageNumbers: Array.from(new Set(pageNumbers)),
        hasNext: Array.from(document.querySelectorAll('button,[role="button"]')).some(
          (button) => (button.innerText || "").trim() === "Next" && !button.disabled && button.getAttribute("aria-disabled") !== "true",
        ),
        hasBack: Array.from(document.querySelectorAll('button,[role="button"]')).some(
          (button) => (button.innerText || "").trim() === "Back" && !button.disabled && button.getAttribute("aria-disabled") !== "true",
        ),
        status: (document.querySelector('[aria-label="Search Results"]')?.innerText || "").slice(0, 300),
      };
    });
    lastPage = state.currentPage;
    if (state.currentPage === targetPage) return { reached: true, currentPage: state.currentPage };
    if (state.currentPage === 0) throw new Error(`Search page navigation lost results: ${state.status}`);
    const navigation = choosePaginationControl(
      state.currentPage,
      targetPage,
      state.pageNumbers,
      state.hasNext,
      state.hasBack,
    );
    if (!navigation) {
      throw new Error(
        `No adjacent page control while targeting page ${targetPage}: current=${state.currentPage}, visible=${state.pageNumbers.join(",")}, status=${state.status}`,
      );
    }
    const { nextPage } = navigation;
    const pageButton = tab.playwright.getByRole("button", { name: navigation.accessibleName, exact: true });
    const pageButtonCount = await pageButton.count();
    if (pageButtonCount !== 1) throw new Error(`Navigation to page ${nextPage} button count ${pageButtonCount}`);
    if (navigationStep > 0 && stepDelayMs > 0) await tab.playwright.waitForTimeout(stepDelayMs);
    await pageButton.click();
    lastPage = nextPage;
    const expectedIndex = (nextPage - 1) * 25 + 1;
    const expectedItem = tab.playwright.locator(
      `[aria-label="Search Results"] [role="listitem"][aria-posinset="${expectedIndex}"]`,
    );
    await expectedItem.waitFor({ state: "visible", timeoutMs: visibleTimeoutMs });
  }
  throw new DiscordResumeNavigationPending(lastPage, targetPage, maxSteps);
}

export function enrichCollectedRow(row, options = {}) {
  const isForumChannel = options.channelKind === "forum channel";
  const exactParentForumConflict = Boolean(
    isForumChannel &&
      options.channelId &&
      row.group_header_parent_forum_channel_id &&
      row.group_header_parent_forum_channel_id !== options.channelId,
  );
  const permalinkChannelId = isForumChannel
    ? row.inferred_thread_channel_id || null
    : options.channelId || row.inferred_thread_channel_id || null;
  const exactAttachmentOwnerChannelId = isForumChannel
    ? row.thread_channel_id_exact === true && row.thread_channel_id_conflict !== true
      ? row.inferred_thread_channel_id || null
      : null
    : options.channelId ||
      (row.thread_channel_id_exact === true && row.thread_channel_id_conflict !== true
        ? row.inferred_thread_channel_id
        : null);
  const forumPermalinkStatuses = {
    forum_group_header_data_list_item_id: "thread_id_from_forum_group_header",
    forum_group_header_navigation_exact: "thread_id_from_forum_group_header_navigation",
    owned_reply_permalink: "thread_id_from_owned_reply_permalink",
    attachment_cdn_path_unverified: "thread_id_from_unverified_attachment",
    legacy_inferred_container_id: "thread_id_from_legacy_inference",
  };
  const withExactReply = deriveDiscordReplyFields(row, permalinkChannelId);
  const withAttachmentRelations = deriveDiscordAttachmentFields(
    withExactReply,
    exactAttachmentOwnerChannelId,
  );
  const enriched = {
    ...withAttachmentRelations,
    collection_channel_id: options.channelId || null,
    collection_channel_name: options.channelName || null,
    collection_channel_kind: options.channelKind || null,
    collection_category_name: options.categoryName || null,
    collection_channel_id_source: options.channelIdSource || null,
    exact_permalink: permalinkChannelId
      ? `https://discord.com/channels/${GUILD_ID}/${permalinkChannelId}/${row.message_id}`
      : null,
    exact_permalink_status: permalinkChannelId
      ? isForumChannel
        ? forumPermalinkStatuses[row.thread_channel_id_source] || "thread_id_from_other_exact_source"
        : "exact_inventoried_channel_id"
      : "thread_id_unresolved",
    exact_parent_forum_conflict_detected: exactParentForumConflict,
    exact_permalink_conflict_detected: Boolean(row.thread_channel_id_conflict || exactParentForumConflict),
  };
  return deriveDiscordSystemEventFields(enriched, options.channelKind);
}

export async function collectSegment(tab, segment, outputDirectory, options = {}) {
  const checkpointEvery = options.checkpointEvery || 5;
  const pageDelayMs = options.pageDelayMs ?? 1200;
  const maxPagesPerCall = Number(options.maxPagesPerCall ?? Number.POSITIVE_INFINITY);
  if (
    !(
      maxPagesPerCall === Number.POSITIVE_INFINITY ||
      (Number.isInteger(maxPagesPerCall) && maxPagesPerCall > 0)
    )
  ) {
    throw new Error("maxPagesPerCall must be a positive integer or Infinity");
  }
  await fs.mkdir(outputDirectory, { recursive: true });
  const persistedSegment = {
    ...segment,
    timezone: segment.timezone || options.timezone || "America/Chicago",
  };
  const { partialPath, finalPath } = segmentPaths(segment, outputDirectory, options);
  const existingFinal = await readJsonIfPresent(finalPath);
  if (existingFinal) {
    return await summarizeExistingComplete(existingFinal, persistedSegment, finalPath, options);
  }
  const collectionStartedAtUtc = new Date().toISOString();
  let prior = null;
  try {
    prior = await readJsonIfPresent(partialPath);
  } catch {}
  const priorRowsCandidate = Array.isArray(prior?.messages) ? prior.messages : [];
  let search = await beginSearch(tab, segment.query, {
    reuseActiveSearch: options.reuseActiveSearch === true && priorRowsCandidate.length > 0,
  });
  const enrichRow = (row) => enrichCollectedRow(row, options);
  let rows = [];
  let pages = Math.ceil(search.total / 25);
  let completionEvidence =
    search.total === 0
      ? {
          schema_version: "1.0.0",
          query: segment.query,
          reported_total: 0,
          reported_pages: 0,
          terminal_state: "stable_empty",
          search_submission: search.search_submission || null,
          search_observations: Array.isArray(search.search_observations)
            ? search.search_observations
            : [],
          stable_empty: {
            required_observations: REQUIRED_STABLE_EMPTY_OBSERVATIONS,
            observations: Array.isArray(search.stable_empty_observations)
              ? search.stable_empty_observations
              : [],
          },
          stable_bottom: null,
        }
      : null;
  let resumedFromPartialRows = 0;
  let resumePage = pages === 0 ? 0 : 1;
  let resumedCompatiblePartial = false;
  if (prior) {
    const priorRows = priorRowsCandidate;
    const priorPages = new Set(priorRows.map((row) => Number(row.page_number || 0)).filter((page) => page > 0));
    const maxPriorPage = priorPages.size ? Math.max(...priorPages) : 0;
    const pagesContiguous = Array.from({ length: maxPriorPage }, (_, index) => index + 1).every((page) =>
      priorPages.has(page),
    );
    const priorValidation = validateRows(priorRows, priorRows.length);
    const priorForumNavigationCompatible =
      !requiresForumGroupNavigationEvidence(options) ||
      priorRows.every((row) => rowHasExactForumGroupNavigationEvidence(row, options));
    const expectedRowsThroughPriorPage =
      maxPriorPage === pages ? search.total : Math.min(search.total, maxPriorPage * 25);
    const compatible =
      prior.collector_version === COLLECTOR_VERSION &&
      prior.segment?.query === segment.query &&
      prior.reported_total === search.total &&
      prior.complete === false &&
      priorRows.length > 0 &&
      priorRows.length === expectedRowsThroughPriorPage &&
      pagesContiguous &&
      priorForumNavigationCompatible &&
      priorValidation.unique === priorRows.length &&
      priorValidation.gaps.length === 0 &&
      priorRows.every(
        (row) =>
          (row.collection_channel_id || null) === (options.channelId || null) &&
          (row.collection_channel_name || null) === (options.channelName || null),
      );
    if (compatible) {
      rows = priorRows;
      resumedFromPartialRows = priorRows.length;
      resumePage = maxPriorPage;
      resumedCompatiblePartial = true;
    } else {
      throw new Error(`Refusing to overwrite incompatible partial artifact: ${partialPath}`);
    }
  } else {
    if (search.reused_active_search) {
      search = await beginSearch(tab, segment.query, { reuseActiveSearch: false });
      pages = Math.ceil(search.total / 25);
      resumePage = pages === 0 ? 0 : 1;
    }
    rows = (
      await extractPageValidated(tab, 1, segment.query, search.total, [], options)
    ).map(enrichRow);
  }

  const checkpoint = async (pageNumber, complete = false) => {
    const validation = validateRows(rows, complete ? search.total : rows.length);
    const completionValidation = validateCompletionEvidence(
      completionEvidence,
      segment.query,
      search.total,
      pages,
    );
    const validatedComplete =
      complete &&
      completionValidation.valid &&
      rows.length === search.total &&
      validation.unique === search.total &&
      validation.gaps.length === 0;
    const containerMismatches = options.channelName
      ? rows.filter(
          (row) => row.thread_title !== options.channelName && row.parent_channel !== options.channelName,
        )
      : [];
    const forumNavigationFailures = requiresForumGroupNavigationEvidence(options)
      ? rows.filter((row) => !rowHasExactForumGroupNavigationEvidence(row, options))
      : [];
    const forumNavigationEvidenceMap = forumGroupNavigationEvidenceMapFromRows(rows, options);
    const forumNavigationPagePlans = requiresForumGroupNavigationEvidence(options)
      ? forumNavigationPagePlansFromRows(rows, options)
      : {};
    const payload = {
      collector_version: COLLECTOR_VERSION,
      guild_id: GUILD_ID,
      collection_scope: options.scope || "query-defined",
      collection_started_at_utc: collectionStartedAtUtc,
      captured_at_utc: new Date().toISOString(),
      resumed_from_partial_rows: resumedFromPartialRows,
      requested_container: {
        channel_id: options.channelId || null,
        channel_name: options.channelName || null,
        channel_kind: options.channelKind || null,
        category_name: options.categoryName || null,
        channel_id_source: options.channelIdSource || null,
      },
      segment: persistedSegment,
      reported_total: search.total,
      reported_pages: pages,
      pages_captured: pageNumber,
      captured_rows: rows.length,
      unique_message_ids: validation.unique,
      gap_indices: complete ? validation.gaps : [],
      container_mismatch_count: containerMismatches.length,
      container_mismatch_message_ids: containerMismatches.slice(0, 100).map((row) => row.message_id),
      forum_group_navigation_contract_version: requiresForumGroupNavigationEvidence(options)
        ? "1.1.0"
        : null,
      forum_group_navigation_checkpoint_directory: requiresForumGroupNavigationEvidence(options)
        ? options.forumGroupNavigationCheckpointDirectoryArtifactPath || null
        : null,
      forum_group_navigation_checkpoint_count: Object.keys(forumNavigationEvidenceMap).length,
      forum_group_navigation_page_plans: forumNavigationPagePlans,
      forum_group_navigation_page_acceptance: requiresForumGroupNavigationEvidence(options)
        ? "all_groups_exact_before_page_acceptance"
        : null,
      forum_group_header_navigation_exact: forumNavigationEvidenceMap,
      forum_group_navigation_unresolved_count: forumNavigationFailures.length,
      forum_group_navigation_unresolved_message_ids: forumNavigationFailures
        .slice(0, 100)
        .map((row) => row.message_id),
      completion_evidence: completionEvidence,
      completion_evidence_validation: completionValidation,
      complete:
        validatedComplete &&
        containerMismatches.length === 0 &&
        forumNavigationFailures.length === 0,
      messages: rows,
    };
    await writeJsonAtomic(payload.complete ? finalPath : partialPath, payload);
  };

  // A navigation-only retry must leave a compatible raw checkpoint byte-for-byte
  // unchanged. The next checkpoint is written only after a new page is extracted.
  if (!resumedCompatiblePartial) {
    await checkpoint(resumePage, pages === 0);
  }
  let extractedPagesThisCall = 0;
  for (let pageNumber = resumePage + 1; pageNumber <= pages; pageNumber += 1) {
    if (pageDelayMs > 0) await tab.playwright.waitForTimeout(pageDelayMs);
    await gotoSearchPage(tab, pageNumber, pages, pageDelayMs, {
      maxSteps: options.resumeNavigationMaxSteps ?? 80,
      visibleTimeoutMs: options.searchPageVisibleTimeoutMs ?? 30000,
    });
    const pageRows = (
      await extractPageValidated(tab, pageNumber, segment.query, search.total, rows, options)
    ).map(enrichRow);
    rows.push(...pageRows);
    extractedPagesThisCall += 1;
    let checkpointWritten = false;
    if (pageNumber % checkpointEvery === 0 || pageNumber === pages) {
      await checkpoint(pageNumber, false);
      checkpointWritten = true;
    }
    if (pageNumber < pages && extractedPagesThisCall >= maxPagesPerCall) {
      if (!checkpointWritten) await checkpoint(pageNumber, false);
      throw new DiscordExtractionBatchPending(pageNumber, pages, maxPagesPerCall);
    }
  }

  if (search.total > 0) {
    completionEvidence = await observeStableBottom(tab, segment.query, search.total, pages, {
      stableBottomObservationDelayMs: options.stableBottomObservationDelayMs,
      searchSubmission: search.search_submission,
      searchObservations: search.search_observations,
    });
    await checkpoint(pages, true);
  }

  const validation = validateRows(rows, search.total);
  const containerMismatches = options.channelName
    ? rows.filter((row) => row.thread_title !== options.channelName && row.parent_channel !== options.channelName)
    : [];
  const forumNavigationFailures = requiresForumGroupNavigationEvidence(options)
    ? rows.filter((row) => !rowHasExactForumGroupNavigationEvidence(row, options))
    : [];
  if (
    rows.length !== search.total ||
    validation.unique !== search.total ||
    validation.gaps.length !== 0 ||
    containerMismatches.length !== 0 ||
    forumNavigationFailures.length !== 0
  ) {
    throw new Error(
      `Segment validation failed: reported=${search.total}, captured=${rows.length}, unique=${validation.unique}, gaps=${validation.gaps.slice(0, 20).join(",")}, container_mismatches=${containerMismatches.length}, forum_navigation_unresolved=${forumNavigationFailures.length}`,
    );
  }
  await fs.unlink(partialPath).catch(() => {});
  return {
    start: segment.start,
    end: segment.end,
    reported: search.total,
    captured: rows.length,
    unique: validation.unique,
    gaps: validation.gaps.length,
    pages,
    finalPath,
  };
}

export async function collectSegmentResilient(tab, segment, outputDirectory, options = {}) {
  const maxAttempts = options.maxAttempts || 3;
  const retryDelayMs = options.retryDelayMs ?? 10000;
  let lastError;
  for (let attempt = 1; attempt <= maxAttempts; attempt += 1) {
    try {
      const result = await collectSegment(tab, segment, outputDirectory, options);
      return { ...result, attempts: attempt };
    } catch (error) {
      lastError = error;
      // Search-error and page-transition timeouts are usually account-wide
      // throttling signals. Retrying them immediately consumes more quota and
      // can turn a healthy checkpoint into a long retry storm. Let the batch
      // scheduler pause the range instead; an explicit opt-in remains for
      // one-off manual recovery.
      if (isThrottleLikeError(error) && options.retryThrottleErrors !== true) throw error;
      if (attempt < maxAttempts) await tab.playwright.waitForTimeout(retryDelayMs);
    }
  }
  throw new Error(
    `Segment ${segment.start} through ${segment.end} failed after ${maxAttempts} attempts: ${lastError?.message || lastError}`,
  );
}

async function partialCheckpointSummary(path) {
  try {
    const payload = await readJsonIfPresent(path);
    if (!payload) return { exists: false };
    return {
      exists: true,
      complete: payload.complete === true,
      reported_total: Number(payload.reported_total || 0),
      reported_pages: Number(payload.reported_pages || 0),
      pages_captured: Number(payload.pages_captured || 0),
      captured_rows: Number(payload.captured_rows || 0),
      unique_message_ids: Number(payload.unique_message_ids || 0),
      gap_indices: Array.isArray(payload.gap_indices) ? payload.gap_indices : [],
      captured_at_utc: payload.captured_at_utc || null,
    };
  } catch (error) {
    return { exists: true, unreadable: true, error: String(error?.message || error) };
  }
}

export async function collectSegmentsBatched(
  tab,
  segments,
  outputDirectory,
  collectorOptions = {},
  schedulerOptions = {},
) {
  const batchSize = Number(schedulerOptions.batchSize ?? 3);
  const cooldownMs = Number(schedulerOptions.cooldownMs ?? 60000);
  const throttleCooldownMs = Number(schedulerOptions.throttleCooldownMs ?? 300000);
  const maxSegments = Number(schedulerOptions.maxSegments ?? Number.POSITIVE_INFINITY);
  if (!Number.isInteger(batchSize) || batchSize < 1) throw new Error("batchSize must be a positive integer");
  if (!Number.isFinite(cooldownMs) || cooldownMs < 0) throw new Error("cooldownMs must be non-negative");
  if (!Number.isFinite(throttleCooldownMs) || throttleCooldownMs < 0) {
    throw new Error("throttleCooldownMs must be non-negative");
  }
  if (!(maxSegments === Number.POSITIVE_INFINITY || (Number.isInteger(maxSegments) && maxSegments > 0))) {
    throw new Error("maxSegments must be a positive integer or Infinity");
  }

  const collectFn = schedulerOptions.collectFn || collectSegmentResilient;
  const sleepFn =
    schedulerOptions.sleepFn || (async (milliseconds) => tab.playwright.waitForTimeout(milliseconds));
  const progressFn = schedulerOptions.onProgress || (async () => {});
  await fs.mkdir(outputDirectory, { recursive: true });

  const skipped = [];
  const conflicts = [];
  const pending = [];
  for (const segment of segments) {
    const { finalPath } = segmentPaths(segment, outputDirectory, collectorOptions);
    let existing;
    try {
      existing = await readJsonIfPresent(finalPath);
      if (existing) {
        skipped.push(await summarizeExistingComplete(existing, segment, finalPath, collectorOptions));
      } else {
        pending.push(segment);
      }
    } catch (error) {
      conflicts.push({
        start: segment.start,
        end: segment.end,
        finalPath,
        error: String(error?.message || error),
      });
    }
  }

  const completed = [];
  const failures = [];
  const cooldowns = [];
  let cursor = 0;
  let attempted = 0;
  let pausedOn = null;
  while (cursor < pending.length && attempted < maxSegments) {
    let batchAttempts = 0;
    while (batchAttempts < batchSize && cursor < pending.length && attempted < maxSegments) {
      const segment = pending[cursor];
      cursor += 1;
      attempted += 1;
      batchAttempts += 1;
      try {
        const result = await collectFn(tab, segment, outputDirectory, collectorOptions);
        completed.push(result);
        await progressFn({ event: "segment_complete", segment, result });
      } catch (error) {
        const { partialPath } = segmentPaths(segment, outputDirectory, collectorOptions);
        const throttleLike = isThrottleLikeError(error);
        const failure = {
          start: segment.start,
          end: segment.end,
          query: segment.query,
          error: String(error?.message || error),
          throttle_like: throttleLike,
          partial_checkpoint: await partialCheckpointSummary(partialPath),
        };
        failures.push(failure);
        await progressFn({ event: "segment_failed", segment, failure });
        if (throttleLike) {
          pausedOn = segment;
          break;
        }
      }
    }
    if (pausedOn) break;
    if (cursor < pending.length && attempted < maxSegments && cooldownMs > 0) {
      const cooldown = {
        after_attempted_segments: attempted,
        duration_ms: cooldownMs,
      };
      cooldowns.push(cooldown);
      await progressFn({ event: "batch_cooldown", ...cooldown });
      await sleepFn(cooldownMs);
    }
  }

  const stoppedByLimit = !pausedOn && cursor < pending.length && attempted >= maxSegments;
  const remainingSegments = pending.length - cursor + (pausedOn ? 1 : 0);
  const status = conflicts.length
    ? "blocked_conflict"
    : pausedOn
      ? "paused_throttled"
      : stoppedByLimit
        ? "paused_limit"
        : failures.length
          ? "partial"
          : "complete";
  return {
    status,
    total_segments: segments.length,
    skipped_complete_segments: skipped.length,
    attempted_segments: attempted,
    completed_segments: completed.length,
    failed_segments: failures.length,
    remaining_segments: remainingSegments,
    paused_on_segment: pausedOn,
    recommended_cooldown_ms: pausedOn ? throttleCooldownMs : 0,
    skipped,
    completed,
    failures,
    conflicts,
    cooldowns,
  };
}

export async function collectDateRange(
  tab,
  {
    startIso,
    endIso,
    outputDirectory,
    queryPrefix = "in:premium-journals",
    spanDays = 1,
    collectorOptions = {},
    schedulerOptions = {},
  },
) {
  if (!startIso || !endIso || !outputDirectory) {
    throw new Error("startIso, endIso, and outputDirectory are required");
  }
  const segments = makeSegments(startIso, endIso, spanDays, queryPrefix);
  return await collectSegmentsBatched(
    tab,
    segments,
    outputDirectory,
    collectorOptions,
    schedulerOptions,
  );
}

export async function collectSimpleSearch(tab, query, outputPath, options = {}) {
  const segment = {
    start: options.start || "unknown",
    end: options.end || "unknown",
    query,
  };
  const directory = outputPath.replace(/[/\\][^/\\]+$/, "");
  const prefix = outputPath.replace(/^.*[/\\]/, "").replace(/\.json$/, "");
  return await collectSegment(tab, segment, directory, { prefix, checkpointEvery: options.checkpointEvery || 5 });
}
