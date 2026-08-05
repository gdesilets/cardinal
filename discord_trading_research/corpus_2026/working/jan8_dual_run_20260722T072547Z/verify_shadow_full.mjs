import crypto from "node:crypto";
import fs from "node:fs/promises";
import path from "node:path";
import { isDeepStrictEqual } from "node:util";

import * as v26 from "../../../discord_browser_collector.mjs";
import * as v27 from "../../../discord_browser_collector_v2_7.mjs";

const QUERY = "in:premium-journals after:2026-01-07 before:2026-01-09";
const ROUTE_DAY = "2026-01-08";
const REPORTED_TOTAL = 162;
const REPORTED_PAGES = 7;
const PARENT_ID = "1283941772577472643";

function parseArgs(argv) {
  const args = {};
  for (let index = 0; index < argv.length; index += 2) {
    if (!String(argv[index] || "").startsWith("--") || argv[index + 1] === undefined) {
      throw new Error("Arguments must be --name value pairs");
    }
    args[argv[index].slice(2)] = argv[index + 1];
  }
  for (const key of ["artifact", "stage-nav", "corpus", "audit"]) {
    if (!args[key]) throw new Error(`Missing --${key}`);
  }
  return {
    artifactPath: path.resolve(args.artifact),
    stageNavigationRoot: path.resolve(args["stage-nav"]),
    artifactRoot: path.resolve(args.corpus),
    auditRoot: path.resolve(args.audit),
  };
}

function digest(bytes) {
  return crypto.createHash("sha256").update(bytes).digest("hex");
}

async function readJson(filePath) {
  const bytes = await fs.readFile(filePath);
  return { bytes, value: JSON.parse(bytes.toString("utf8")) };
}

function relative(root, filePath) {
  return path.relative(root, filePath).split(path.sep).join("/");
}

async function writeExclusive(filePath, value) {
  const bytes = Buffer.from(`${JSON.stringify(value, null, 2)}\n`, "utf8");
  const temporary = `${filePath}.${process.pid}.${crypto.randomUUID()}.tmp`;
  try {
    await fs.writeFile(temporary, bytes, { flag: "wx" });
    await fs.link(temporary, filePath);
    await fs.unlink(temporary);
  } catch (error) {
    await fs.unlink(temporary).catch(() => {});
    throw error;
  }
  return { sha256: digest(bytes), bytes: bytes.length };
}

async function main() {
  const options = parseArgs(process.argv.slice(2));
  const artifactState = await readJson(options.artifactPath);
  const artifact = artifactState.value;
  if (
    artifact.collector_version !== "2.6" ||
    artifact.complete !== true ||
    artifact.segment?.query !== QUERY ||
    artifact.reported_total !== REPORTED_TOTAL ||
    artifact.reported_pages !== REPORTED_PAGES ||
    artifact.messages?.length !== REPORTED_TOTAL
  ) {
    throw new Error("Final v2.6 artifact does not match the frozen Jan8 route");
  }

  const shadowDayRoot = path.join(
    options.artifactRoot,
    "raw",
    "premium_journals_v2_7_checkpoints",
    ROUTE_DAY,
  );
  const forbiddenCanonical = path.join(
    options.artifactRoot,
    "raw",
    "channel_segments_v2_7",
    `channel_premium_journals_${PARENT_ID}_${ROUTE_DAY}_${ROUTE_DAY}.json`,
  );
  if (await fs.stat(forbiddenCanonical).then(() => true).catch(() => false)) {
    throw new Error("Forbidden v2.7 canonical exists");
  }

  const pageSummaries = [];
  const allKeys = new Set();
  const allControlChildren = new Map();
  const allShadowChildren = new Map();
  let totalControls = 0;
  let totalDirect = 0;
  let totalFallback = 0;
  let totalDirectKeyMatches = 0;
  let totalDirectChildMatches = 0;
  let totalResolutionChildMatches = 0;

  for (let pageNumber = 1; pageNumber <= REPORTED_PAGES; pageNumber += 1) {
    const pageLabel = `page_${String(pageNumber).padStart(3, "0")}`;
    const controlPageRoot = path.join(options.stageNavigationRoot, pageLabel);
    const shadowPageRoot = path.join(shadowDayRoot, pageLabel);
    const controlPlanState = await readJson(path.join(controlPageRoot, "page_plan.json"));
    const shadowPlanState = await readJson(path.join(shadowPageRoot, "page_plan.json"));
    if (!isDeepStrictEqual(controlPlanState.value, shadowPlanState.value)) {
      throw new Error(`${pageLabel} shadow page plan differs from the v2.6 control plan`);
    }
    const pagePlan = controlPlanState.value;
    const planValidation = v26.validateForumNavigationPagePlan(pagePlan, {
      query: QUERY,
      pageNumber,
      reportedTotal: REPORTED_TOTAL,
    });
    if (!planValidation.valid) {
      throw new Error(`${pageLabel} page plan invalid: ${planValidation.errors.join(",")}`);
    }
    const pageRows = artifact.messages.filter((row) => row.page_number === pageNumber);
    const expectedRows = Math.min(25, REPORTED_TOTAL - (pageNumber - 1) * 25);
    if (pageRows.length !== expectedRows) throw new Error(`${pageLabel} final row count mismatch`);
    const groupsByKey = new Map();
    for (const row of pageRows) {
      const key = row.forum_group_membership_key;
      if (!groupsByKey.has(key)) groupsByKey.set(key, []);
      groupsByKey.get(key).push(row);
    }
    const groups = pagePlan.expected_group_evidence_keys.map((key) => groupsByKey.get(key));
    if (groups.some((group) => !Array.isArray(group) || group.length === 0)) {
      throw new Error(`${pageLabel} final group partition is incomplete`);
    }

    const controls = new Map();
    for (const groupRows of groups) {
      const key = groupRows[0].forum_group_membership_key;
      if (allKeys.has(key)) throw new Error(`Cross-page duplicate evidence key ${key}`);
      allKeys.add(key);
      const checkpointPath = path.join(
        controlPageRoot,
        v26.forumGroupNavigationCheckpointFilename(key),
      );
      const checkpointState = await readJson(checkpointPath);
      const validation = v26.validateForumGroupNavigationCheckpoint(
        checkpointState.value,
        groupRows[0],
        {
          parentForumChannelId: PARENT_ID,
          pageMembershipSha256: pagePlan.page_membership_sha256,
        },
      );
      if (!validation.valid) {
        throw new Error(`${pageLabel} control ${key} invalid: ${validation.errors.join(",")}`);
      }
      controls.set(key, checkpointState.value);
      allControlChildren.set(key, checkpointState.value.evidence.thread_channel_id);
    }
    const sourceUrls = [...new Set([...controls.values()].map((item) => item.evidence.source_url))];
    if (sourceUrls.length !== 1) throw new Error(`${pageLabel} control source URL set is not exact`);
    const currentSourceUrl = sourceUrls[0];
    const partition = v27.validatePremiumV27PagePartition(groups, pagePlan, {
      query: QUERY,
      pageNumber,
      reportedTotal: REPORTED_TOTAL,
      pageMembershipSha256: pagePlan.page_membership_sha256,
      currentSourceUrl,
      parentForumChannelId: PARENT_ID,
    });
    if (!partition.valid) {
      throw new Error(`${pageLabel} v2.7 partition invalid: ${partition.errors.join(",")}`);
    }

    const expectedFiles = new Set([path.resolve(shadowPageRoot, "page_plan.json")]);
    let pageDirect = 0;
    let pageFallback = 0;
    let pageChildMatches = 0;
    for (const groupRows of groups) {
      const key = groupRows[0].forum_group_membership_key;
      const control = controls.get(key);
      const input = {
        groupRows,
        query: QUERY,
        pageNumber,
        pageMembershipSha256: pagePlan.page_membership_sha256,
        pagePlanSha256: digest(shadowPlanState.bytes),
        pagePlanBytes: shadowPlanState.bytes.length,
        currentSourceUrl,
      };
      const selection = v27.selectPremiumV27GroupResolution(input, { enableV27Pilot: true });
      const isDirect = selection.method === "direct_consensus_v2_7";
      const filename = isDirect
        ? v27.checkpointFilename(key)
        : v26.forumGroupNavigationCheckpointFilename(key);
      const shadowCheckpointPath = path.join(shadowPageRoot, filename);
      expectedFiles.add(path.resolve(shadowCheckpointPath));
      const shadowCheckpointState = await readJson(shadowCheckpointPath);
      let shadowChild;
      if (isDirect) {
        pageDirect += 1;
        totalDirect += 1;
        const validation = v27.validatePremiumV27Checkpoint(
          shadowCheckpointState.value,
          shadowCheckpointState.value.evidence,
          input,
        );
        if (!validation.valid) {
          throw new Error(`${pageLabel} direct checkpoint ${key} invalid: ${validation.errors.join(",")}`);
        }
        if (selection.evidence.evidence_key === key) totalDirectKeyMatches += 1;
        shadowChild = shadowCheckpointState.value.evidence.thread_channel_id;
        if (selection.thread_channel_id === control.evidence.thread_channel_id) {
          totalDirectChildMatches += 1;
        }
      } else {
        pageFallback += 1;
        totalFallback += 1;
        const validation = v26.validateForumGroupNavigationCheckpoint(
          shadowCheckpointState.value,
          groupRows[0],
          {
            parentForumChannelId: PARENT_ID,
            pageMembershipSha256: pagePlan.page_membership_sha256,
          },
        );
        if (!validation.valid || !isDeepStrictEqual(shadowCheckpointState.value.evidence, control.evidence)) {
          throw new Error(`${pageLabel} fallback checkpoint ${key} does not exactly preserve its control`);
        }
        shadowChild = shadowCheckpointState.value.evidence.thread_channel_id;
      }
      allShadowChildren.set(key, shadowChild);
      if (shadowChild !== control.evidence.thread_channel_id) {
        throw new Error(`${pageLabel} shadow/control child mismatch for ${key}`);
      }
      pageChildMatches += 1;
      totalResolutionChildMatches += 1;
    }
    totalControls += controls.size;

    const actualFiles = new Set(
      (await fs.readdir(shadowPageRoot, { withFileTypes: true }))
        .filter((entry) => entry.isFile())
        .map((entry) => path.resolve(shadowPageRoot, entry.name)),
    );
    if (
      actualFiles.size !== expectedFiles.size ||
      [...actualFiles].some((filePath) => !expectedFiles.has(filePath))
    ) {
      throw new Error(`${pageLabel} shadow file set is not exact`);
    }
    const comparisonPath = path.join(
      options.auditRoot,
      `${pageLabel}_shadow_control_comparison.json`,
    );
    const comparisonState = await readJson(comparisonPath);
    if (
      comparisonState.value.shadow?.accepted !== true ||
      comparisonState.value.v2_6_control_group_count !== controls.size ||
      comparisonState.value.shadow.direct_count !== pageDirect ||
      comparisonState.value.shadow.fallback_count !== pageFallback ||
      comparisonState.value.shadow.all_resolution_child_match_count !== pageChildMatches
    ) {
      throw new Error(`${pageLabel} saved comparison report does not match the final re-derivation`);
    }
    pageSummaries.push({
      page_number: pageNumber,
      rows: pageRows.length,
      controls: controls.size,
      direct: pageDirect,
      fallback: pageFallback,
      all_resolution_child_matches: pageChildMatches,
      comparison_report: {
        path: relative(options.artifactRoot, comparisonPath),
        sha256: digest(comparisonState.bytes),
        bytes: comparisonState.bytes.length,
      },
    });
  }

  const dayEntries = await fs.readdir(shadowDayRoot, { withFileTypes: true });
  const expectedPageDirectories = new Set(
    Array.from({ length: REPORTED_PAGES }, (_, index) => `page_${String(index + 1).padStart(3, "0")}`),
  );
  if (
    dayEntries.length !== expectedPageDirectories.size ||
    dayEntries.some((entry) => !entry.isDirectory() || !expectedPageDirectories.has(entry.name))
  ) {
    throw new Error("Shadow day root does not contain exactly seven page directories");
  }
  if (
    totalControls !== allKeys.size ||
    totalControls !== totalDirect + totalFallback ||
    totalDirectKeyMatches !== totalDirect ||
    totalDirectChildMatches !== totalDirect ||
    totalResolutionChildMatches !== totalControls ||
    allControlChildren.size !== totalControls ||
    allShadowChildren.size !== totalControls
  ) {
    throw new Error("Full-run shadow/control aggregate reconciliation failed");
  }

  const report = {
    schema_version: "1.0.0",
    artifact_type: "premium_journals_v2_7_full_shadow_verification",
    status: "PASS",
    mode: "shadow_nonpromotable",
    route_day: ROUTE_DAY,
    query: QUERY,
    reported_total: REPORTED_TOTAL,
    reported_pages: REPORTED_PAGES,
    source_v2_6_artifact: {
      path: relative(options.artifactRoot, options.artifactPath),
      sha256: digest(artifactState.bytes),
      bytes: artifactState.bytes.length,
    },
    v2_6_authority: true,
    all_v2_6_groups_header_navigated: true,
    v2_6_control_group_count: totalControls,
    shadow_live_collection_enabled: false,
    shadow_promotion_allowed: false,
    shadow_canonical_written: false,
    shadow_direct_count: totalDirect,
    shadow_fallback_count: totalFallback,
    direct_key_match_count: totalDirectKeyMatches,
    direct_child_match_count: totalDirectChildMatches,
    all_resolution_child_match_count: totalResolutionChildMatches,
    page_summaries: pageSummaries,
    verified_at_utc: new Date().toISOString(),
  };
  const outputPath = path.join(options.auditRoot, "shadow_full_verification.json");
  const outputState = await writeExclusive(outputPath, report);
  return {
    status: report.status,
    controls: totalControls,
    direct: totalDirect,
    fallback: totalFallback,
    direct_key_matches: totalDirectKeyMatches,
    direct_child_matches: totalDirectChildMatches,
    all_resolution_child_matches: totalResolutionChildMatches,
    report_path: outputPath,
    report_sha256: outputState.sha256,
    report_bytes: outputState.bytes,
  };
}

main()
  .then((result) => process.stdout.write(`${JSON.stringify(result)}\n`))
  .catch((error) => {
    process.stderr.write(`${error?.stack || error}\n`);
    process.exitCode = 1;
  });

