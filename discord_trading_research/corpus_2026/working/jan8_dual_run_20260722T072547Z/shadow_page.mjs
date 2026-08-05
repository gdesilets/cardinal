import crypto from "node:crypto";
import fs from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";

import * as v26 from "../../../discord_browser_collector.mjs";
import * as v27 from "../../../discord_browser_collector_v2_7.mjs";

const GUILD_ID = "1167376964680691732";
const PARENT_FORUM_CHANNEL_ID = "1283941772577472643";
const ROUTE_DAY = "2026-01-08";

function parseArgs(argv) {
  const parsed = {};
  for (let index = 0; index < argv.length; index += 2) {
    const name = String(argv[index] || "");
    const value = argv[index + 1];
    if (!name.startsWith("--") || value === undefined) {
      throw new Error(`Invalid argument sequence near ${name || "<empty>"}`);
    }
    parsed[name.slice(2)] = value;
  }
  for (const required of ["artifact", "stage-nav", "corpus", "audit", "page", "query", "total"]) {
    if (!parsed[required]) throw new Error(`Missing --${required}`);
  }
  const pageNumber = Number(parsed.page);
  const reportedTotal = Number(parsed.total);
  if (!Number.isInteger(pageNumber) || pageNumber < 1) throw new Error("--page must be a positive integer");
  if (!Number.isInteger(reportedTotal) || reportedTotal < 1) throw new Error("--total must be a positive integer");
  return {
    artifactPath: path.resolve(parsed.artifact),
    stageNavigationRoot: path.resolve(parsed["stage-nav"]),
    artifactRoot: path.resolve(parsed.corpus),
    auditRoot: path.resolve(parsed.audit),
    query: parsed.query,
    pageNumber,
    reportedTotal,
  };
}

function sha256(bytes) {
  return crypto.createHash("sha256").update(bytes).digest("hex");
}

async function readJsonBytes(filePath) {
  const bytes = await fs.readFile(filePath);
  return { bytes, value: JSON.parse(bytes.toString("utf8")) };
}

async function writeJsonExclusiveAtomic(filePath, payload) {
  await fs.mkdir(path.dirname(filePath), { recursive: true });
  const bytes = Buffer.from(`${JSON.stringify(payload, null, 2)}\n`, "utf8");
  const temporary = `${filePath}.${process.pid}.${crypto.randomUUID()}.tmp`;
  try {
    await fs.writeFile(temporary, bytes, { flag: "wx" });
    await fs.link(temporary, filePath);
    await fs.unlink(temporary);
  } catch (error) {
    await fs.unlink(temporary).catch(() => {});
    throw error;
  }
  return { sha256: sha256(bytes), bytes: bytes.length };
}

function relativeTo(root, filePath) {
  const resolvedRoot = path.resolve(root);
  const resolvedPath = path.resolve(filePath);
  if (resolvedPath !== resolvedRoot && !resolvedPath.startsWith(`${resolvedRoot}${path.sep}`)) {
    throw new Error(`Path escaped expected root: ${resolvedPath}`);
  }
  return path.relative(resolvedRoot, resolvedPath).split(path.sep).join("/");
}

function groupRowsByPlan(pageRows, pagePlan) {
  const byKey = new Map();
  for (const row of pageRows) {
    const key = String(row?.forum_group_membership_key || "");
    if (!key) throw new Error(`Page row ${row?.message_id || "<unknown>"} lacks a forum group key`);
    if (!byKey.has(key)) byKey.set(key, []);
    byKey.get(key).push(row);
  }
  const expectedKeys = pagePlan.expected_group_evidence_keys || [];
  const observedKeys = [...byKey.keys()].sort();
  if (JSON.stringify(observedKeys) !== JSON.stringify([...expectedKeys].sort())) {
    throw new Error("v2.6 control group key set does not equal the immutable page plan");
  }
  return expectedKeys.map((key) => byKey.get(key));
}

async function main() {
  const options = parseArgs(process.argv.slice(2));
  const pageLabel = `page_${String(options.pageNumber).padStart(3, "0")}`;
  const comparisonPath = path.join(options.auditRoot, `${pageLabel}_shadow_control_comparison.json`);
  const quarantinePath = path.join(options.auditRoot, `${pageLabel}_shadow_quarantine.json`);
  const shadowDayRoot = path.join(
    options.artifactRoot,
    "raw",
    "premium_journals_v2_7_checkpoints",
    ROUTE_DAY,
  );
  const shadowPageRoot = path.join(shadowDayRoot, pageLabel);
  const controlPageRoot = path.join(options.stageNavigationRoot, pageLabel);
  const controlPlanPath = path.join(controlPageRoot, "page_plan.json");
  const forbiddenCanonicalPath = path.join(
    options.artifactRoot,
    "raw",
    "channel_segments_v2_7",
    `channel_premium_journals_${PARENT_FORUM_CHANNEL_ID}_${ROUTE_DAY}_${ROUTE_DAY}.json`,
  );

  if (await fs.stat(comparisonPath).then(() => true).catch(() => false)) {
    throw new Error(`Refusing to overwrite existing page comparison: ${comparisonPath}`);
  }
  if (await fs.stat(forbiddenCanonicalPath).then(() => true).catch(() => false)) {
    throw new Error(`Forbidden v2.7 canonical exists: ${forbiddenCanonicalPath}`);
  }

  let context = { page_number: options.pageNumber, phase: "precheck" };
  try {
    const artifactState = await readJsonBytes(options.artifactPath);
    const artifact = artifactState.value;
    if (artifact?.collector_version !== "2.6") throw new Error("Source artifact is not collector v2.6");
    if (artifact?.segment?.query !== options.query || artifact?.reported_total !== options.reportedTotal) {
      throw new Error("Source artifact query or reported total does not match the frozen Jan8 run");
    }
    const pageRows = (artifact.messages || []).filter(
      (row) => Number(row?.page_number) === options.pageNumber,
    );
    const expectedPageRows = Math.min(
      25,
      options.reportedTotal - (options.pageNumber - 1) * 25,
    );
    if (pageRows.length !== expectedPageRows) {
      throw new Error(`Page is not complete: expected ${expectedPageRows}, observed ${pageRows.length}`);
    }
    const uniqueMessageIds = new Set(pageRows.map((row) => String(row?.message_id || "")));
    if (uniqueMessageIds.size !== pageRows.length || uniqueMessageIds.has("")) {
      throw new Error("Page message IDs are missing or duplicated");
    }

    const controlPlanState = await readJsonBytes(controlPlanPath);
    const pagePlan = controlPlanState.value;
    const planValidation = v26.validateForumNavigationPagePlan(pagePlan, {
      query: options.query,
      pageNumber: options.pageNumber,
      reportedTotal: options.reportedTotal,
    });
    if (!planValidation.valid) {
      throw new Error(`v2.6 page plan invalid: ${planValidation.errors.join(",")}`);
    }
    const groups = groupRowsByPlan(pageRows, pagePlan);
    if (groups.length !== pagePlan.expected_group_count) {
      throw new Error("Control group count does not equal the page plan");
    }

    context = {
      ...context,
      phase: "control_validation",
      page_membership_sha256: pagePlan.page_membership_sha256,
      expected_group_count: groups.length,
    };
    const controls = new Map();
    for (const groupRows of groups) {
      const key = groupRows[0].forum_group_membership_key;
      const checkpointPath = path.join(
        controlPageRoot,
        v26.forumGroupNavigationCheckpointFilename(key),
      );
      const checkpointState = await readJsonBytes(checkpointPath);
      const validation = v26.validateForumGroupNavigationCheckpoint(
        checkpointState.value,
        groupRows[0],
        {
          parentForumChannelId: PARENT_FORUM_CHANNEL_ID,
          pageMembershipSha256: pagePlan.page_membership_sha256,
        },
      );
      if (!validation.valid) {
        throw new Error(`Invalid v2.6 control ${key}: ${validation.errors.join(",")}`);
      }
      const evidence = checkpointState.value.evidence;
      const rowChildren = new Set(groupRows.map((row) => row?.inferred_thread_channel_id));
      if (
        evidence.evidence_key !== key ||
        rowChildren.size !== 1 ||
        !rowChildren.has(evidence.thread_channel_id) ||
        groupRows.some(
          (row) =>
            row?.thread_channel_id_source !== "forum_group_header_navigation_exact" ||
            row?.thread_channel_id_exact !== true ||
            row?.forum_group_navigation_validation?.valid !== true,
        )
      ) {
        throw new Error(`v2.6 row/control binding invalid for ${key}`);
      }
      controls.set(key, {
        checkpoint: checkpointState.value,
        checkpointPath,
        checkpointSha256: sha256(checkpointState.bytes),
        checkpointBytes: checkpointState.bytes.length,
      });
    }
    if (controls.size !== pagePlan.expected_group_count) {
      throw new Error("Validated v2.6 control count is incomplete");
    }

    const sourceUrls = [...new Set([...controls.values()].map((item) => item.checkpoint.evidence.source_url))];
    if (sourceUrls.length !== 1) throw new Error("v2.6 controls do not share one exact parent source URL");
    const currentSourceUrl = sourceUrls[0];

    context = { ...context, phase: "shadow_precomparison" };
    const shadowPlanState = await v26.persistForumNavigationPagePlan(shadowDayRoot, pagePlan);
    if (path.resolve(shadowPlanState.pageDirectory) !== path.resolve(shadowPageRoot)) {
      throw new Error("Shadow page plan escaped the exact v2.7 day/page root");
    }
    const shadowPlanBytes = await fs.readFile(shadowPlanState.pagePlanPath);
    const shadowPlanSha256 = sha256(shadowPlanBytes);
    const partition = v27.validatePremiumV27PagePartition(groups, pagePlan, {
      query: options.query,
      pageNumber: options.pageNumber,
      reportedTotal: options.reportedTotal,
      pageMembershipSha256: pagePlan.page_membership_sha256,
      currentSourceUrl,
      parentForumChannelId: PARENT_FORUM_CHANNEL_ID,
    });
    if (!partition.valid) {
      throw new Error(`v2.7 page partition invalid: ${partition.errors.join(",")}`);
    }

    const comparisons = [];
    const selections = new Map();
    for (const groupRows of groups) {
      const key = groupRows[0].forum_group_membership_key;
      const control = controls.get(key);
      const input = {
        groupRows,
        query: options.query,
        pageNumber: options.pageNumber,
        pageMembershipSha256: pagePlan.page_membership_sha256,
        pagePlanSha256: shadowPlanSha256,
        pagePlanBytes: shadowPlanBytes.length,
        currentSourceUrl,
      };
      const selection = v27.selectPremiumV27GroupResolution(input, { enableV27Pilot: true });
      selections.set(key, selection);
      const comparison = {
        evidence_key: key,
        group_message_ids: [...groupRows[0].forum_group_message_ids].sort(),
        v2_6_control_thread_channel_id: control.checkpoint.evidence.thread_channel_id,
        v2_6_control_checkpoint_path: relativeTo(options.artifactRoot, control.checkpointPath),
        v2_6_control_checkpoint_sha256: control.checkpointSha256,
        v2_6_control_checkpoint_bytes: control.checkpointBytes,
        shadow_method: selection.method,
        shadow_direct_thread_channel_id: selection.thread_channel_id || null,
        shadow_direct_errors: selection.direct_errors || [],
        direct_key_match:
          selection.method === "direct_consensus_v2_7"
            ? selection.evidence.evidence_key === key
            : null,
        direct_child_match:
          selection.method === "direct_consensus_v2_7"
            ? selection.thread_channel_id === control.checkpoint.evidence.thread_channel_id
            : null,
      };
      if (selection.method === "direct_consensus_v2_7") {
        const validation = v27.validatePremiumV27DirectEvidence(selection.evidence, input);
        if (!validation.valid) {
          throw new Error(`Invalid direct evidence ${key}: ${validation.errors.join(",")}`);
        }
        if (!comparison.direct_key_match || !comparison.direct_child_match) {
          throw new Error(`Direct/control key or child mismatch for ${key}`);
        }
      }
      comparisons.push(comparison);
    }

    context = { ...context, phase: "shadow_checkpoint_persistence" };
    const pageResult = await v27.resolvePremiumV27Page({
      groups,
      query: options.query,
      pageNumber: options.pageNumber,
      reportedTotal: options.reportedTotal,
      pageMembershipSha256: pagePlan.page_membership_sha256,
      pagePlanPath: shadowPlanState.pagePlanPath,
      currentSourceUrl,
      checkpointDirectory: shadowPlanState.pageDirectory,
      artifactRoot: options.artifactRoot,
      routeDay: ROUTE_DAY,
      enableV27Pilot: true,
      headerResolver: async (groupRows, resolverContext) => {
        const key = groupRows[0].forum_group_membership_key;
        const control = controls.get(key);
        if (!control) throw new Error(`Missing prevalidated control for fallback ${key}`);
        const saved = await v26.persistForumGroupNavigationCheckpoint(
          shadowPlanState.pageDirectory,
          control.checkpoint.evidence,
          groupRows[0],
          {
            parentForumChannelId: PARENT_FORUM_CHANNEL_ID,
            pageMembershipSha256: resolverContext.pageMembershipSha256,
          },
        );
        return {
          method: "header_navigation_v2_6",
          evidence: saved.checkpoint.evidence,
          checkpoint: saved.checkpoint,
          checkpointPath: saved.checkpointPath,
        };
      },
    });
    if (!pageResult.accepted) throw new Error("v2.7 shadow resolver did not accept the complete page");

    for (const comparison of comparisons) {
      const resolution = pageResult.resolutions[comparison.evidence_key];
      const expectedMethod = selections.get(comparison.evidence_key).method;
      if (!resolution || resolution.method !== expectedMethod) {
        throw new Error(`Shadow resolution method mismatch for ${comparison.evidence_key}`);
      }
      comparison.shadow_resolved_thread_channel_id = resolution.thread_channel_id;
      comparison.shadow_checkpoint_path = relativeTo(options.artifactRoot, resolution.checkpoint_path);
      comparison.shadow_checkpoint_sha256 = resolution.checkpoint_sha256;
      comparison.shadow_checkpoint_bytes = resolution.checkpoint_bytes;
      comparison.resolved_child_match =
        resolution.thread_channel_id === comparison.v2_6_control_thread_channel_id;
      if (!comparison.resolved_child_match || resolution.evidence_key !== comparison.evidence_key) {
        throw new Error(`Persisted shadow/control mismatch for ${comparison.evidence_key}`);
      }
    }

    const expectedShadowFiles = new Set([path.resolve(shadowPlanState.pagePlanPath)]);
    for (const resolution of Object.values(pageResult.resolutions)) {
      expectedShadowFiles.add(path.resolve(resolution.checkpoint_path));
    }
    const actualShadowFiles = new Set(
      (await fs.readdir(shadowPageRoot, { withFileTypes: true }))
        .filter((entry) => entry.isFile())
        .map((entry) => path.resolve(shadowPageRoot, entry.name)),
    );
    if (
      actualShadowFiles.size !== expectedShadowFiles.size ||
      [...actualShadowFiles].some((filePath) => !expectedShadowFiles.has(filePath))
    ) {
      throw new Error("Shadow checkpoint file set is not exact for the accepted page");
    }
    if (await fs.stat(forbiddenCanonicalPath).then(() => true).catch(() => false)) {
      throw new Error("A forbidden v2.7 canonical appeared during shadow processing");
    }

    const directCount = comparisons.filter(
      (item) => item.shadow_method === "direct_consensus_v2_7",
    ).length;
    const fallbackCount = comparisons.length - directCount;
    const report = {
      schema_version: "1.0.0",
      artifact_type: "premium_journals_v2_7_shadow_control_comparison",
      mode: "shadow_nonpromotable",
      route_day: ROUTE_DAY,
      query: options.query,
      reported_total: options.reportedTotal,
      page_number: options.pageNumber,
      page_rows: pageRows.length,
      source_artifact: {
        path: relativeTo(options.artifactRoot, options.artifactPath),
        sha256: sha256(artifactState.bytes),
        bytes: artifactState.bytes.length,
      },
      v2_6_authority: true,
      v2_6_control_group_count: controls.size,
      all_v2_6_groups_header_navigated: true,
      page_plan: {
        path: relativeTo(options.artifactRoot, shadowPlanState.pagePlanPath),
        sha256: shadowPlanSha256,
        bytes: shadowPlanBytes.length,
        page_membership_sha256: pagePlan.page_membership_sha256,
        expected_group_count: pagePlan.expected_group_count,
      },
      shadow: {
        collector_version: "2.7",
        live_collection_enabled: false,
        promotion_allowed: false,
        canonical_written: false,
        accepted: true,
        direct_count: directCount,
        fallback_count: fallbackCount,
        direct_key_match_count: comparisons.filter((item) => item.direct_key_match === true).length,
        direct_child_match_count: comparisons.filter((item) => item.direct_child_match === true).length,
        all_resolution_child_match_count: comparisons.filter((item) => item.resolved_child_match).length,
      },
      comparisons,
      exact_shadow_files: [...actualShadowFiles]
        .sort()
        .map((filePath) => relativeTo(options.artifactRoot, filePath)),
      observed_at_utc: new Date().toISOString(),
    };
    const reportState = await writeJsonExclusiveAtomic(comparisonPath, report);
    return {
      status: "pass",
      page_number: options.pageNumber,
      rows: pageRows.length,
      control_groups: controls.size,
      shadow_direct: directCount,
      shadow_fallback: fallbackCount,
      direct_key_matches: report.shadow.direct_key_match_count,
      direct_child_matches: report.shadow.direct_child_match_count,
      all_resolution_child_matches: report.shadow.all_resolution_child_match_count,
      comparison_path: comparisonPath,
      comparison_sha256: reportState.sha256,
      comparison_bytes: reportState.bytes,
    };
  } catch (error) {
    const quarantine = {
      schema_version: "1.0.0",
      artifact_type: "premium_journals_v2_7_shadow_quarantine",
      mode: "shadow_nonpromotable",
      route_day: ROUTE_DAY,
      query: options.query,
      reported_total: options.reportedTotal,
      ...context,
      shadow_stopped: true,
      promotion_allowed: false,
      error_name: error?.name || "Error",
      error_message: error?.message || String(error),
      observed_at_utc: new Date().toISOString(),
    };
    if (!(await fs.stat(quarantinePath).then(() => true).catch(() => false))) {
      await writeJsonExclusiveAtomic(quarantinePath, quarantine);
    }
    throw error;
  }
}

main()
  .then((result) => process.stdout.write(`${JSON.stringify(result)}\n`))
  .catch((error) => {
    process.stderr.write(`${error?.stack || error}\n`);
    process.exitCode = 2;
  });

