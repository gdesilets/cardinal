from __future__ import annotations

import os
import re
import subprocess
import sys
import unittest
from pathlib import Path


HERE = Path(__file__).resolve().parent
RUNBOOK = HERE / "FINAL_PIPELINE_RUNBOOK.md"


def powershell_invocations() -> list[str]:
    text = RUNBOOK.read_text(encoding="utf-8")
    blocks = re.findall(r"```powershell\s*(.*?)```", text, flags=re.DOTALL)
    invocations: list[str] = []
    for block in blocks:
        joined = re.sub(r"`\r?\n\s*", " ", block)
        invocations.extend(
            match.group(1).strip()
            for match in re.finditer(
                r"^\s*Invoke-CheckedPython\s+([^\r\n]+)",
                joined,
                flags=re.MULTILINE,
            )
        )
    return invocations


def invocation_for_output(output: str) -> str:
    pattern = re.compile(
        rf"(?:^|\s)--(?:output|output-dir|markdown-output)\s+{re.escape(output)}(?:\s|$)"
    )
    matches = [row for row in powershell_invocations() if pattern.search(row)]
    if len(matches) != 1:
        raise AssertionError(f"Expected one invocation for {output!r}; found {len(matches)}")
    return matches[0]


class FinalPipelineRunbookContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.text = RUNBOOK.read_text(encoding="utf-8")
        cls.invocations = powershell_invocations()

    def test_every_documented_python_flag_exists_in_actual_help(self) -> None:
        checked = 0
        for invocation in self.invocations:
            tokens = invocation.split()
            if not tokens or tokens[0] == "-m":
                continue
            script = HERE / tokens[0]
            self.assertTrue(script.is_file(), f"Missing runbook script: {tokens[0]}")
            subcommand = None
            if len(tokens) > 1 and not tokens[1].startswith("-"):
                if tokens[0] in {"discord_attachment_archiver.py"}:
                    subcommand = tokens[1]
            command = [sys.executable, str(script)]
            if subcommand:
                command.append(subcommand)
            command.append("--help")
            result = subprocess.run(
                command,
                cwd=HERE,
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            help_text = result.stdout + result.stderr
            for flag in re.findall(r"(?<!\w)--[a-z][a-z0-9-]*", invocation):
                self.assertIn(flag, help_text, f"{tokens[0]} does not document {flag}")
            checked += 1
        self.assertGreaterEqual(checked, 10)

    def test_every_powershell_block_parses_without_execution(self) -> None:
        parser_script = r'''
$text = Get-Content -LiteralPath $env:RUNBOOK_CONTRACT_PATH -Raw
$matches = [regex]::Matches($text, '(?s)```powershell\s*(.*?)```')
$allErrors = @()
for ($index = 0; $index -lt $matches.Count; $index++) {
  $tokens = $null
  $parseErrors = $null
  [void][System.Management.Automation.Language.Parser]::ParseInput(
    $matches[$index].Groups[1].Value,
    [ref]$tokens,
    [ref]$parseErrors
  )
  foreach ($parseError in $parseErrors) {
    $allErrors += "block=$($index + 1): $($parseError.Message)"
  }
}
if ($allErrors.Count -gt 0) {
  $allErrors | Write-Error
  exit 1
}
Write-Output $matches.Count
'''
        result = subprocess.run(
            ["powershell.exe", "-NoProfile", "-Command", parser_script],
            cwd=HERE,
            env={**os.environ, "RUNBOOK_CONTRACT_PATH": str(RUNBOOK)},
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertGreaterEqual(int(result.stdout.strip()), 10)

    def test_forbidden_override_and_broad_scope_flags_are_not_used(self) -> None:
        forbidden = {
            "--replace",
            "--overwrite",
            "--force",
            "--allow-failures",
            "--relevance-segment-dir",
            "--audit-segment-dir",
            "--relevance-plan",
            "--orchestrator-progress-manifest",
        }
        used = {
            flag
            for invocation in self.invocations
            for flag in re.findall(r"(?<!\w)--[a-z][a-z0-9-]*", invocation)
        }
        self.assertFalse(forbidden & used)

    def test_exact_questions_identity_is_explicit(self) -> None:
        self.assertIn("`❓│questions`", self.text)
        self.assertIn("`in:❓│questions`", self.text)
        self.assertIn("`in:questions`", self.text)
        self.assertIn("logical display label only", self.text)

    def test_both_corpus_builds_are_strictly_scoped(self) -> None:
        builds = [row for row in self.invocations if row.startswith("build_corpus.py ")]
        self.assertEqual(len(builds), 2)
        for row in builds:
            self.assertEqual(row.count("--segment-dir "), 2)
            self.assertIn("--segment-dir raw/channel_segments", row)
            self.assertIn("--segment-dir raw/channel_segments_v2_5", row)
            self.assertIn("--authorized-scope authorized_collection_scope.json", row)
            self.assertIn(
                "--scoped-child-inventory-reconciliation "
                "working/premium_journals_scoped_inventory_reconciliation.json",
                row,
            )
            self.assertIn("--data-cutoff-utc $CutoffUtc", row)

    def test_premium_authoritative_root_and_non_census_closure_are_explicit(self) -> None:
        self.assertIn("`raw/channel_segments_v2_5/`", self.text)
        self.assertIn("preservation-only and never authoritative", self.text)
        self.assertIn(
            '$PremiumSource.premium_collector_version_required -ne "2.6"',
            self.text,
        )
        self.assertIn("$PremiumSource.accepted_premium_segment_count -ne 201", self.text)
        self.assertIn("$PremiumClosure.closure_proven -ne $true", self.text)
        self.assertIn(
            "$Scope.child_inventory_reconciliation.inventory_complete -ne $false",
            self.text,
        )

    def test_both_database_builds_use_scope_corpus_and_manifest(self) -> None:
        builds = [
            row for row in self.invocations if row.startswith("build_cardinal_database_v2.py ")
        ]
        self.assertEqual(len(builds), 2)
        for row in builds:
            self.assertEqual(row.count("--input "), 2)
            self.assertIn("--authorized-scope authorized_collection_scope.json", row)
            self.assertIn("--window-start 2026-01-01T06:00:00Z", row)
            self.assertIn("--window-end 2026-07-21T05:00:00Z", row)

    def test_final_package_uses_scoped_post_final_evidence(self) -> None:
        package = invocation_for_output(
            "Cardinal_Discord_Research_2026-01-01_2026-07-20"
        )
        self.assertIn(
            "--release-evidence final/scoped_post_final_release_evidence.json",
            package,
        )
        self.assertIn("--corpus-manifest final/coverage_manifest_release.json", package)

    def test_scoped_release_evidence_and_qa_replace_broad_workflow(self) -> None:
        evidence = invocation_for_output(
            "final/scoped_post_final_release_evidence.json"
        )
        self.assertTrue(evidence.startswith("build_scoped_release_evidence.py "))
        self.assertIn("--authorized-scope authorized_collection_scope.json", evidence)
        self.assertIn("--database final/cardinal_analyzed.sqlite", evidence)
        qa = invocation_for_output("final/independent_qa_report.json")
        self.assertTrue(qa.startswith("qa/validate_scoped_release.py "))
        self.assertIn(
            "--release-evidence final/scoped_post_final_release_evidence.json", qa
        )
        self.assertIn(
            "--drift-audit working/scoped_collection_drift_final.json", qa
        )

    def test_major_phases_are_ordered_fail_closed(self) -> None:
        ordered = [
            invocation_for_output("working/scoped_corpus_preflight.json"),
            invocation_for_output("working/scoped_cardinal_preflight.sqlite"),
            invocation_for_output("working/scoped_cardinal_preflight_analyzed.sqlite"),
            invocation_for_output("final/raw_corpus_release.json"),
            invocation_for_output("final/cardinal_pristine.sqlite"),
            invocation_for_output("final/cardinal_analyzed.sqlite"),
            invocation_for_output("final/discord_trading_research.md"),
            invocation_for_output("final/cardinal_llm.sqlite"),
            invocation_for_output("working/scoped_collection_drift_final.json"),
            invocation_for_output("final/scoped_post_final_release_evidence.json"),
            invocation_for_output("final/independent_qa_report.json"),
            invocation_for_output("final/LLM_HANDOFF_GUIDE.md"),
            invocation_for_output(
                "Cardinal_Discord_Research_2026-01-01_2026-07-20"
            ),
        ]
        positions = [self.invocations.index(row) for row in ordered]
        self.assertEqual(positions, sorted(positions))

    def test_cutoff_and_write_once_guards_are_explicit(self) -> None:
        self.assertIn(
            '$RequiredEndUtc = [DateTimeOffset]"2026-07-21T05:00:00Z"',
            self.text,
        )
        self.assertEqual(self.text.count("--data-cutoff-utc $CutoffUtc"), 2)
        self.assertIn("$WriteOnceOutputs", self.text)
        self.assertIn("Raw and quarantine bytes are unchanged.", self.text)


if __name__ == "__main__":
    unittest.main()
