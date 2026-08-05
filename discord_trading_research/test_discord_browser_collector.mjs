import assert from "node:assert/strict";
import fs from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import test from "node:test";

import {
  DiscordExtractionBatchPending,
  DiscordForumNavigationBatchPending,
  DiscordResumeNavigationPending,
  DiscordSearchStateError,
  choosePaginationControl,
  choosePaginationStep,
  classifySearchPanelText,
  attachForumGroupHeaderNavigationEvidence,
  buildForumGroupHeaderNavigationEvidence,
  buildForumGroupNavigationCheckpoint,
  buildForumNavigationPagePlan,
  collectDateRange,
  collectSegment,
  collectSegmentResilient,
  collectSegmentsBatched,
  countSearch,
  deriveDiscordAttachmentFields,
  deriveDiscordAuthorFields,
  deriveDiscordForumGroupMembershipFields,
  deriveDiscordReplyFields,
  deriveDiscordSnowflakeFields,
  deriveDiscordSystemEventFields,
  deriveDiscordThreadFields,
  enrichCollectedRow,
  extractPageValidated,
  forumGroupEvidenceKey,
  forumGroupMembershipSha256,
  forumGroupNavigationCheckpointFilename,
  forumPageMembershipSha256,
  makeSegments,
  persistForumGroupNavigationCheckpoint,
  persistForumNavigationPagePlan,
  readForumGroupNavigationCheckpoint,
  validateForumPreNavigationMembership,
  validateForumGroupNavigationCheckpoint,
  validateForumNavigationPagePlan,
  validateForumPageNavigationCoverage,
  validateCompletionEvidence,
  validateExtractedPage,
  verifySegmentCompletionEvidence,
} from "./discord_browser_collector.mjs";


const CHANNEL_ID = "1273692573898113076";
const CHANNEL_NAME = "❓│questions";
const PREFIX = `channel_questions_${CHANNEL_ID}`;
const FORUM_SOURCE_URL =
  "https://discord.com/channels/1167376964680691732/1283941772577472643";

function exactForumReturnFields(query, pageNumber, messageIds, sourceUrl = FORUM_SOURCE_URL) {
  return {
    sourceUrl,
    backUrl: sourceUrl,
    restoredQuery: query,
    restoredPageNumber: pageNumber,
    restoredGroupMessageIds: messageIds,
    preNavigationPageMembershipSha256: "a".repeat(64),
    restoredPageMembershipSha256: "a".repeat(64),
  };
}

function collectorOptions() {
  return {
    prefix: PREFIX,
    checkpointEvery: 1,
    pageDelayMs: 0,
    channelId: CHANNEL_ID,
    channelName: CHANNEL_NAME,
    channelKind: "text channel",
    categoryName: "PREMIUM",
    channelIdSource: "navigation_inventory",
    scope: "channel-scoped",
  };
}

function artifactPaths(root, segment) {
  const stem = `${PREFIX}_${segment.start}_${segment.end}`;
  return {
    finalPath: path.join(root, `${stem}.json`),
    partialPath: path.join(root, `${stem}.partial.json`),
  };
}

function completePayload(segment, messages = []) {
  const observed = [1, 2, 3].map((sequence) => ({
    sequence,
    observed_at_utc: `2026-07-21T00:00:0${sequence}.000Z`,
    state: "empty_candidate",
    visible_result_count: 0,
    panel_text: "No Results",
  }));
  return {
    collector_version: "test",
    guild_id: "1167376964680691732",
    collection_scope: "channel-scoped",
    requested_container: {
      channel_id: CHANNEL_ID,
      channel_name: CHANNEL_NAME,
      channel_kind: "text channel",
      category_name: "PREMIUM",
      channel_id_source: "navigation_inventory",
    },
    segment,
    reported_total: messages.length,
    reported_pages: Math.ceil(messages.length / 25),
    pages_captured: Math.ceil(messages.length / 25),
    captured_rows: messages.length,
    unique_message_ids: new Set(messages.map((row) => row.message_id)).size,
    gap_indices: [],
    container_mismatch_count: 0,
    completion_evidence: {
      schema_version: "1.0.0",
      query: segment.query,
      reported_total: 0,
      reported_pages: 0,
      terminal_state: "stable_empty",
      search_submission: {
        mode: "fresh",
        query: segment.query,
        submission_count: 1,
        submitted_at_utc: "2026-07-21T00:00:00.000Z",
      },
      search_observations: observed,
      stable_empty: { required_observations: 3, observations: observed },
      stable_bottom: null,
    },
    complete: true,
    messages,
  };
}

function fakeSearchTab(statuses) {
  let statusIndex = 0;
  let evaluateCount = 0;
  let presses = 0;
  const searchBox = {
    count: async () => 1,
    fill: async () => {},
    press: async () => {
      presses += 1;
    },
  };
  return {
    stats: {
      get presses() {
        return presses;
      },
      get statusObservations() {
        return statusIndex;
      },
    },
    playwright: {
      domSnapshot: async () => "",
      waitForTimeout: async () => {},
      getByRole: () => searchBox,
      locator: () => ({
        count: async () => 0,
        waitFor: async () => {},
      }),
      evaluate: async (fn) => {
        evaluateCount += 1;
        const source = String(fn);
        if (evaluateCount === 1) return null;
        if (source.includes("return (region?.innerText")) {
          const value = statuses[Math.min(statusIndex, statuses.length - 1)] || "";
          statusIndex += 1;
          return value;
        }
        if (source.includes("const rows = []")) return [];
        if (source.includes("total: first")) return { total: 0, visible: 0 };
        throw new Error(`Unexpected fake evaluate call: ${source.slice(0, 100)}`);
      },
    },
  };
}

function fakeResumeNavigationTab({ query, total, currentPage = 1 }) {
  let page = currentPage;
  let fills = 0;
  let presses = 0;
  const searchBox = {
    count: async () => 1,
    fill: async () => {
      fills += 1;
    },
    press: async () => {
      presses += 1;
    },
  };
  return {
    stats: {
      get currentPage() {
        return page;
      },
      get fills() {
        return fills;
      },
      get presses() {
        return presses;
      },
    },
    playwright: {
      domSnapshot: async () => "",
      waitForTimeout: async () => {},
      getByRole: (role, options = {}) => {
        if (role === "combobox") return searchBox;
        if (role === "button") {
          return {
            count: async () => 1,
            click: async () => {
              if (options.name === "Next") page += 1;
              else if (options.name === "Back") page -= 1;
              else if (String(options.name || "").startsWith("Page ")) {
                page = Number(String(options.name).slice(5));
              }
            },
          };
        }
        throw new Error(`Unexpected role ${role}`);
      },
      locator: () => ({
        waitFor: async () => {},
      }),
      evaluate: async (fn, arg) => {
        const source = String(fn);
        if (source.includes("stable_bottom_dom_observation")) {
          const firstResultIndex = (page - 1) * 25 + 1;
          return {
            observation_kind: "stable_bottom_dom_observation",
            query,
            visible_result_count: total - firstResultIndex + 1,
            first_result_index: firstResultIndex,
            last_result_index: total,
            current_page: page,
            result_set_size: total,
            result_set_size_candidates: [total],
            has_enabled_next: false,
            panel_text: "",
          };
        }
        if (source.includes("const queryValue = searchBox")) {
          return {
            query,
            currentPage: page,
            total,
            visible: Math.min(25, total - (page - 1) * 25),
            status: "",
          };
        }
        if (source.includes("const pageNumbers = Array.from")) {
          return {
            currentPage: page,
            pageNumbers: [1, page, Math.ceil(total / 25)],
            hasNext: page < Math.ceil(total / 25),
            hasBack: page > 1,
            status: "",
          };
        }
        if (source.includes("const rows = []")) {
          const pageNumber = Number(arg?.pageNumber || page);
          const firstIndex = (pageNumber - 1) * 25 + 1;
          const lastIndex = Math.min(total, firstIndex + 24);
          return Array.from({ length: lastIndex - firstIndex + 1 }, (_, offset) => ({
            message_id: String(20_000 + firstIndex + offset),
            result_index: firstIndex + offset,
            page_number: pageNumber,
            thread_title: CHANNEL_NAME,
            parent_channel: "",
          }));
        }
        throw new Error(`Unexpected fake evaluate call: ${source.slice(0, 120)}`);
      },
    },
  };
}

function checkpointRows(total) {
  return Array.from({ length: total }, (_, index) => ({
    message_id: String(10_000 + index),
    result_index: index + 1,
    page_number: Math.floor(index / 25) + 1,
    collection_channel_id: CHANNEL_ID,
    collection_channel_name: CHANNEL_NAME,
    thread_title: CHANNEL_NAME,
    parent_channel: "",
  }));
}

async function withTempDirectory(run) {
  const root = await fs.mkdtemp(path.join(os.tmpdir(), "discord-collector-test-"));
  try {
    return await run(root);
  } finally {
    await fs.rm(root, { recursive: true, force: true });
  }
}

test("search state classification separates stable empties from errors and pending states", () => {
  assert.equal(classifySearchPanelText("No Results\nFilters (3)"), "empty_candidate");
  assert.equal(classifySearchPanelText("Searching…\nFilters (3)"), "pending");
  assert.equal(
    classifySearchPanelText("No Results\nWe dropped the magnifying glass. Can you try searching again?"),
    "error",
  );
});

test("snowflake timestamps are derived in Node with discrepancy metadata", () => {
  const snowflakeIso = "2026-01-20T15:30:00.000Z";
  const snowflakeMs = Date.parse(snowflakeIso);
  const messageId = (((BigInt(snowflakeMs) - 1420070400000n) << 22n) + 123n).toString();
  const enriched = deriveDiscordSnowflakeFields({
    message_id: messageId,
    timestamp_utc: "2026-01-20T15:30:01.500Z",
  });
  assert.equal(enriched.snowflake_timestamp_utc, snowflakeIso);
  assert.equal(enriched.timestamp_discrepancy_ms, 1500);
});

test("exact Discord Stage system events use a snowflake-qualified timestamp fallback", () => {
  const messageId = "1461035531021582346";
  const timestamp = "2026-01-14T16:33:35.323Z";
  const row = deriveDiscordSystemEventFields(
    {
      message_id: messageId,
      author: "",
      author_id: null,
      timestamp_utc: timestamp,
      snowflake_timestamp_utc: timestamp,
      timestamp_discrepancy_ms: 0,
      timestamp_scope_exact: false,
      content_scope_exact: true,
      article_aria_labelledby: `message-content-${messageId}`,
      content_text: "Powell\nis now a speaker.\n—\n1/14/26, 10:33 AM",
    },
    "stage channel",
  );
  assert.equal(row.message_kind, "discord_stage_system_event");
  assert.equal(row.discord_system_event_exact, true);
  assert.equal(row.discord_system_event_type, "stage_speaker_added");
  assert.equal(row.timestamp_exact_fallback_source, "discord_snowflake_exact_stage_system_event");
});

test("duplicated Stage speaker labels use the existing exact event grammar", () => {
  const messageId = "1473403911636258939";
  const timestamp = new Date(
    Number((BigInt(messageId) >> 22n) + 1420070400000n),
  ).toISOString();
  const base = {
    message_id: messageId,
    author: "",
    author_id: null,
    timestamp_utc: timestamp,
    snowflake_timestamp_utc: timestamp,
    timestamp_discrepancy_ms: 0,
    timestamp_scope_exact: false,
    content_scope_exact: true,
    article_aria_labelledby: `message-content-${messageId}`,
    content_text: "tig\ntig\n ended NY Session\n—\n2/17/26, 8:00 AM",
  };
  const exact = deriveDiscordSystemEventFields(base, "stage channel");
  assert.equal(exact.discord_system_event_exact, true);
  assert.equal(exact.discord_system_event_type, "stage_ended");

  const mismatched = deriveDiscordSystemEventFields(
    { ...base, content_text: "tig\nother\n ended NY Session\n—\n2/17/26, 8:00 AM" },
    "stage channel",
  );
  assert.equal(mismatched.discord_system_event_exact, false);
});

test("Stage timestamp fallback never exempts ordinary or structurally ambiguous rows", () => {
  const messageId = "1461035531021582346";
  const exactTimestamp = "2026-01-14T16:33:35.323Z";
  const base = {
    message_id: messageId,
    author: "",
    author_id: null,
    timestamp_utc: exactTimestamp,
    snowflake_timestamp_utc: exactTimestamp,
    timestamp_discrepancy_ms: 0,
    timestamp_scope_exact: false,
    content_scope_exact: true,
    article_aria_labelledby: `message-content-${messageId}`,
    content_text: "Powell\nis now a speaker.\n—\n1/14/26, 10:33 AM",
  };
  assert.equal(deriveDiscordSystemEventFields(base, "text channel").discord_system_event_exact, false);
  assert.equal(
    deriveDiscordSystemEventFields({ ...base, author: "Powell" }, "stage channel").discord_system_event_exact,
    false,
  );
  assert.equal(
    deriveDiscordSystemEventFields(
      { ...base, timestamp_utc: "2026-01-14T16:33:36.323Z" },
      "stage channel",
    ).discord_system_event_exact,
    false,
  );
  assert.equal(
    deriveDiscordSystemEventFields(
      { ...base, content_text: "Powell\nordinary message\n1/14/26, 10:33 AM" },
      "stage channel",
    ).discord_system_event_exact,
    false,
  );
  assert.equal(
    deriveDiscordSystemEventFields(
      { ...base, timestamp_discrepancy_ms: null },
      "stage channel",
    ).discord_system_event_exact,
    false,
  );
});

test("exact Discord poll-close events use the same narrow snowflake fallback", () => {
  const messageId = "1465470547935760515";
  const timestamp = "2026-01-26T22:16:45.754Z";
  const row = deriveDiscordSystemEventFields(
    {
      message_id: messageId,
      author: "",
      author_id: null,
      timestamp_utc: timestamp,
      snowflake_timestamp_utc: timestamp,
      timestamp_discrepancy_ms: 0,
      timestamp_scope_exact: false,
      content_scope_exact: true,
      article_aria_labelledby:
        `message-content-${messageId} message-accessories-${messageId}`,
      content_text:
        "yarin's poll what boy should play? has closed.\n—\n1/26/26, 4:16 PM\n" +
        "Monday, January 26, 2026 at 4:16 PM\nThe results were tied\n50%",
    },
    "stage channel",
  );
  assert.equal(row.message_kind, "discord_poll_system_event");
  assert.equal(row.discord_system_event_exact, true);
  assert.equal(row.discord_system_event_type, "poll_closed");
  assert.equal(row.timestamp_exact_fallback_source, "discord_snowflake_exact_stage_system_event");

  const winningAnswer = deriveDiscordSystemEventFields(
    {
      ...row,
      message_id: "1480724088875126896",
      timestamp_utc: "2026-03-10T00:28:53.311Z",
      snowflake_timestamp_utc: "2026-03-10T00:28:53.311Z",
      article_aria_labelledby:
        "message-content-1480724088875126896 message-accessories-1480724088875126896",
      content_text:
        "kp's poll Does Erik Hit TP? has closed.\n—\n3/9/26, 7:28 PM\n" +
        "Monday, March 9, 2026 at 7:28 PM\nyes\nWinning answer • 63%",
      message_kind: null,
      discord_system_event_exact: false,
      discord_system_event_type: null,
      timestamp_exact_fallback_source: null,
    },
    "stage channel",
  );
  assert.equal(winningAnswer.discord_system_event_exact, true);
  assert.equal(winningAnswer.discord_system_event_type, "poll_closed");

  const noResults = deriveDiscordSystemEventFields(
    {
      ...row,
      message_kind: null,
      discord_system_event_exact: false,
      discord_system_event_type: null,
      timestamp_exact_fallback_source: null,
      content_text: "yarin's poll what boy should play? has closed.\n1/26/26, 4:16 PM",
    },
    "stage channel",
  );
  assert.equal(noResults.discord_system_event_exact, false);
});

test("exact Discord pinned-message events use only the sole row-owned snowflake timestamp", () => {
  const messageId = "1501683564796973076";
  const timestamp = "2026-05-06T20:34:21.779Z";
  const base = {
    message_id: messageId,
    author: "",
    author_id: null,
    article_id: `search-result-${messageId}`,
    article_aria_labelledby: `message-content-${messageId}`,
    content_scope_exact: true,
    timestamp_scope_exact: false,
    timestamp_utc: timestamp,
    snowflake_timestamp_utc: timestamp,
    timestamp_discrepancy_ms: 0,
    row_owned_time_count: 1,
    row_owned_time_datetime: timestamp,
    row_owned_time_element_id: null,
    content_text:
      "Domme\npinned a message to this channel. See all pinned messages.\n\u2014\n5/6/26, 3:34 PM\n" +
      "Wednesday, May 6, 2026 at 3:34 PM",
  };
  const exact = deriveDiscordSystemEventFields(base, "text channel");
  assert.equal(exact.message_kind, "discord_pinned_message_system_event");
  assert.equal(exact.discord_system_event_exact, true);
  assert.equal(exact.discord_system_event_type, "message_pinned");
  assert.equal(
    exact.timestamp_exact_fallback_source,
    "discord_snowflake_exact_pinned_message_system_event",
  );

  for (const mutation of [
    { article_id: `search-result-1501683564796973077` },
    { article_aria_labelledby: `message-content-${messageId} message-timestamp-${messageId}` },
    { author: "Domme" },
    { row_owned_time_count: 2 },
    { row_owned_time_datetime: "2026-05-06T20:34:22.779Z" },
    { row_owned_time_element_id: `message-timestamp-${messageId}` },
    { timestamp_discrepancy_ms: 1 },
    { timestamp_discrepancy_ms: null },
    { content_text: "Domme\nordinary message\n\u2014\n5/6/26, 3:34 PM\nWednesday, May 6, 2026 at 3:34 PM" },
  ]) {
    assert.equal(
      deriveDiscordSystemEventFields({ ...base, ...mutation }, "text channel")
        .discord_system_event_exact,
      false,
    );
  }
  assert.equal(
    deriveDiscordSystemEventFields(base, "stage channel").discord_system_event_exact,
    false,
  );
});

test("owner-scoped avatar CDN paths provide exact Discord author IDs", () => {
  const enriched = deriveDiscordAuthorFields({
    message_id: "1471146119261327421",
    author: "sample user",
    author_avatar_url:
      "https://cdn.discordapp.com/avatars/733399973949079673/a_0123456789abcdef.webp?size=80",
  });
  assert.equal(enriched.author_id, "733399973949079673");
  assert.equal(enriched.author_id_source, "owner_scoped_avatar_cdn_path");
});

test("exact username-bound data-user-id precedes avatar inference", () => {
  const enriched = deriveDiscordAuthorFields({
    message_id: "1471146119261327421",
    author: "sample user",
    author_id: "858723137612152842",
    author_id_source: "exact_username_bound_data_user_id",
    author_id_candidates: ["858723137612152842"],
    author_id_conflict: false,
    author_avatar_url:
      "https://cdn.discordapp.com/avatars/733399973949079673/a_0123456789abcdef.webp",
  });
  assert.equal(enriched.author_id, "858723137612152842");
  assert.equal(enriched.author_id_source, "exact_username_bound_data_user_id");

  const conflicted = deriveDiscordAuthorFields({
    message_id: "1471146119261327421",
    author_id: "858723137612152842",
    author_id_source: "exact_username_bound_data_user_id",
    author_id_candidates: ["858723137612152842", "733399973949079673"],
    author_id_conflict: true,
    author_avatar_url:
      "https://cdn.discordapp.com/avatars/733399973949079673/a_0123456789abcdef.webp",
  });
  assert.equal(conflicted.author_id, null);
  assert.equal(conflicted.author_id_source, null);
});

test("default Discord avatars do not invent an author ID", () => {
  const enriched = deriveDiscordAuthorFields({
    message_id: "1471146119261327421",
    author: "sample user",
    author_avatar_url: "https://cdn.discordapp.com/embed/avatars/3.png",
  });
  assert.equal(enriched.author_id, null);
  assert.equal(enriched.author_id_source, null);
});

test("owner-scoped guild avatar paths provide exact Discord author IDs", () => {
  const enriched = deriveDiscordAuthorFields({
    message_id: "1471146119261327421",
    author_avatar_url:
      "https://cdn.discordapp.com/guilds/1167376964680691732/users/858723137612152842/avatars/hash.webp",
  });
  assert.equal(enriched.author_id, "858723137612152842");
  assert.equal(enriched.author_avatar_guild_id, "1167376964680691732");
  assert.equal(enriched.author_id_source, "owner_scoped_guild_avatar_cdn_path");
});

test("forum group card identifiers provide exact thread provenance", () => {
  const enriched = deriveDiscordThreadFields({
    message_id: "1527000000000000000",
    group_header_data_list_item_id:
      "forum-channel-list-1283941772577472643___1508933293322801183",
    reply_to_permalink:
      "https://discord.com/channels/1167376964680691732/1508933293322801183/1526999999999999999",
    attachments: [
      {
        thread_channel_id: "1508933293322801183",
        url: "https://cdn.discordapp.com/attachments/1508933293322801183/1526000000000000000/chart.png",
      },
    ],
  });
  assert.equal(enriched.group_header_parent_forum_channel_id, "1283941772577472643");
  assert.equal(enriched.inferred_thread_channel_id, "1508933293322801183");
  assert.equal(enriched.thread_channel_id_source, "forum_group_header_data_list_item_id");
  assert.equal(enriched.thread_channel_id_exact, true);
  assert.equal(enriched.reply_to_channel_id, "1508933293322801183");
  assert.equal(enriched.reply_to_message_id, "1526999999999999999");
  assert.equal(enriched.thread_channel_id_conflict, false);
  assert.equal(
    enriched.inferred_permalink,
    "https://discord.com/channels/1167376964680691732/1508933293322801183/1527000000000000000",
  );
});

test("verified forum group navigation provides exact row-owned thread provenance", () => {
  const parentForumChannelId = "1283941772577472643";
  const threadChannelId = "1508933293322801183";
  const messageIds = ["1527000000000000000", "1527000000000000001"];
  const query = "in:premium-journals after:2026-01-01 before:2026-01-03";
  const row = deriveDiscordForumGroupMembershipFields({
    message_id: messageIds[0],
    page_number: 1,
    search_query: query,
    thread_title: "same mutable title",
    forum_group_message_ids: messageIds,
    forum_group_membership_exact: true,
    attachments: [
      {
        thread_channel_id: threadChannelId,
        attachment_id: "1526999999999999999",
        dom_relation: "exact_message_accessories_descendant",
        href_in_message_content: false,
      },
    ],
  });
  const evidence = buildForumGroupHeaderNavigationEvidence({
    query,
    pageNumber: 1,
    messageIds,
    parentForumChannelId,
    ...exactForumReturnFields(query, 1, messageIds),
    destinationUrl: `https://discord.com/channels/1167376964680691732/${threadChannelId}`,
    observedAtUtc: "2026-07-21T16:30:00.000Z",
  });
  const attached = attachForumGroupHeaderNavigationEvidence(
    row,
    { [evidence.evidence_key]: evidence },
    { parentForumChannelId },
  );
  const withThread = deriveDiscordThreadFields(attached);
  assert.equal(attached.forum_group_navigation_validation.valid, true);
  assert.equal(withThread.inferred_thread_channel_id, threadChannelId);
  assert.equal(withThread.thread_channel_id_source, "forum_group_header_navigation_exact");
  assert.equal(withThread.thread_channel_id_exact, true);

  const enriched = enrichCollectedRow(withThread, {
    channelId: parentForumChannelId,
    channelName: "premium-journals",
    channelKind: "forum channel",
    categoryName: "PREMIUM",
  });
  assert.equal(
    enriched.exact_permalink_status,
    "thread_id_from_forum_group_header_navigation",
  );
  assert.equal(enriched.attachments[0].ownership_status, "owned_exact");
});

test("forum navigation requires exact source URL restoration", () => {
  const parentForumChannelId = "1283941772577472643";
  const threadChannelId = "1508933293322801183";
  const messageIds = ["1527000000000000000", "1527000000000000001"];
  const query = "in:premium-journals after:2026-01-01 before:2026-01-03";
  const row = deriveDiscordForumGroupMembershipFields({
    message_id: messageIds[0],
    page_number: 1,
    search_query: query,
    forum_group_message_ids: messageIds,
    forum_group_membership_exact: true,
  });
  const evidence = buildForumGroupHeaderNavigationEvidence({
    query,
    pageNumber: 1,
    messageIds,
    parentForumChannelId,
    sourceUrl: FORUM_SOURCE_URL,
    backUrl: `https://discord.com/channels/1167376964680691732/${threadChannelId}`,
    restoredQuery: query,
    restoredPageNumber: 1,
    restoredGroupMessageIds: messageIds,
    destinationUrl: `https://discord.com/channels/1167376964680691732/${threadChannelId}`,
    observedAtUtc: "2026-07-21T16:30:00.000Z",
  });
  const attached = attachForumGroupHeaderNavigationEvidence(
    row,
    { [evidence.evidence_key]: evidence },
    { parentForumChannelId },
  );
  assert.equal(attached.forum_group_navigation_validation.valid, false);
  assert.ok(
    attached.forum_group_navigation_validation.errors.includes(
      "forum_navigation_source_url_not_restored",
    ),
  );
});

test("immutable forum group checkpoints bind exact query page membership and navigation", () => {
  const parentForumChannelId = "1283941772577472643";
  const threadChannelId = "1508933293322801183";
  const messageIds = ["1527000000000000000", "1527000000000000001"];
  const query = "in:premium-journals after:2026-01-01 before:2026-01-03";
  const row = deriveDiscordForumGroupMembershipFields({
    message_id: messageIds[0],
    page_number: 1,
    search_query: query,
    forum_group_message_ids: messageIds,
    forum_group_membership_exact: true,
  });
  const evidence = buildForumGroupHeaderNavigationEvidence({
    query,
    pageNumber: 1,
    messageIds,
    parentForumChannelId,
    ...exactForumReturnFields(query, 1, messageIds),
    destinationUrl: `https://discord.com/channels/1167376964680691732/${threadChannelId}`,
    observedAtUtc: "2026-07-21T16:30:00.000Z",
  });
  const checkpoint = buildForumGroupNavigationCheckpoint(evidence);
  const validation = validateForumGroupNavigationCheckpoint(checkpoint, row, {
    parentForumChannelId,
  });
  assert.equal(validation.valid, true);
  assert.equal(
    checkpoint.restored_group_membership_sha256,
    forumGroupMembershipSha256(query, 1, messageIds),
  );
  assert.equal(
    forumGroupNavigationCheckpointFilename(checkpoint.evidence_key),
    `forum_group_navigation_${checkpoint.evidence_key.split(":")[1]}.json`,
  );

  const tampered = structuredClone(checkpoint);
  tampered.back_url =
    "https://discord.com/channels/1167376964680691732/1509487946565292152";
  const tamperedValidation = validateForumGroupNavigationCheckpoint(tampered, row, {
    parentForumChannelId,
  });
  assert.equal(tamperedValidation.valid, false);
  assert.ok(
    tamperedValidation.errors.includes("forum_navigation_checkpoint_back_url_mismatch"),
  );

  const pending = new DiscordForumNavigationBatchPending(2, 3, 7, 1);
  assert.equal(pending.code, "forum_navigation_batch_pending");
  assert.equal(pending.completedGroups, 3);
  assert.equal(pending.totalGroups, 7);
});

test("file-backed forum page plans and group checkpoints resume fail-closed", async () => {
  await withTempDirectory(async (root) => {
    const parentForumChannelId = "1283941772577472643";
    const query = "in:premium-journals after:2026-01-01 before:2026-01-03";
    const groupA = ["1527000000000000000", "1527000000000000001"];
    const groupB = ["1527000000000000002", "1527000000000000003"];
    const canonical = {
      groups: [groupA, groupB].map((messageIds) => ({
        message_ids: [...messageIds].sort(),
        direct_header_button_count: 1,
      })),
      rows: [...groupA, ...groupB].map((messageId, index) => ({
        message_id: messageId,
        result_index: index + 1,
      })),
    };
    const plan = buildForumNavigationPagePlan({
      query,
      pageNumber: 1,
      reportedTotal: 4,
      canonical,
      observedAtUtc: "2026-07-21T16:29:00.000Z",
    });
    assert.equal(validateForumNavigationPagePlan(plan).valid, true);
    assert.equal(
      plan.page_membership_sha256,
      forumPageMembershipSha256(query, 1, 4, canonical),
    );
    const planState = await persistForumNavigationPagePlan(root, plan);
    assert.equal(planState.reused, false);
    const planResume = await persistForumNavigationPagePlan(
      root,
      buildForumNavigationPagePlan({
        query,
        pageNumber: 1,
        reportedTotal: 4,
        canonical,
        observedAtUtc: "2026-07-21T16:29:30.000Z",
      }),
    );
    assert.equal(planResume.reused, true);

    const makeRow = (messageIds) =>
      deriveDiscordForumGroupMembershipFields({
        message_id: messageIds[0],
        page_number: 1,
        search_query: query,
        forum_group_message_ids: messageIds,
        forum_group_membership_exact: true,
      });
    const makeEvidence = (messageIds, threadChannelId) =>
      buildForumGroupHeaderNavigationEvidence({
        query,
        pageNumber: 1,
        messageIds,
        parentForumChannelId,
        ...exactForumReturnFields(query, 1, messageIds),
        preNavigationPageMembershipSha256: plan.page_membership_sha256,
        restoredPageMembershipSha256: plan.page_membership_sha256,
        destinationUrl: `https://discord.com/channels/1167376964680691732/${threadChannelId}`,
        observedAtUtc: "2026-07-21T16:30:00.000Z",
      });
    const rowA = makeRow(groupA);
    const rowB = makeRow(groupB);
    const evidenceA = makeEvidence(groupA, "1508933293322801183");
    const evidenceB = makeEvidence(groupB, "1509487946565292152");
    const checkpointOptions = {
      parentForumChannelId,
      pageMembershipSha256: plan.page_membership_sha256,
    };
    const savedA = await persistForumGroupNavigationCheckpoint(
      planState.pageDirectory,
      evidenceA,
      rowA,
      checkpointOptions,
    );
    assert.equal(savedA.reused, false);
    const resumedA = await persistForumGroupNavigationCheckpoint(
      planState.pageDirectory,
      evidenceA,
      rowA,
      checkpointOptions,
    );
    assert.equal(resumedA.reused, true);
    assert.equal(
      (
        await readForumGroupNavigationCheckpoint(
          planState.pageDirectory,
          evidenceA.evidence_key,
          rowA,
          checkpointOptions,
        )
      ).checkpoint.evidence.thread_channel_id,
      evidenceA.thread_channel_id,
    );

    const partialPath = path.join(root, "segment.partial.json");
    const partialBytes = Buffer.from("accepted-page-checkpoint");
    await fs.writeFile(partialPath, partialBytes);
    const invalidB = structuredClone(evidenceB);
    invalidB.back_url =
      "https://discord.com/channels/1167376964680691732/1508933293322801183";
    invalidB.source_url_restored = false;
    await assert.rejects(
      persistForumGroupNavigationCheckpoint(
        planState.pageDirectory,
        invalidB,
        rowB,
        checkpointOptions,
      ),
      /New immutable forum checkpoint failed validation/,
    );
    const childSourceUrl =
      "https://discord.com/channels/1167376964680691732/1508933293322801183";
    const childSurfaceB = buildForumGroupHeaderNavigationEvidence({
      query,
      pageNumber: 1,
      messageIds: groupB,
      parentForumChannelId,
      sourceUrl: childSourceUrl,
      backUrl: childSourceUrl,
      restoredQuery: query,
      restoredPageNumber: 1,
      restoredGroupMessageIds: groupB,
      preNavigationPageMembershipSha256: plan.page_membership_sha256,
      restoredPageMembershipSha256: plan.page_membership_sha256,
      destinationUrl:
        "https://discord.com/channels/1167376964680691732/1509487946565292152",
      observedAtUtc: "2026-07-21T16:30:30.000Z",
    });
    await assert.rejects(
      persistForumGroupNavigationCheckpoint(
        planState.pageDirectory,
        childSurfaceB,
        rowB,
        checkpointOptions,
      ),
      /forum_navigation_source_not_parent_forum/,
    );
    assert.equal((await fs.readFile(partialPath)).equals(partialBytes), true);
    await assert.rejects(
      fs.stat(
        path.join(
          planState.pageDirectory,
          forumGroupNavigationCheckpointFilename(evidenceB.evidence_key),
        ),
      ),
      { code: "ENOENT" },
    );

    const incompleteCoverage = validateForumPageNavigationCoverage(
      [rowA, rowB],
      { [evidenceA.evidence_key]: evidenceA },
      plan,
      { parentForumChannelId },
    );
    assert.equal(incompleteCoverage.valid, false);
    assert.ok(
      incompleteCoverage.errors.includes("forum_navigation_page_evidence_key_set_mismatch"),
    );
    const completeCoverage = validateForumPageNavigationCoverage(
      [rowA, rowB],
      {
        [evidenceA.evidence_key]: evidenceA,
        [evidenceB.evidence_key]: evidenceB,
      },
      plan,
      { parentForumChannelId },
    );
    assert.equal(completeCoverage.valid, true);

    const changedCanonical = structuredClone(canonical);
    changedCanonical.groups[1].message_ids = [
      "1527000000000000002",
      "1527000000000000004",
    ];
    changedCanonical.rows[3].message_id = "1527000000000000004";
    await assert.rejects(
      persistForumNavigationPagePlan(
        root,
        buildForumNavigationPagePlan({
          query,
          pageNumber: 1,
          reportedTotal: 4,
          canonical: changedCanonical,
        }),
      ),
      /Existing immutable forum page plan failed validation/,
    );

    const corrupted = structuredClone(savedA.checkpoint);
    corrupted.thread_channel_id = "1509487946565292152";
    await fs.writeFile(savedA.checkpointPath, JSON.stringify(corrupted));
    await assert.rejects(
      readForumGroupNavigationCheckpoint(
        planState.pageDirectory,
        evidenceA.evidence_key,
        rowA,
        checkpointOptions,
      ),
      /Existing immutable forum checkpoint failed validation/,
    );
  });
});

test("same-title forum groups resolve only by distinct exact membership keys", () => {
  const parentForumChannelId = "1283941772577472643";
  const query = "in:premium-journals after:2026-01-01 before:2026-01-03";
  const groupA = ["1527000000000000000", "1527000000000000001"];
  const groupB = ["1527000000000000002", "1527000000000000003"];
  const keyA = forumGroupEvidenceKey(query, 1, groupA);
  const keyB = forumGroupEvidenceKey(query, 1, groupB);
  assert.notEqual(keyA, keyB);
  const evidenceA = buildForumGroupHeaderNavigationEvidence({
    query,
    pageNumber: 1,
    messageIds: groupA,
    parentForumChannelId,
    ...exactForumReturnFields(query, 1, groupA),
    destinationUrl: "https://discord.com/channels/1167376964680691732/1508933293322801183",
    observedAtUtc: "2026-07-21T16:30:00.000Z",
  });
  const evidenceB = buildForumGroupHeaderNavigationEvidence({
    query,
    pageNumber: 1,
    messageIds: groupB,
    parentForumChannelId,
    ...exactForumReturnFields(query, 1, groupB),
    destinationUrl: "https://discord.com/channels/1167376964680691732/1509487946565292152",
    observedAtUtc: "2026-07-21T16:31:00.000Z",
  });
  const evidenceMap = { [keyA]: evidenceA, [keyB]: evidenceB };
  const resolve = (messageIds, messageId) =>
    deriveDiscordThreadFields(
      attachForumGroupHeaderNavigationEvidence(
        {
          message_id: messageId,
          page_number: 1,
          search_query: query,
          thread_title: "duplicate title",
          forum_group_message_ids: messageIds,
          forum_group_membership_exact: true,
        },
        evidenceMap,
        { parentForumChannelId },
      ),
    );
  assert.equal(resolve(groupA, groupA[0]).inferred_thread_channel_id, "1508933293322801183");
  assert.equal(resolve(groupB, groupB[0]).inferred_thread_channel_id, "1509487946565292152");
});

test("forum navigation evidence rejects membership mismatches", () => {
  const parentForumChannelId = "1283941772577472643";
  const query = "in:premium-journals after:2026-01-01 before:2026-01-03";
  const rowMessageIds = ["1527000000000000000", "1527000000000000001"];
  const row = deriveDiscordForumGroupMembershipFields({
    message_id: rowMessageIds[0],
    page_number: 1,
    search_query: query,
    forum_group_message_ids: rowMessageIds,
    forum_group_membership_exact: true,
  });
  const mismatched = buildForumGroupHeaderNavigationEvidence({
    query,
    pageNumber: 1,
    messageIds: [rowMessageIds[0], "1527000000000000002"],
    parentForumChannelId,
    ...exactForumReturnFields(query, 1, [rowMessageIds[0], "1527000000000000002"]),
    destinationUrl: "https://discord.com/channels/1167376964680691732/1508933293322801183",
    observedAtUtc: "2026-07-21T16:30:00.000Z",
  });
  mismatched.evidence_key = row.forum_group_membership_key;
  const attached = attachForumGroupHeaderNavigationEvidence(
    row,
    { [row.forum_group_membership_key]: mismatched },
    { parentForumChannelId },
  );
  assert.equal(attached.forum_group_navigation_validation.valid, false);
  assert.ok(
    attached.forum_group_navigation_validation.errors.includes(
      "forum_navigation_group_membership_mismatch",
    ),
  );
  assert.equal(deriveDiscordThreadFields(attached).thread_channel_id_exact, false);
});

test("forum navigation evidence rejects wrong guilds and non-Discord destinations", () => {
  const parentForumChannelId = "1283941772577472643";
  const query = "in:premium-journals after:2026-01-01 before:2026-01-03";
  const messageIds = ["1527000000000000000"];
  const row = deriveDiscordForumGroupMembershipFields({
    message_id: messageIds[0],
    page_number: 1,
    search_query: query,
    forum_group_message_ids: messageIds,
    forum_group_membership_exact: true,
  });
  const wrongGuild = buildForumGroupHeaderNavigationEvidence({
    query,
    pageNumber: 1,
    messageIds,
    parentForumChannelId,
    ...exactForumReturnFields(query, 1, messageIds),
    destinationUrl: "https://discord.com/channels/999999999999999999/1508933293322801183",
    observedAtUtc: "2026-07-21T16:30:00.000Z",
  });
  const wrongGuildRow = attachForumGroupHeaderNavigationEvidence(
    row,
    { [row.forum_group_membership_key]: wrongGuild },
    { parentForumChannelId },
  );
  assert.equal(wrongGuildRow.forum_group_navigation_validation.valid, false);
  assert.ok(
    wrongGuildRow.forum_group_navigation_validation.errors.includes(
      "forum_navigation_destination_guild_mismatch",
    ),
  );

  const wrongUrl = buildForumGroupHeaderNavigationEvidence({
    query,
    pageNumber: 1,
    messageIds,
    parentForumChannelId,
    ...exactForumReturnFields(query, 1, messageIds),
    destinationUrl: "https://example.com/channels/1167376964680691732/1508933293322801183",
    observedAtUtc: "2026-07-21T16:30:00.000Z",
  });
  wrongUrl.evidence_key = row.forum_group_membership_key;
  const wrongUrlRow = attachForumGroupHeaderNavigationEvidence(
    row,
    { [row.forum_group_membership_key]: wrongUrl },
    { parentForumChannelId },
  );
  assert.equal(wrongUrlRow.forum_group_navigation_validation.valid, false);
  assert.ok(
    wrongUrlRow.forum_group_navigation_validation.errors.includes(
      "forum_navigation_destination_url_invalid",
    ),
  );
});

test("legacy forum attachment inference cannot bootstrap exact ownership", () => {
  const parentForumChannelId = "1283941772577472643";
  const inferredThreadId = "1455656711926055012";
  const withLegacyInference = deriveDiscordThreadFields({
    message_id: "1456857553072951337",
    inferred_thread_channel_id: inferredThreadId,
    attachments: [
      {
        thread_channel_id: inferredThreadId,
        attachment_id: "1456857552804642857",
        dom_relation: "exact_message_accessories_descendant",
        href_in_message_content: false,
      },
    ],
  });
  assert.equal(withLegacyInference.thread_channel_id_source, "legacy_inferred_container_id");
  assert.equal(withLegacyInference.thread_channel_id_exact, false);
  const enriched = enrichCollectedRow(withLegacyInference, {
    channelId: parentForumChannelId,
    channelName: "premium-journals",
    channelKind: "forum channel",
  });
  assert.equal(enriched.attachments[0].ownership_status, "unresolved");
  assert.equal(enriched.attachments[0].ownership_evidence.exact, false);
});

test("reply permalinks are exact thread fallbacks but unclassified attachment paths are not", () => {
  const fromReply = deriveDiscordThreadFields({
    message_id: "1527000000000000000",
    reply_to_permalink:
      "https://discord.com/channels/1167376964680691732/1509487946565292152/1526999999999999999",
    attachments: [{ thread_channel_id: "1509487946565292152" }],
  });
  assert.equal(fromReply.inferred_thread_channel_id, "1509487946565292152");
  assert.equal(fromReply.thread_channel_id_source, "owned_reply_permalink");
  assert.equal(fromReply.thread_channel_id_exact, true);

  const fromAttachment = deriveDiscordThreadFields({
    message_id: "1527000000000000000",
    attachments: [{ thread_channel_id: "1448694359355691150" }],
  });
  assert.equal(fromAttachment.inferred_thread_channel_id, null);
  assert.equal(fromAttachment.thread_channel_id_source, null);
  assert.equal(fromAttachment.thread_channel_id_exact, false);
});

test("attachment ownership is fail-closed and copied CDN media remains auditable", () => {
  const owned = deriveDiscordAttachmentFields(
    {
      message_id: "1527000000000000000",
      attachments: [
        {
          attachment_id: "1526999999999999999",
          thread_channel_id: "1329615478716502097",
          dom_relation: "exact_message_accessories_descendant",
          href_in_message_content: false,
        },
      ],
    },
    "1329615478716502097",
  ).attachments[0];
  assert.equal(owned.relation_type, "owned");
  assert.equal(owned.ownership_status, "owned_exact");
  assert.equal(owned.ownership_evidence.exact, true);

  const copied = deriveDiscordAttachmentFields(
    {
      message_id: "1527000000000000000",
      attachments: [
        {
          attachment_id: "1450000000000000000",
          thread_channel_id: "1254669393955258399",
          dom_relation: "exact_message_accessories_descendant",
        },
      ],
    },
    "1329615478716502097",
  ).attachments[0];
  assert.equal(copied.relation_type, "embedded_external");
  assert.equal(copied.ownership_status, "non_owned_exact");
  assert.match(copied.ownership_evidence.basis, /source_channel_differs/);

  const unresolved = deriveDiscordAttachmentFields(
    {
      message_id: "1527000000000000000",
      attachments: [
        {
          attachment_id: "1526999999999999999",
          thread_channel_id: "1329615478716502097",
          dom_relation: "article_link_unresolved",
        },
      ],
    },
    "1329615478716502097",
  ).attachments[0];
  assert.equal(unresolved.relation_type, "unresolved");
  assert.equal(unresolved.ownership_evidence.exact, false);

  const missingContentExclusion = deriveDiscordAttachmentFields(
    {
      message_id: "1527000000000000000",
      attachments: [
        {
          attachment_id: "1526999999999999999",
          thread_channel_id: "1329615478716502097",
          dom_relation: "exact_message_accessories_descendant",
        },
      ],
    },
    "1329615478716502097",
  ).attachments[0];
  assert.equal(missingContentExclusion.relation_type, "unresolved");
  assert.equal(missingContentExclusion.ownership_status, "unresolved");
});

test("stable-bottom proof requires an explicit disabled-next observation", () => {
  const query = `in:${CHANNEL_NAME} after:2026-01-15 before:2026-01-17`;
  const observations = [1, 2].map((sequence) => ({
    sequence,
    observed_at_utc: `2026-07-21T00:00:0${sequence}.000Z`,
    query,
    visible_result_count: 5,
    first_result_index: 26,
    last_result_index: 30,
    current_page: 2,
    result_set_size: 30,
  }));
  const validation = validateCompletionEvidence(
    {
      schema_version: "1.0.0",
      query,
      reported_total: 30,
      reported_pages: 2,
      terminal_state: "stable_bottom",
      search_submission: {
        mode: "fresh",
        query,
        submission_count: 1,
        submitted_at_utc: "2026-07-21T00:00:00.000Z",
      },
      stable_empty: null,
      stable_bottom: { required_observations: 2, observations },
    },
    query,
    30,
    2,
  );
  assert.equal(validation.valid, false);
  assert.ok(validation.errors.includes("stable_bottom_next_disabled_not_proven"));
});

test("forum thread provenance records conflicting sources without overriding the card identifier", () => {
  const enriched = deriveDiscordThreadFields({
    message_id: "1527000000000000000",
    group_header_data_list_item_id:
      "forum-channel-list-1283941772577472643___1508933293322801183",
    reply_to_permalink:
      "https://discord.com/channels/1167376964680691732/1405897225845997588/1526999999999999999",
  });
  assert.equal(enriched.inferred_thread_channel_id, "1508933293322801183");
  assert.equal(enriched.thread_channel_id_source, "forum_group_header_data_list_item_id");
  assert.equal(enriched.thread_channel_id_conflict, true);
  assert.deepEqual(
    enriched.thread_channel_id_candidates.map((candidate) => candidate.channel_id),
    ["1508933293322801183", "1405897225845997588"],
  );
});

test("row-owned reply content IDs resolve exact Discord reply targets", () => {
  const enriched = deriveDiscordReplyFields(
    {
      message_id: "1471158600193016042",
      reply_context_present: true,
      reply_target_content_id: "message-content-1471158475533844511",
      reply_target_content_text: "Does this invalidate the RB?",
    },
    "1329615478716502097",
  );
  assert.equal(enriched.reply_to_message_id, "1471158475533844511");
  assert.equal(enriched.reply_to_message_id_source, "owned_reply_context_descendant_content_id");
  assert.equal(enriched.reply_to_channel_id, "1329615478716502097");
  assert.equal(enriched.reply_target_scope_exact, true);
  assert.equal(
    enriched.reply_to_permalink,
    "https://discord.com/channels/1167376964680691732/1329615478716502097/1471158475533844511",
  );
});

test("reply IDs are not invented when the row-owned preview has no exact content ID", () => {
  const enriched = deriveDiscordReplyFields(
    {
      message_id: "1471158600193016042",
      reply_context_present: true,
      reply_target_content_id: null,
      reply_to_content: "Message could not be loaded",
    },
    "1329615478716502097",
  );
  assert.equal(enriched.reply_to_message_id, null);
  assert.equal(enriched.reply_to_permalink, null);
  assert.equal(enriched.reply_target_scope_exact, false);
});

test("reply previews without exact targets are documented but never linked", () => {
  const missing = deriveDiscordReplyFields({
    message_id: "1461035298992685282",
    reply_context_present: true,
    reply_context: "Message could not be loaded",
  }, CHANNEL_ID);
  assert.equal(missing.reply_to_message_id, null);
  assert.equal(missing.reply_target_resolution_status, "discord_message_not_loaded");
  assert.equal(missing.reply_target_unavailability_documented, true);

  const attachmentPreview = deriveDiscordReplyFields({
    message_id: "1461036422784876667",
    reply_context_present: true,
    reply_context: "@vale\nServer Tag: APSL\nClick to see attachment",
  }, CHANNEL_ID);
  assert.equal(attachmentPreview.reply_to_message_id, null);
  assert.equal(
    attachmentPreview.reply_target_resolution_status,
    "discord_attachment_preview_without_exact_target_id",
  );
  assert.equal(attachmentPreview.reply_target_unavailability_documented, true);

  const stickerPreview = deriveDiscordReplyFields({
    message_id: "1479265496992972861",
    reply_context_present: true,
    reply_context: "@! nq john\nServer Tag: stdv\nClick to see sticker",
  }, CHANNEL_ID);
  assert.equal(stickerPreview.reply_to_message_id, null);
  assert.equal(
    stickerPreview.reply_target_resolution_status,
    "discord_sticker_preview_without_exact_target_id",
  );
  assert.equal(stickerPreview.reply_target_unavailability_documented, true);

  for (const observedMessageId of ["1459199677718200543", "1459199648798609624"]) {
    const voicePreview = deriveDiscordReplyFields({
      message_id: observedMessageId,
      reply_context_present: true,
      reply_context: "Click to see voice message",
    }, CHANNEL_ID);
    assert.equal(voicePreview.reply_to_message_id, null);
    assert.equal(voicePreview.reply_to_channel_id, null);
    assert.equal(voicePreview.reply_to_permalink, null);
    assert.equal(
      voicePreview.reply_target_resolution_status,
      "discord_voice_message_preview_without_exact_target_id",
    );
    assert.equal(voicePreview.reply_target_unavailability_documented, true);
  }

  const nonExactVoiceText = deriveDiscordReplyFields({
    message_id: "1479265496992972863",
    reply_context_present: true,
    reply_context: "@target\nClick to see voice message\nwith transcript",
  }, CHANNEL_ID);
  assert.equal(nonExactVoiceText.reply_to_message_id, null);
  assert.equal(nonExactVoiceText.reply_target_resolution_status, "unresolved_without_exact_target_id");
  assert.equal(nonExactVoiceText.reply_target_unavailability_documented, false);

  const ambiguous = deriveDiscordReplyFields({
    message_id: "1461036422784876667",
    reply_context_present: true,
    reply_context: "Click to see attachment",
    reply_target_aria_labelledby: "ambiguous-reference",
  }, CHANNEL_ID);
  assert.equal(ambiguous.reply_target_resolution_status, "unresolved_without_exact_target_id");
  assert.equal(ambiguous.reply_target_unavailability_documented, false);
});

test("reply derivation is idempotent on already-enriched rows", () => {
  const once = deriveDiscordReplyFields({
    message_id: "1479312718061371456",
    reply_context_present: true,
    reply_context: "@target\nquoted text",
    reply_target_content_id: "message-content-1479312646036656191",
    reply_target_id_candidates: [{
      message_id: "1479312646036656191",
      channel_id: null,
      source: "owned_reply_descendant_message_id",
      raw_value: "message-content-1479312646036656191",
    }],
  }, CHANNEL_ID);
  const twice = deriveDiscordReplyFields(once, CHANNEL_ID);
  assert.deepEqual(twice, once);

  const permalinkOnce = deriveDiscordReplyFields({
    message_id: "1479312718061371456",
    reply_context_present: true,
    reply_context: "@target\nquoted text",
    reply_to_permalink:
      "https://discord.com/channels/1167376964680691732/1329615478716502097/1479312646036656191",
  }, CHANNEL_ID);
  const permalinkTwice = deriveDiscordReplyFields(permalinkOnce, CHANNEL_ID);
  assert.deepEqual(permalinkTwice, permalinkOnce);
});

test("exact Dyno command contexts are documented as non-replies without inventing IDs", () => {
  const base = {
    message_id: "1473346682816303196",
    author: "Dyno",
    author_id: "155149108183695360",
    content_scope_exact: true,
    content_text: "",
    reply_context_present: true,
    reply_context: "boy\n used \nmute",
    reply_target_content_id: null,
    reply_target_aria_labelledby: null,
    reply_target_data_list_item_id: null,
    reply_target_id_candidates: [],
    reply_to_permalink: null,
  };
  const command = deriveDiscordReplyFields(base, CHANNEL_ID);
  assert.equal(command.reply_to_message_id, null);
  assert.equal(
    command.reply_target_resolution_status,
    "discord_dyno_command_context_without_reply_target",
  );
  assert.equal(command.reply_target_unavailability_documented, true);
  assert.equal(command.reply_context_non_reply_exact, true);
  assert.equal(command.reply_context_non_reply_type, "discord_dyno_command_invocation");

  for (const changed of [
    { author_id: "155149108183695361" },
    { content_text: "mute" },
    { reply_context: "boy\nreplied\nmute" },
    { reply_target_aria_labelledby: "ambiguous-reference" },
  ]) {
    const rejected = deriveDiscordReplyFields({ ...base, ...changed }, CHANNEL_ID);
    assert.equal(rejected.reply_context_non_reply_exact, false);
    assert.equal(rejected.reply_target_resolution_status, "unresolved_without_exact_target_id");
  }
});

test("exact Discord application command contexts are documented without inventing reply IDs", () => {
  const base = {
    message_id: "1523613360099295304",
    author: "Wordle",
    author_id: "1211781489931452447",
    author_id_source: "owner_scoped_avatar_cdn_path",
    author_id_conflict: false,
    article_id: "search-result-1523613360099295304",
    article_aria_labelledby:
      "message-username-1523613360099295304 uid_3 message-content-1523613360099295304 " +
      "message-accessories-1523613360099295304 uid_4 message-timestamp-1523613360099295304",
    content_scope_exact: true,
    content_text: "LukeLarps was playing",
    reply_context_present: true,
    reply_context_scope_exact: false,
    reply_context: "LukeLarps\n used \nPlay",
    reply_to_content: "LukeLarps\n used \nPlay",
    reply_context_dom_class:
      "repliedMessage_c19a55 messageSpine_c19a55 executedCommand_c19a55",
    reply_context_dom_tag: "DIV",
    reply_context_aria_hidden: true,
    reply_context_article_binding_exact: true,
    reply_context_owner_message_id: "1523613360099295304",
    reply_context_executed_command_exact: true,
    author_verified_app_exact: true,
    reply_target_owner_scoped: false,
    reply_target_content_id: null,
    reply_target_content_text: "",
    reply_target_aria_labelledby: null,
    reply_target_data_list_item_id: null,
    reply_target_id_candidates: [],
    reply_to_permalink: null,
  };
  const command = deriveDiscordReplyFields(base, CHANNEL_ID);
  assert.equal(command.reply_to_message_id, null);
  assert.equal(command.reply_to_channel_id, null);
  assert.equal(command.reply_to_permalink, null);
  assert.equal(
    command.reply_target_resolution_status,
    "discord_executed_command_context_without_reply_target",
  );
  assert.equal(command.reply_target_unavailability_documented, true);
  assert.equal(command.reply_context_non_reply_exact, true);
  assert.equal(command.reply_context_non_reply_type, "discord_application_command_invocation");

  const second = deriveDiscordReplyFields({
    ...base,
    message_id: "1523977453436010537",
    article_id: "search-result-1523977453436010537",
    article_aria_labelledby:
      "message-username-1523977453436010537 uid_3 message-content-1523977453436010537 " +
      "message-accessories-1523977453436010537 uid_4 message-timestamp-1523977453436010537",
    reply_context: "TenshiKira\n used \nPlay",
    reply_to_content: "TenshiKira\n used \nPlay",
    reply_context_owner_message_id: "1523977453436010537",
  }, CHANNEL_ID);
  assert.equal(
    second.reply_target_resolution_status,
    "discord_executed_command_context_without_reply_target",
  );

  for (const changed of [
    { reply_context_executed_command_exact: false },
    { reply_context_aria_hidden: false },
    { author_verified_app_exact: false },
    { reply_context_dom_class: "repliedMessage_c19a55 messageSpine_c19a55" },
    { reply_context_dom_class: "executedCommand_lookalike" },
    { reply_context_article_binding_exact: false },
    { reply_context_owner_message_id: "1523613360099295305" },
    { reply_context_dom_tag: "SPAN" },
    { article_id: "search-result-1523613360099295305" },
    { author: "Wordle lookalike" },
    { author_id: "1211781489931452448" },
    { reply_context: "LukeLarps\nreplied\nPlay" },
    { reply_context: "LukeLarps\nused\nOther", reply_to_content: "LukeLarps\nused\nOther" },
    { reply_target_aria_labelledby: "ambiguous-reference" },
    { reply_target_aria_describedby: "ambiguous-reference" },
    {
      reply_to_message_id_candidates: [],
      reply_target_id_candidates: [{}],
    },
  ]) {
    const rejected = deriveDiscordReplyFields({ ...base, ...changed }, CHANNEL_ID);
    assert.equal(rejected.reply_context_non_reply_exact, false);
    assert.equal(rejected.reply_target_resolution_status, "unresolved_without_exact_target_id");
  }
});

test("row-owned ARIA and data-list evidence resolves reply IDs while conflicts stay null", () => {
  const ariaResolved = deriveDiscordReplyFields(
    {
      message_id: "1471158600193016042",
      reply_context_present: true,
      reply_target_id_candidates: [
        {
          message_id: "1471158475533844511",
          channel_id: null,
          source: "owned_reply_descendant_aria_reference",
          raw_value: "message-username-1471158475533844511 message-content-1471158475533844511",
          owner_scoped: true,
        },
      ],
    },
    "1329615478716502097",
  );
  assert.equal(ariaResolved.reply_to_message_id, "1471158475533844511");
  assert.equal(ariaResolved.reply_to_message_id_source, "owned_reply_descendant_aria_reference");
  assert.equal(ariaResolved.reply_target_scope_exact, true);

  const conflicted = deriveDiscordReplyFields(
    {
      message_id: "1471158600193016042",
      reply_context_present: true,
      reply_target_id_candidates: [
        {
          message_id: "1471158475533844511",
          source: "owned_reply_descendant_aria_reference",
          owner_scoped: true,
        },
        {
          message_id: "1471158000000000000",
          source: "owned_reply_descendant_data_list_item_id",
          owner_scoped: true,
        },
      ],
    },
    "1329615478716502097",
  );
  assert.equal(conflicted.reply_to_message_id, null);
  assert.equal(conflicted.reply_to_permalink, null);
  assert.equal(conflicted.reply_to_message_id_conflict, true);
});

test("direct ARIA and data-list reply evidence requires exact row-owner scope", () => {
  const ownerScopedAria = deriveDiscordReplyFields(
    {
      message_id: "1471158600193016042",
      reply_context_present: true,
      reply_target_owner_scoped: true,
      reply_target_aria_labelledby:
        "message-username-1471158475533844511 message-content-1471158475533844511",
    },
    "1329615478716502097",
  );
  assert.equal(ownerScopedAria.reply_to_message_id, "1471158475533844511");
  assert.equal(ownerScopedAria.reply_to_message_id_source, "owned_reply_descendant_aria_reference");
  assert.equal(ownerScopedAria.reply_target_scope_exact, true);

  const ownerScopedDataList = deriveDiscordReplyFields(
    {
      message_id: "1471158600193016042",
      reply_context_present: true,
      reply_context_scope_exact: true,
      reply_target_data_list_item_id: "chat-messages___1471158475533844511",
    },
    "1329615478716502097",
  );
  assert.equal(ownerScopedDataList.reply_to_message_id, "1471158475533844511");
  assert.equal(ownerScopedDataList.reply_to_message_id_source, "owned_reply_descendant_data_list_item_id");

  const unownedAria = deriveDiscordReplyFields(
    {
      message_id: "1471158600193016042",
      reply_context_present: true,
      reply_target_aria_labelledby: "message-content-1471158475533844511",
    },
    "1329615478716502097",
  );
  assert.equal(unownedAria.reply_to_message_id, null);
  assert.equal(unownedAria.reply_to_channel_id, null);
  assert.equal(unownedAria.reply_to_permalink, null);
  assert.equal(unownedAria.reply_target_resolution_status, "unresolved_without_exact_target_id");

  const mismatchedOwnerCandidate = deriveDiscordReplyFields(
    {
      message_id: "1471158600193016042",
      reply_context_present: true,
      reply_target_id_candidates: [
        {
          message_id: "1471158475533844511",
          source: "owned_reply_descendant_aria_reference",
          owner_scoped: false,
        },
      ],
    },
    "1329615478716502097",
  );
  assert.equal(mismatchedOwnerCandidate.reply_to_message_id, null);
  assert.equal(mismatchedOwnerCandidate.reply_to_channel_id, null);
  assert.equal(mismatchedOwnerCandidate.reply_to_permalink, null);
  assert.equal(mismatchedOwnerCandidate.reply_target_scope_exact, false);

  const unknownCandidate = deriveDiscordReplyFields(
    {
      message_id: "1471158600193016042",
      reply_context_present: true,
      reply_target_id_candidates: [
        {
          message_id: "1471158475533844511",
          source: "nearby_search_result_attribute",
          raw_value: "message-content-1471158475533844511",
        },
      ],
    },
    "1329615478716502097",
  );
  assert.equal(unknownCandidate.reply_to_message_id, null);
  assert.equal(unknownCandidate.reply_to_channel_id, null);
  assert.equal(unknownCandidate.reply_to_permalink, null);
  assert.equal(unknownCandidate.reply_target_scope_exact, false);
  assert.equal(unknownCandidate.reply_target_resolution_status, "unresolved_without_exact_target_id");
});

test("page validation rejects missing indices and cross-page message duplication", () => {
  const shortPage = Array.from({ length: 24 }, (_, offset) => ({
    result_index: 76 + offset,
    message_id: String(30_000 + offset),
  }));
  const missing = validateExtractedPage(shortPage, 4, 648, []);
  assert.equal(missing.valid, false);
  assert.deepEqual(missing.missing_indices, [100]);

  const existing = checkpointRows(200);
  const nextPage = Array.from({ length: 25 }, (_, offset) => ({
    result_index: 201 + offset,
    message_id: offset === 0 ? existing.at(-1).message_id : String(40_000 + offset),
  }));
  const overlap = validateExtractedPage(nextPage, 9, 648, existing);
  assert.equal(overlap.valid, false);
  assert.deepEqual(overlap.overlap_message_ids, [existing.at(-1).message_id]);
});

test("countSearch requires three stable empty observations and submits only once", async () => {
  const tab = fakeSearchTab(["No Results", "No Results", "No Results"]);
  const result = await countSearch(tab, "in:questions after:2025-12-31 before:2026-01-02");
  assert.equal(result.empty, true);
  assert.equal(result.empty_observations, 3);
  assert.equal(tab.stats.statusObservations, 3);
  assert.equal(tab.stats.presses, 1);
});

test("countSearch never converts Searching or Discord search errors into verified empty", async () => {
  const pendingTab = fakeSearchTab(Array(8).fill("Searching…\nFilters (3)"));
  await assert.rejects(
    countSearch(pendingTab, "in:questions after:2025-12-31 before:2026-01-02"),
    (error) => error instanceof DiscordSearchStateError && error.code === "search_state_unresolved",
  );
  assert.equal(pendingTab.stats.presses, 1);

  const errorTab = fakeSearchTab([
    "No Results\nWe dropped the magnifying glass. Can you try searching again?",
  ]);
  await assert.rejects(
    countSearch(errorTab, "in:questions after:2025-12-31 before:2026-01-02"),
    (error) => error instanceof DiscordSearchStateError && error.code === "search_error",
  );
  assert.equal(errorTab.stats.presses, 1);
});

test("pagination advances conservatively through adjacent visible pages", () => {
  assert.equal(choosePaginationStep(1, 5, [1, 2, 3, 10]), 2);
  assert.equal(choosePaginationStep(2, 5, [1, 2, 3, 10]), 3);
  assert.equal(choosePaginationStep(3, 5, [1, 3, 4, 10]), 4);
  assert.equal(choosePaginationStep(4, 5, [1, 4, 5, 10]), 5);
});

test("pagination uses Next instead of jumping to the last page when the adjacent number is hidden", () => {
  assert.deepEqual(
    choosePaginationControl(3, 4, [1, 2, 3, 12], true, true),
    { nextPage: 4, accessibleName: "Next", kind: "adjacent" },
  );
  assert.deepEqual(
    choosePaginationControl(9, 8, [1, 9, 10, 12], true, true),
    { nextPage: 8, accessibleName: "Back", kind: "adjacent" },
  );
});

test("scheduled segments declare the Central-time corpus boundary explicitly", () => {
  const [segment] = makeSegments(
    "2026-01-01",
    "2026-01-01",
    1,
    `in:${CHANNEL_NAME}`,
  );
  assert.equal(segment.timezone, "America/Chicago");
  assert.deepEqual(Object.keys(segment), ["start", "end", "query", "timezone"]);
});

test("bounded resume navigation reuses the active query and preserves raw checkpoint bytes", async () => {
  await withTempDirectory(async (root) => {
    const segment = makeSegments("2026-01-14", "2026-01-14", 1, `in:${CHANNEL_NAME}`)[0];
    const { partialPath } = artifactPaths(root, segment);
    const rows = checkpointRows(100);
    const checkpoint = JSON.stringify({
      collector_version: "2.6",
      guild_id: "1167376964680691732",
      collection_scope: "channel-scoped",
      requested_container: {
        channel_id: CHANNEL_ID,
        channel_name: CHANNEL_NAME,
        channel_kind: "text channel",
        category_name: "PREMIUM",
        channel_id_source: "navigation_inventory",
      },
      segment,
      reported_total: 150,
      reported_pages: 6,
      pages_captured: 4,
      captured_rows: 100,
      unique_message_ids: 100,
      gap_indices: [],
      container_mismatch_count: 0,
      complete: false,
      messages: rows,
    });
    await fs.writeFile(partialPath, checkpoint);
    const tab = fakeResumeNavigationTab({ query: segment.query, total: 150, currentPage: 1 });
    await assert.rejects(
      collectSegment(tab, segment, root, {
        ...collectorOptions(),
        reuseActiveSearch: true,
        resumeNavigationMaxSteps: 2,
      }),
      (error) =>
        error instanceof DiscordResumeNavigationPending &&
        error.code === "resume_navigation_pending" &&
        error.currentPage === 3 &&
        error.targetPage === 5,
    );
    assert.equal(tab.stats.currentPage, 3);
    assert.equal(tab.stats.fills, 0);
    assert.equal(tab.stats.presses, 0);
    assert.equal(await fs.readFile(partialPath, "utf8"), checkpoint);
  });
});

test("active-search resume refuses changed checkpoint query, total, or index continuity", async () => {
  const cases = [
    {
      name: "query",
      mutate(payload) {
        payload.segment = { ...payload.segment, query: `${payload.segment.query} rb` };
      },
    },
    {
      name: "total",
      mutate(payload) {
        payload.reported_total = 151;
        payload.reported_pages = 7;
      },
    },
    {
      name: "continuity",
      mutate(payload) {
        payload.messages.at(-1).result_index = 99;
      },
    },
  ];

  for (const scenario of cases) {
    await withTempDirectory(async (root) => {
      const segment = makeSegments("2026-01-14", "2026-01-14", 1, `in:${CHANNEL_NAME}`)[0];
      const { partialPath } = artifactPaths(root, segment);
      const rows = checkpointRows(100);
      const payload = {
        collector_version: "2.6",
        guild_id: "1167376964680691732",
        collection_scope: "channel-scoped",
        requested_container: {
          channel_id: CHANNEL_ID,
          channel_name: CHANNEL_NAME,
          channel_kind: "text channel",
          category_name: "PREMIUM",
          channel_id_source: "navigation_inventory",
        },
        segment,
        reported_total: 150,
        reported_pages: 6,
        pages_captured: 4,
        captured_rows: 100,
        unique_message_ids: 100,
        gap_indices: [],
        container_mismatch_count: 0,
        complete: false,
        messages: rows,
      };
      scenario.mutate(payload);
      const checkpoint = JSON.stringify(payload);
      await fs.writeFile(partialPath, checkpoint);
      const tab = fakeResumeNavigationTab({ query: segment.query, total: 150, currentPage: 4 });

      await assert.rejects(
        collectSegment(tab, segment, root, {
          ...collectorOptions(),
          checkpointEvery: 5,
          pageDelayMs: 1200,
          reuseActiveSearch: true,
        }),
        /Refusing to overwrite incompatible partial artifact/,
        scenario.name,
      );
      assert.equal(tab.stats.fills, 0, scenario.name);
      assert.equal(tab.stats.presses, 0, scenario.name);
      assert.equal(await fs.readFile(partialPath, "utf8"), checkpoint, scenario.name);
    });
  }
});

test("resume rejects pre-2.6 partials without modifying their bytes", async () => {
  await withTempDirectory(async (root) => {
    const segment = makeSegments("2026-01-14", "2026-01-14", 1, `in:${CHANNEL_NAME}`)[0];
    const { partialPath } = artifactPaths(root, segment);
    const rows = checkpointRows(100);
    const checkpoint = JSON.stringify({
      collector_version: "2.4",
      guild_id: "1167376964680691732",
      collection_scope: "channel-scoped",
      requested_container: {
        channel_id: CHANNEL_ID,
        channel_name: CHANNEL_NAME,
        channel_kind: "text channel",
        category_name: "PREMIUM",
        channel_id_source: "navigation_inventory",
      },
      segment,
      reported_total: 150,
      reported_pages: 6,
      pages_captured: 4,
      captured_rows: 100,
      unique_message_ids: 100,
      gap_indices: [],
      container_mismatch_count: 0,
      complete: false,
      messages: rows,
    });
    await fs.writeFile(partialPath, checkpoint);
    const tab = fakeResumeNavigationTab({ query: segment.query, total: 150, currentPage: 4 });
    await assert.rejects(
      collectSegment(tab, segment, root, {
        ...collectorOptions(),
        reuseActiveSearch: true,
      }),
      /Refusing to overwrite incompatible partial artifact/,
    );
    assert.equal(await fs.readFile(partialPath, "utf8"), checkpoint);
  });
});

test("bounded extraction checkpoints newly captured pages before yielding", async () => {
  await withTempDirectory(async (root) => {
    const segment = makeSegments("2026-01-15", "2026-01-15", 1, `in:${CHANNEL_NAME}`)[0];
    const { partialPath } = artifactPaths(root, segment);
    const rows = checkpointRows(100);
    await fs.writeFile(
      partialPath,
      JSON.stringify({
        collector_version: "2.6",
        guild_id: "1167376964680691732",
        collection_scope: "channel-scoped",
        requested_container: {
          channel_id: CHANNEL_ID,
          channel_name: CHANNEL_NAME,
          channel_kind: "text channel",
          category_name: "PREMIUM",
          channel_id_source: "navigation_inventory",
        },
        segment,
        reported_total: 150,
        reported_pages: 6,
        pages_captured: 4,
        captured_rows: 100,
        unique_message_ids: 100,
        gap_indices: [],
        container_mismatch_count: 0,
        complete: false,
        messages: rows,
      }),
    );
    const tab = fakeResumeNavigationTab({ query: segment.query, total: 150, currentPage: 5 });
    await assert.rejects(
      collectSegment(tab, segment, root, {
        ...collectorOptions(),
        reuseActiveSearch: true,
        resumeNavigationMaxSteps: 2,
        maxPagesPerCall: 1,
      }),
      (error) =>
        error instanceof DiscordExtractionBatchPending &&
        error.code === "extraction_batch_pending" &&
        error.pageNumber === 5 &&
        error.totalPages === 6,
    );
    const updated = JSON.parse(await fs.readFile(partialPath, "utf8"));
    assert.equal(updated.pages_captured, 5);
    assert.equal(updated.captured_rows, 125);
    assert.equal(updated.unique_message_ids, 125);
    assert.equal(updated.complete, false);
    assert.equal(updated.container_mismatch_count, 0);
  });
});

test("positive completion requires and persists two matching stable-bottom observations", async () => {
  await withTempDirectory(async (root) => {
    const segment = makeSegments("2026-01-16", "2026-01-16", 1, `in:${CHANNEL_NAME}`)[0];
    const { partialPath, finalPath } = artifactPaths(root, segment);
    const rows = checkpointRows(30);
    await fs.writeFile(
      partialPath,
      JSON.stringify({
        collector_version: "2.6",
        guild_id: "1167376964680691732",
        collection_scope: "channel-scoped",
        requested_container: {
          channel_id: CHANNEL_ID,
          channel_name: CHANNEL_NAME,
          channel_kind: "text channel",
          category_name: "PREMIUM",
          channel_id_source: "navigation_inventory",
        },
        segment,
        reported_total: 30,
        reported_pages: 2,
        pages_captured: 2,
        captured_rows: 30,
        unique_message_ids: 30,
        gap_indices: [],
        container_mismatch_count: 0,
        complete: false,
        messages: rows,
      }),
    );
    const tab = fakeResumeNavigationTab({ query: segment.query, total: 30, currentPage: 2 });
    const result = await collectSegment(tab, segment, root, {
      ...collectorOptions(),
      reuseActiveSearch: true,
      stableBottomObservationDelayMs: 0,
    });
    assert.equal(result.reported, 30);
    const payload = JSON.parse(await fs.readFile(finalPath, "utf8"));
    assert.equal(payload.complete, true);
    assert.equal(payload.completion_evidence.terminal_state, "stable_bottom");
    assert.equal(payload.completion_evidence.stable_bottom.observations.length, 2);
    assert.equal(
      validateCompletionEvidence(payload.completion_evidence, segment.query, 30, 2).valid,
      true,
    );
    await assert.rejects(fs.stat(partialPath), { code: "ENOENT" });
  });
});

test("collectSegment skips a compatible complete artifact without touching the browser", async () => {
  await withTempDirectory(async (root) => {
    const segment = makeSegments("2026-01-01", "2026-01-01", 1, `in:${CHANNEL_NAME}`)[0];
    const { finalPath } = artifactPaths(root, segment);
    await fs.writeFile(finalPath, JSON.stringify(completePayload(segment)));
    const browserTrap = {
      playwright: new Proxy(
        {},
        {
          get() {
            throw new Error("browser should not be read for a completed segment");
          },
        },
      ),
    };
    const result = await collectSegment(browserTrap, segment, root, collectorOptions());
    assert.equal(result.skipped_existing_complete, true);
    assert.equal(result.reported, 0);

    const missingTimezone = completePayload(segment);
    delete missingTimezone.segment.timezone;
    await fs.writeFile(finalPath, JSON.stringify(missingTimezone));
    await assert.rejects(
      collectSegment(browserTrap, segment, root, collectorOptions()),
      /Refusing to overwrite incompatible complete artifact/,
    );
  });
});

test("sidecar revalidation preserves source bytes and enables browser-free skip", async () => {
  await withTempDirectory(async (root) => {
    const segment = makeSegments("2026-01-02", "2026-01-02", 1, `in:${CHANNEL_NAME}`)[0];
    const { finalPath } = artifactPaths(root, segment);
    const payload = completePayload(segment);
    payload.collector_version = "2.4";
    delete payload.completion_evidence;
    const sourceBytes = JSON.stringify(payload);
    await fs.writeFile(finalPath, sourceBytes);

    const tab = fakeSearchTab(["No Results", "No Results", "No Results"]);
    const { sidecarPath, sidecar } = await verifySegmentCompletionEvidence(tab, finalPath);
    assert.equal(await fs.readFile(finalPath, "utf8"), sourceBytes);
    assert.equal(sidecar.source_artifact_path, path.basename(finalPath));
    assert.deepEqual(sidecar.requested_container, payload.requested_container);

    const browserTrap = {
      playwright: new Proxy(
        {},
        {
          get() {
            throw new Error("browser should not be read for a sidecar-completed segment");
          },
        },
      ),
    };
    const skipped = await collectSegment(browserTrap, segment, root, collectorOptions());
    assert.equal(skipped.skipped_existing_complete, true);

    const invalidSidecar = JSON.parse(await fs.readFile(sidecarPath, "utf8"));
    invalidSidecar.requested_container = { ...payload.requested_container, channel_id: "999999999999999999" };
    await fs.writeFile(sidecarPath, JSON.stringify(invalidSidecar));
    await assert.rejects(
      collectSegment(browserTrap, segment, root, collectorOptions()),
      /sidecar binding is invalid/,
    );
  });
});

test("sidecar revalidation detects source mutation and writes no sidecar", async () => {
  await withTempDirectory(async (root) => {
    const segment = makeSegments("2026-01-03", "2026-01-03", 1, `in:${CHANNEL_NAME}`)[0];
    const { finalPath } = artifactPaths(root, segment);
    const payload = completePayload(segment);
    payload.collector_version = "2.4";
    delete payload.completion_evidence;
    await fs.writeFile(finalPath, JSON.stringify(payload));
    const sidecarPath = finalPath.replace(/\.json$/i, ".completion-evidence.json");
    const tab = fakeSearchTab(["No Results", "No Results", "No Results"]);
    let changed = false;
    tab.playwright.waitForTimeout = async () => {
      if (!changed) {
        changed = true;
        await fs.writeFile(finalPath, JSON.stringify({ ...payload, captured_at_utc: "2026-07-21T12:00:00Z" }));
      }
    };
    await assert.rejects(
      verifySegmentCompletionEvidence(tab, finalPath),
      /Source artifact changed during completion-evidence revalidation/,
    );
    await assert.rejects(fs.stat(sidecarPath), { code: "ENOENT" });
  });
});

test("sidecar revalidation never overwrites an existing null sidecar", async () => {
  await withTempDirectory(async (root) => {
    const segment = makeSegments("2026-01-04", "2026-01-04", 1, `in:${CHANNEL_NAME}`)[0];
    const { finalPath } = artifactPaths(root, segment);
    const payload = completePayload(segment);
    payload.collector_version = "2.4";
    delete payload.completion_evidence;
    await fs.writeFile(finalPath, JSON.stringify(payload));
    const sidecarPath = finalPath.replace(/\.json$/i, ".completion-evidence.json");
    await fs.writeFile(sidecarPath, "null");
    const tab = fakeSearchTab(["No Results", "No Results", "No Results"]);
    await assert.rejects(
      verifySegmentCompletionEvidence(tab, finalPath),
      /Refusing to overwrite completion-evidence sidecar/,
    );
    assert.equal(await fs.readFile(sidecarPath, "utf8"), "null");
  });
});

test("collectSegment writes a verified empty file only after stable empty observations", async () => {
  await withTempDirectory(async (root) => {
    const segment = makeSegments("2026-01-06", "2026-01-06", 1, `in:${CHANNEL_NAME}`)[0];
    const tab = fakeSearchTab(["No Results", "No Results", "No Results"]);
    const result = await collectSegment(tab, segment, root, {
      ...collectorOptions(),
      checkpointEvery: 5,
      pageDelayMs: 1200,
      reuseActiveSearch: true,
    });
    const paths = artifactPaths(root, segment);
    const payload = JSON.parse(await fs.readFile(paths.finalPath, "utf8"));
    assert.equal(result.reported, 0);
    assert.equal(tab.stats.presses, 1);
    assert.equal(tab.stats.statusObservations, 3);
    assert.equal(payload.complete, true);
    assert.equal(payload.reported_total, 0);
    assert.equal(payload.captured_rows, 0);
    assert.equal(payload.requested_container.channel_id, CHANNEL_ID);
    assert.equal(payload.collector_version, "2.6");
    assert.equal(payload.completion_evidence.terminal_state, "stable_empty");
    assert.equal(payload.completion_evidence.search_submission.mode, "fresh");
    assert.equal(payload.completion_evidence.search_submission.submission_count, 1);
    assert.equal(payload.completion_evidence.stable_empty.observations.length, 3);
    assert.deepEqual(
      payload.completion_evidence.stable_empty.observations.map((row) => row.state),
      ["empty_candidate", "empty_candidate", "empty_candidate"],
    );
    assert.equal(
      validateCompletionEvidence(payload.completion_evidence, segment.query, 0, 0).valid,
      true,
    );
    await assert.rejects(fs.stat(paths.partialPath), { code: "ENOENT" });
  });
});

test("resilient collection does not immediately retry a throttle-like search error", async () => {
  await withTempDirectory(async (root) => {
    const segment = makeSegments("2026-01-05", "2026-01-05", 1, `in:${CHANNEL_NAME}`)[0];
    const paths = artifactPaths(root, segment);
    const checkpoint = JSON.stringify({
      complete: false,
      segment,
      reported_total: 243,
      reported_pages: 10,
      pages_captured: 3,
      captured_rows: 75,
      unique_message_ids: 75,
      messages: [{ sentinel: "keep-checkpoint" }],
    });
    await fs.writeFile(paths.partialPath, checkpoint);
    const tab = fakeSearchTab([
      "No Results\nWe dropped the magnifying glass. Can you try searching again?",
    ]);
    await assert.rejects(
      collectSegmentResilient(tab, segment, root, {
        ...collectorOptions(),
        maxAttempts: 3,
        retryDelayMs: 0,
      }),
      (error) => error instanceof DiscordSearchStateError && error.code === "search_error",
    );
    assert.equal(tab.stats.presses, 1);
    await assert.rejects(fs.stat(paths.finalPath), { code: "ENOENT" });
    assert.equal(await fs.readFile(paths.partialPath, "utf8"), checkpoint);
  });
});

test("throttle pause preserves an existing partial checkpoint byte-for-byte", async () => {
  await withTempDirectory(async (root) => {
    const segments = makeSegments("2026-01-01", "2026-01-02", 1, `in:${CHANNEL_NAME}`);
    await fs.writeFile(
      artifactPaths(root, segments[0]).finalPath,
      JSON.stringify(completePayload(segments[0])),
    );
    const partialPath = artifactPaths(root, segments[1]).partialPath;
    const checkpoint = JSON.stringify({
      complete: false,
      segment: segments[1],
      reported_total: 243,
      reported_pages: 10,
      pages_captured: 3,
      captured_rows: 75,
      unique_message_ids: 75,
      gap_indices: [],
      messages: [{ sentinel: "preserve-me" }],
    });
    await fs.writeFile(partialPath, checkpoint);
    let calls = 0;
    const result = await collectSegmentsBatched(
      {},
      segments,
      root,
      collectorOptions(),
      {
        batchSize: 2,
        cooldownMs: 0,
        collectFn: async () => {
          calls += 1;
          throw new DiscordSearchStateError("Search produced no result row: Searching…");
        },
        sleepFn: async () => {},
      },
    );
    assert.equal(calls, 1);
    assert.equal(result.status, "paused_throttled");
    assert.equal(result.skipped_complete_segments, 1);
    assert.equal(result.failed_segments, 1);
    assert.equal(result.remaining_segments, 1);
    assert.equal(result.failures[0].partial_checkpoint.pages_captured, 3);
    assert.equal(await fs.readFile(partialPath, "utf8"), checkpoint);
  });
});

test("collectDateRange resumes pending dates, skips complete dates, and cools between batches", async () => {
  await withTempDirectory(async (root) => {
    const segments = makeSegments("2026-01-01", "2026-01-03", 1, `in:${CHANNEL_NAME}`);
    await fs.writeFile(
      artifactPaths(root, segments[0]).finalPath,
      JSON.stringify(completePayload(segments[0])),
    );
    await fs.writeFile(
      artifactPaths(root, segments[1]).partialPath,
      JSON.stringify({ complete: false, segment: segments[1], messages: [{ sentinel: true }] }),
    );
    const calls = [];
    const sleeps = [];
    const result = await collectDateRange({}, {
      startIso: "2026-01-01",
      endIso: "2026-01-03",
      outputDirectory: root,
      queryPrefix: `in:${CHANNEL_NAME}`,
      collectorOptions: collectorOptions(),
      schedulerOptions: {
        batchSize: 1,
        cooldownMs: 123,
        collectFn: async (_tab, segment) => {
          calls.push(segment.start);
          return { start: segment.start, end: segment.end, reported: 0, captured: 0, unique: 0, gaps: 0 };
        },
        sleepFn: async (milliseconds) => sleeps.push(milliseconds),
      },
    });
    assert.equal(result.status, "complete");
    assert.equal(result.skipped_complete_segments, 1);
    assert.equal(result.completed_segments, 2);
    assert.deepEqual(calls, ["2026-01-02", "2026-01-03"]);
    assert.deepEqual(sleeps, [123]);
  });
});

const FORUM_STABILITY_QUERY = "in:premium-journals after:2026-01-01 before:2026-01-03";

function forumStabilityRows(groups) {
  return groups.flatMap((group) =>
    group.items.map((item) => ({
      message_id: item.message_id,
      result_index: item.result_index,
      page_number: 1,
      search_query: FORUM_STABILITY_QUERY,
      forum_group_message_ids: group.items.map((entry) => entry.message_id),
      forum_group_membership_exact: true,
    })),
  );
}

function forumStabilityObservation(groups, mutate = () => {}) {
  const observedGroups = groups.map((group) => ({
    direct_header_button_count: 1,
    direct_listitem_count: group.items.length,
    items: group.items.map((item) => ({
      result_index: item.result_index,
      article_count: 1,
      article_id: `search-result-${item.message_id}`,
      article_data_list_item_id: `NO_LIST___${item.message_id}`,
      message_id: item.message_id,
      article_closest_group_is_owner: true,
    })),
  }));
  mutate(observedGroups);
  return { query: FORUM_STABILITY_QUERY, page_number: 1, groups: observedGroups };
}

test("forum pre-navigation validator rejects malformed direct membership structures", () => {
  const groups = [
    {
      items: [
        { message_id: "1527000000000000000", result_index: 1 },
        { message_id: "1527000000000000001", result_index: 2 },
      ],
    },
  ];
  const rows = forumStabilityRows(groups);
  const cases = [
    ["missing article", (observed) => { observed[0].items[0].article_count = 0; }],
    ["duplicate article", (observed) => { observed[0].items[0].article_count = 2; }],
    ["wrong article id", (observed) => { observed[0].items[0].article_id = "search-result-1527000000000000999"; }],
    ["extra direct listitem", (observed) => { observed[0].direct_listitem_count += 1; }],
    ["nonunique header", (observed) => { observed[0].direct_header_button_count = 2; }],
  ];
  for (const [label, mutate] of cases) {
    const validation = validateForumPreNavigationMembership(
      forumStabilityObservation(groups, mutate),
      rows,
      FORUM_STABILITY_QUERY,
      1,
      2,
    );
    assert.equal(validation.valid, false, label);
  }
});

test("forum extraction retries a transient malformed first sample before any navigation", async () => {
  const groups = [{ items: [{ message_id: "1527000000000000000", result_index: 1 }] }];
  const rows = forumStabilityRows(groups);
  let observationCalls = 0;
  let navigationClicks = 0;
  const tab = {
    playwright: {
      domSnapshot: async () => "",
      getByRole: () => {
        navigationClicks += 1;
        throw new Error("navigation must not begin before stable membership");
      },
    },
  };
  const result = await extractPageValidated(tab, 1, FORUM_STABILITY_QUERY, 1, [], {
    channelKind: "forum channel",
    captureForumGroupNavigationEvidence: false,
    pageValidationRetries: 1,
    pageValidationRetryDelayMs: 0,
    extractPageForTesting: async () => rows,
    observeForumPreNavigationForTesting: async () => {
      observationCalls += 1;
      return forumStabilityObservation(groups, (observed) => {
        if (observationCalls === 1) observed[0].items[0].article_count = 0;
      });
    },
  });
  assert.deepEqual(result.map((row) => row.message_id), ["1527000000000000000"]);
  assert.equal(observationCalls, 3);
  assert.equal(navigationClicks, 0);
});

test("forum extraction rejects differing stable-looking samples and retries before navigation", async () => {
  const combined = [
    {
      items: [
        { message_id: "1527000000000000000", result_index: 1 },
        { message_id: "1527000000000000001", result_index: 2 },
      ],
    },
  ];
  const split = [
    { items: [{ message_id: "1527000000000000000", result_index: 1 }] },
    { items: [{ message_id: "1527000000000000001", result_index: 2 }] },
  ];
  let sampleCalls = 0;
  let navigationClicks = 0;
  const tab = {
    playwright: {
      domSnapshot: async () => "",
      getByRole: () => {
        navigationClicks += 1;
        throw new Error("navigation must not begin before stable membership");
      },
    },
  };
  const result = await extractPageValidated(tab, 1, FORUM_STABILITY_QUERY, 2, [], {
    channelKind: "forum channel",
    captureForumGroupNavigationEvidence: false,
    pageValidationRetries: 1,
    pageValidationRetryDelayMs: 0,
    extractPageForTesting: async () => forumStabilityRows(sampleCalls === 1 ? split : combined),
    observeForumPreNavigationForTesting: async () => {
      const groups = sampleCalls === 1 ? split : combined;
      sampleCalls += 1;
      return forumStabilityObservation(groups);
    },
  });
  assert.equal(result.length, 2);
  assert.equal(sampleCalls, 4);
  assert.equal(navigationClicks, 0);
});
