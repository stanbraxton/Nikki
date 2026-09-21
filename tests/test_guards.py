"""Tests for app/guards.py::convex_lint.

Fixtures are real code from this portfolio, not invented examples. The negative
cases carry the weight — this lint was wrong three times, and each time it was
real code that caught it:

  v1  flagged any `args.x` in a .withIndex() callback
      → wrong on stations.create(): stationNumber is REQUIRED there.
  v2  flagged only fields declared v.optional, scoped per file
      → wrong: stations.ts has stationNumber optional in update(), required in
        create(); file scope leaked between them.
  v3  scoped optionality per exported function
      → wrong on wellcollar's mia.record(), which compiles fine, because
        miaInspections.clientId is ITSELF optional, so .eq() accepts undefined.

The real rule needs both halves: an OPTIONAL argument against a REQUIRED column.

Run:  python3 tests/test_guards.py
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.guards import convex_lint, parse_schema  # noqa: E402

SCHEMA_SRC = """
const schema = defineSchema({
  subAccounts: defineTable({
    orgId: v.id("orgs"),
    contactEmail: v.optional(v.string()),
    status: v.union(v.literal("active"), v.literal("paused"), v.literal("archived")),
  }).index("by_org", ["orgId"]).index("by_org_status", ["orgId", "status"]),

  curriculum: defineTable({
    subject: v.string(),
    gradeLevel: v.string(),
  }).index("by_subject_grade", ["subject", "gradeLevel"]),

  stations: defineTable({
    orgId: v.id("orgs"),
    stationNumber: v.number(),
  }).index("by_org_number", ["orgId", "stationNumber"]),

  miaInspections: defineTable({
    companyId: v.id("companies"),
    clientId: v.optional(v.string()),
  }).index("by_client", ["companyId", "clientId"]),
});
"""
SCHEMA = parse_schema(SCHEMA_SRC)

# agencyos, live bug: status is REQUIRED on the table
SUBACCOUNTS_BROKEN = """\
export const list = orgQuery({
  args: {
    status: v.optional(v.union(v.literal("active"), v.literal("paused"))),
  },
  handler: async (ctx, args) => {
    let query = ctx.db.query("subAccounts").withIndex("by_org", (q) => q.eq("orgId", ctx.orgId));
    if (args.status) {
      query = ctx.db
        .query("subAccounts")
        .withIndex("by_org_status", (q) =>
          q.eq("orgId", ctx.orgId).eq("status", args.status)
        );
    }
    return await query.collect();
  },
});
"""

SUBACCOUNTS_FIXED = """\
export const list = orgQuery({
  args: {
    status: v.optional(v.union(v.literal("active"), v.literal("paused"))),
  },
  handler: async (ctx, args) => {
    const { status } = args;
    return status
      ? await ctx.db.query("subAccounts")
          .withIndex("by_org_status", (q) => q.eq("orgId", ctx.orgId).eq("status", status))
          .collect()
      : await ctx.db.query("subAccounts")
          .withIndex("by_org", (q) => q.eq("orgId", ctx.orgId))
          .collect();
  },
});
"""

# smarttutor-ai, live bug: both rules fire
CURRICULUM_BROKEN = """\
export const listUnits = query({
  args: {
    subject: v.optional(v.string()),
    gradeLevel: v.optional(v.string()),
  },
  handler: async (ctx, args) => {
    let q = ctx.db.query("curriculum");
    if (args.subject && args.gradeLevel) {
      q = q.withIndex("by_subject_grade", (q) =>
        q.eq("subject", args.subject).eq("gradeLevel", args.gradeLevel)
      );
    }
    return await q.collect();
  },
});
"""

# wellcollar, CORRECT: clientId is optional ON THE TABLE, so .eq() accepts undefined
MIA_CORRECT = """\
export const record = mutation({
  args: { clientId: v.optional(v.string()) },
  handler: async (ctx, args) => {
    if (args.clientId) {
      const dup = await ctx.db
        .query("miaInspections")
        .withIndex("by_client", (q) =>
          q.eq("companyId", company._id).eq("clientId", args.clientId),
        )
        .first();
      if (dup) return dup;
    }
  },
});
"""

# redresponse-rms, CORRECT: required in create(), optional in update(), const-hoisted
STATIONS_CORRECT = """\
export const create = orgMutation({
  args: { stationNumber: v.number(), address: v.optional(v.string()) },
  handler: async (ctx, args) => {
    return await ctx.db.query("stations")
      .withIndex("by_org_number", (q) =>
        q.eq("orgId", ctx.orgId).eq("stationNumber", args.stationNumber)
      ).first();
  },
});

export const update = orgMutation({
  args: { id: v.id("stations"), stationNumber: v.optional(v.number()) },
  handler: async (ctx, { id, ...updates }) => {
    if (updates.stationNumber) {
      const stationNumber = updates.stationNumber; // Type narrowing for query
      return await ctx.db.query("stations")
        .withIndex("by_org_number", (q) =>
          q.eq("orgId", ctx.orgId).eq("stationNumber", stationNumber)
        ).first();
    }
    return id;
  },
});
"""


def test_catches_agencyos_live_bug():
    found = convex_lint("convex/subAccounts.ts", SUBACCOUNTS_BROKEN, SCHEMA)
    assert any(f.rule == "narrowing-in-callback" for f in found)
    assert any("args.status" in f.message for f in found)


def test_catches_smarttutor_live_bugs():
    found = convex_lint("convex/curriculum.ts", CURRICULUM_BROKEN, SCHEMA)
    rules = [f.rule for f in found]
    assert rules.count("narrowing-in-callback") == 2, f"subject + gradeLevel, got {rules}"
    assert "query-initializer-reassign" in rules


def test_optional_column_is_not_flagged():
    """v3 regression. mia.record() compiles: miaInspections.clientId is optional."""
    assert convex_lint("convex/mia.ts", MIA_CORRECT, SCHEMA) == []


def test_required_arg_and_hoisted_const_are_not_flagged():
    """v1 and v2 regression, both in one file."""
    assert convex_lint("convex/stations.ts", STATIONS_CORRECT, SCHEMA) == []


def test_proposed_fixes_pass_the_gate():
    """A fix that does not clear the gate is not a fix."""
    assert convex_lint("convex/subAccounts.ts", SUBACCOUNTS_FIXED, SCHEMA) == []


def test_ignores_files_outside_convex():
    assert convex_lint("src/pages/Dashboard.tsx", CURRICULUM_BROKEN, SCHEMA) == []


def test_schema_parses_optional_columns():
    assert "clientId" in SCHEMA["miaInspections"]
    assert "status" not in SCHEMA["subAccounts"]
    assert "stationNumber" not in SCHEMA["stations"]


def test_without_schema_it_still_warns():
    """No schema: cannot check the column, so report and say so rather than stay silent."""
    found = convex_lint("convex/subAccounts.ts", SUBACCOUNTS_BROKEN, None)
    assert found and "verify" in found[0].message


if __name__ == "__main__":
    failures = 0
    for name, fn in sorted(globals().items()):
        if not name.startswith("test_") or not callable(fn):
            continue
        try:
            fn()
            print(f"PASS  {name}")
        except AssertionError as e:
            failures += 1
            print(f"FAIL  {name}: {e or '(assertion)'}")
    print(f"\n{failures} failure(s)")
    sys.exit(1 if failures else 0)
