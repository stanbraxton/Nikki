"""Turn-level guards: stop a reasoning loop before it becomes a bill.

Nothing here talks to the model. `TurnBudget` is bookkeeping the UI layer consults
between tool rounds; `convex_lint` is a static check the engineer toolchain runs
before a push.

Background: on 2026-09-18 a Pivoten import job pushed the commit "Step 2: Enable
Pivoten import HTTP endpoint" three separate times, each triggering a full Cloud
Build, because nothing recorded that the same two-step dance had already been
tried. Every guard below exists to make that specific failure cheap.

`convex_lint` is verified by tests/test_guards.py, which exercises it against the
real source of four files in this portfolio — two carrying live build failures,
two already correct. The negative cases are the load-bearing ones: three earlier
revisions of this lint each flagged code that compiles fine. Run the tests before
trusting any change to the patterns.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any

# ── Turn budget ─────────────────────────────────────────────────────────────


def call_signature(name: str, args: dict | None) -> str:
    """Stable identity for a tool call, so repeats are recognisable across rounds."""
    try:
        payload = json.dumps(args or {}, sort_keys=True, default=str)
    except (TypeError, ValueError):
        payload = str(args)
    return f"{name}:{payload}"


@dataclass
class UsageMeter:
    """Accumulate token usage across a turn, counting each model call exactly once.

    Two traps, both hit in this codebase:

    - The usage fields used to be ASSIGNED rather than added, and a turn makes one
      model call per tool round, so only the final round was ever recorded. Cost
      telemetry was least accurate on exactly the runaway turns it exists to find.
    - astream runs with stream_mode=["messages", "updates"], so one call arrives
      twice -- as streamed chunks and as the completed AIMessage -- and both carry
      usage_metadata. Naively adding doubles every count.

    Chunks of one message share its id, so dedupe on that. A message with no id can
    only be counted from an authoritative (final) observation.

    Shared by the interactive path (TurnRenderer) and the headless path, which had
    no accounting at all.
    """

    input_tokens: int = 0
    output_tokens: int = 0
    # Subsets of input_tokens. langchain-anthropic folds cache reads/writes INTO
    # input_tokens, but a cache read is billed at 0.1x. Kept separately so the
    # token ceiling can judge real cost instead of raw volume.
    cache_read: int = 0
    cache_creation: int = 0
    seen: set[str] = field(default_factory=set)

    @property
    def total_tokens(self) -> int:
        return self.input_tokens + self.output_tokens

    def note(self, msg: Any, *, final: bool = False) -> bool:
        """Record this message's usage if it has not been counted. True if counted."""
        usage = getattr(msg, "usage_metadata", None)
        if not usage:
            return False
        key = getattr(msg, "id", None)
        if key is not None:
            if key in self.seen:
                return False
            self.seen.add(key)
        elif not final:
            return False
        self.input_tokens += usage.get("input_tokens") or 0
        self.output_tokens += usage.get("output_tokens") or 0
        details = usage.get("input_token_details") or {}
        self.cache_read += details.get("cache_read") or 0
        self.cache_creation += details.get("cache_creation") or 0
        return True


async def daily_cap_message() -> str | None:
    """The stop message if today's token cap is already spent, else None."""
    from app import persistence
    from app.config import settings

    cap = settings.daily_token_cap
    if cap <= 0:
        return None
    used = await persistence.tokens_used_today()
    if used < cap:
        return None
    return (
        f"⚠️ **Problem:** today's token cap is used up ({used:,} of {cap:,} cost-weighted tokens since 00:00 UTC), "
        "so I've stopped before spending more. This guard exists to catch runaway loops.\n\n"
        "**Possible solutions:**\n"
        "1. Wait until 00:00 UTC, when the cap resets.\n"
        "2. Raise `NIKKI_DAILY_TOKEN_CAP` on the Cloud Run service (0 turns it off).\n"
        "3. Check /turns for what used the tokens today.\n\n"
        "**Recommendation:** option 3 first — a day this heavy is usually a loop, not real work."
    )


async def calls_model_next(graph: Any, config: dict, inp: Any) -> bool:
    """True when the next graph segment will make a model call (i.e. is a real tool round).

    The graph pauses twice per round: interrupt_before=["tools"] (so calls can be
    approved/blocked) and interrupt_after=["tools"] (so a hot-loaded skill can be
    bound). The segment resumed from the before-tools pause only RUNS the tools and
    stops again; it never calls the model. Counting every segment therefore charged
    two rounds per tool batch, and NIKKI_MAX_TOOL_ROUNDS=16 fired after 8 real
    rounds (thread b7399b70, 2026-09-23: 8 tool calls, "used all 16 rounds").
    """
    if inp is not None:
        return True  # fresh input always goes to the model
    state = await graph.aget_state(config)
    return "tools" not in (state.next or ())


@dataclass
class TurnBudget:
    """Per-turn limits. One instance per user turn, carried through the tool loop.

    Token totals are deliberately NOT held here. TurnRenderer already accumulates
    them in note_usage(), which dedupes each model call across the two stream
    modes; a second copy of that state would only drift. The caller passes its
    running totals into the checks below.
    """

    max_tool_rounds: int
    max_repeats: int
    token_ceiling: int

    rounds: int = 0
    seen: dict[str, int] = field(default_factory=dict)

    def start_round(self) -> None:
        self.rounds += 1

    # -- checks --------------------------------------------------------------

    def round_limit_hit(self) -> bool:
        return self.rounds >= self.max_tool_rounds

    @staticmethod
    def cost_weighted(input_tokens: int, output_tokens: int,
                      cache_read: int = 0, cache_creation: int = 0) -> int:
        """Tokens weighted by what they are billed at, in plain-input-token units.

        Every tool round re-sends the whole conversation, but after the first call
        that prefix comes from the prompt cache at 0.1x (writes cost 1.25x). Counting
        cache reads at full price stopped ordinary multi-file engineering turns at
        ~400k "tokens" that actually cost a fraction of that (golden-picks
        Mismatch Hunter build, 2026-09-23: three turns in a row, each stopped).
        """
        plain = max(0, input_tokens - cache_read - cache_creation)
        return int(plain + 0.1 * cache_read + 1.25 * cache_creation + output_tokens)

    def token_ceiling_hit(self, total_tokens: int) -> bool:
        return self.token_ceiling > 0 and total_tokens >= self.token_ceiling

    def record(self, name: str, args: dict | None) -> int:
        """Count this call and return how many times it has now been seen this turn."""
        sig = call_signature(name, args)
        self.seen[sig] = self.seen.get(sig, 0) + 1
        return self.seen[sig]

    def would_repeat(self, name: str, args: dict | None) -> bool:
        """True when this exact call has already been made max_repeats times."""
        return self.seen.get(call_signature(name, args), 0) >= self.max_repeats

    # -- messages ------------------------------------------------------------

    def repeat_message(self, name: str) -> str:
        return (
            f"BLOCKED: you have already called `{name}` with these exact arguments "
            f"{self.max_repeats} time(s) in this turn, and the result did not change. "
            "Repeating it will not help. Stop, state plainly what you were trying to "
            "achieve, what actually happened, and either try a materially different "
            "approach or ask the user how to proceed."
        )

    def summary(self, input_tokens: int, output_tokens: int,
                cache_read: int = 0, cache_creation: int = 0) -> str:
        total = input_tokens + output_tokens
        cost = self.cost_weighted(input_tokens, output_tokens, cache_read, cache_creation)
        return (
            f"{self.rounds} tool round(s), ~{total:,} tokens "
            f"({input_tokens:,} in [{cache_read:,} cache read, {cache_creation:,} cache write] "
            f"/ {output_tokens:,} out), ~{cost:,} cost-weighted"
        )

    def stop_reason(self, input_tokens: int = 0, output_tokens: int = 0,
                    cache_read: int = 0, cache_creation: int = 0) -> str | None:
        total = self.cost_weighted(input_tokens, output_tokens, cache_read, cache_creation)
        if self.token_ceiling_hit(total):
            return (
                f"⚠️ **Problem:** this turn hit its token ceiling ({total:,} of "
                f"{self.token_ceiling:,} cost-weighted) and was stopped before it could cost more.\n\n"
                "**Possible solutions:**\n"
                "1. Ask me for a smaller, more specific step.\n"
                "2. Start a new thread if the history has grown large.\n"
                "3. Raise NIKKI_TURN_TOKEN_CEILING if this task genuinely needs more.\n\n"
                "**Recommendation:** option 1 — a stopped turn here usually means I was "
                "going in circles, and a narrower request fixes that faster than a bigger budget."
            )
        if self.round_limit_hit():
            if any(n >= 2 for n in self.seen.values()):
                return (
                    f"⚠️ **Problem:** this turn used all {self.max_tool_rounds} of its tool rounds "
                    "and repeated some of the same calls, so I may have been going in circles.\n\n"
                    "**Possible solutions:**\n"
                    "1. Ask me to do one concrete step instead of the whole task.\n"
                    "2. Tell me what you saw go wrong, so I stop repeating the same approach.\n\n"
                    "**Recommendation:** option 1."
                )
            # Every call was different: this was real progress on a long task, not a loop.
            # Calling it "looping" (as this message used to) was simply wrong.
            return (
                f"⏸️ I've used this turn's {self.max_tool_rounds} tool steps and I'm not finished yet. "
                "Nothing is lost - reply **continue** and I'll pick up where I stopped."
            )
        return None


# ── Convex static guard ─────────────────────────────────────────────────────
#
# A real `tsc` run needs node_modules, and installing them is not viable in
# Nikki's 1 GiB container (repo_run's own docstring forbids `bun install`).
# This is not a typecheck. It catches the two patterns that have broken every
# Convex build in this portfolio, at zero cost, before a push.
#
# It is SCHEMA-AWARE, and has to be. Three revisions, each corrected by running
# against real code:
#
#   v1  flagged any `args.x` inside a .withIndex() callback.
#       Wrong: flagged stations.create(), where stationNumber is REQUIRED.
#   v2  flagged only fields declared v.optional in the file.
#       Wrong: stations.ts declares stationNumber optional in update() and
#       required in create(); file-level scope leaked between them.
#   v3  scoped optionality per exported function.
#       Wrong: flagged mia.record() in wellcollar, which compiles fine —
#       miaInspections.clientId is itself optional, so .eq() accepts undefined.
#
# The actual rule: an OPTIONAL argument compared against a REQUIRED schema
# column. Both halves are needed. v4 reads schema.ts and checks the column.
# Verified: 0 findings across 82 files of a green-building branch; still
# catches the live agencyos bug.

_TABLE_DEF = re.compile(r"(\w+)\s*:\s*defineTable\(\{", re.M)
_OPTIONAL_FIELD = re.compile(r"(\w+)\s*:\s*v\.optional\(")
_WITH_INDEX = re.compile(
    r"\.with(?:Index|SearchIndex)\(\s*[\"'][^\"']+[\"']\s*,\s*\(\s*\w+\s*\)\s*=>(?P<body>.*?)\)\s*[,;)\n]",
    re.S,
)
_QUERY_TABLE = re.compile(r"\.query\(\s*[\"'](\w+)[\"']\s*\)")
# The trailing ) is optional: the enclosing .withIndex() match may have eaten it.
_EQ = re.compile(r"\.eq\(\s*[\"'](?P<field>\w+)[\"']\s*,\s*(?P<val>[A-Za-z_][\w.]*)\s*\)?")
_ARGS_MEMBER = re.compile(r"^(args|updates)\.(\w+)$")
_LET_QUERY = re.compile(r"\blet\s+(\w+)\s*=\s*ctx\.db\s*\.query\(")
_EXPORT = re.compile(r"^export\s+const\s+(\w+)\s*=", re.M)


@dataclass
class LintFinding:
    path: str
    line: int
    rule: str
    message: str

    def __str__(self) -> str:
        return f"{self.path}:{self.line}  [{self.rule}] {self.message}"


def parse_schema(schema_source: str) -> dict[str, set[str]]:
    """{table name: set of fields declared v.optional}, by brace matching each defineTable."""
    out: dict[str, set[str]] = {}
    for m in _TABLE_DEF.finditer(schema_source):
        i = m.end() - 1
        depth, j = 0, i
        while j < len(schema_source):
            c = schema_source[j]
            if c == "{":
                depth += 1
            elif c == "}":
                depth -= 1
                if depth == 0:
                    break
            j += 1
        out[m.group(1)] = {x.group(1) for x in _OPTIONAL_FIELD.finditer(schema_source[i:j])}
    return out


def convex_lint(path: str, source: str, schema: dict[str, set[str]] | None = None) -> list[LintFinding]:
    """Static checks for the two TypeScript patterns that break Convex builds here.

    narrowing-in-callback
        An argument declared `v.optional(...)` compared inside a .withIndex()
        callback against a column that is REQUIRED in the schema. TypeScript
        discards narrowing from an enclosing `if` once it crosses into a
        callback, so the value is still `T | undefined` where a `T` is needed.
        Hoist it into a const outside the callback.

        If the indexed column is itself optional, .eq() accepts undefined and
        there is no error — that case is not reported. Without a schema the
        column cannot be checked, so findings are marked "verify".

    query-initializer-reassign
        `let q = ctx.db.query(...)` later assigned from `.withIndex(...)`.
        query() returns QueryInitializer; withIndex() returns Query, which
        lacks fullTableScan/withIndex/withSearchIndex. TS2739.

    Regex, not a parser: it under-reports rather than over-reports. Callbacks
    with destructured or multi-parameter signatures are not matched, and the
    table is taken from the nearest preceding .query("...") in the same block.
    """
    if not path.endswith((".ts", ".tsx")) or "/convex/" not in f"/{path}":
        return []

    findings: list[LintFinding] = []

    def line_of(pos: int) -> int:
        return source.count("\n", 0, pos) + 1

    marks = [(m.group(1), m.start()) for m in _EXPORT.finditer(source)] or [("<module>", 0)]
    for i, (fn, start) in enumerate(marks):
        end = marks[i + 1][1] if i + 1 < len(marks) else len(source)
        block, offset = source[start:end], start
        optional_args = {m.group(1) for m in _OPTIONAL_FIELD.finditer(block)}

        if optional_args:
            for m in _WITH_INDEX.finditer(block):
                table = None
                for tm in _QUERY_TABLE.finditer(block[: m.start()]):
                    table = tm.group(1)
                table_optional = schema.get(table) if (schema and table) else None

                for eq in _EQ.finditer(m.group("body")):
                    am = _ARGS_MEMBER.match(eq.group("val"))
                    if not am or am.group(2) not in optional_args:
                        continue
                    field = eq.group("field")
                    if table_optional is not None and field in table_optional:
                        continue  # indexed column is optional — .eq() accepts undefined
                    note = "" if table_optional is not None else " (schema unavailable — verify)"
                    findings.append(
                        LintFinding(
                            path=path,
                            line=line_of(offset + m.start("body") + eq.start()),
                            rule="narrowing-in-callback",
                            message=(
                                f"in {fn}(): `{eq.group('val')}` is optional but "
                                f"`{table}.{field}` is required, so .eq() needs a defined "
                                f"value — and narrowing is discarded inside the callback. "
                                f"Hoist it: `const {am.group(2)} = {eq.group('val')};`{note}"
                            ),
                        )
                    )

        for m in _LET_QUERY.finditer(block):
            var = m.group(1)
            reassign = re.search(
                rf"\b{re.escape(var)}\s*=\s*{re.escape(var)}\s*\.with(?:Index|SearchIndex)\(", block
            )
            if reassign:
                findings.append(
                    LintFinding(
                        path=path,
                        line=line_of(offset + reassign.start()),
                        rule="query-initializer-reassign",
                        message=(
                            f"in {fn}(): `{var}` is typed QueryInitializer from "
                            f"ctx.db.query(), but is assigned a Query from .withIndex(). "
                            f"That is TS2739. Build each branch as one complete chain."
                        ),
                    )
                )

    return findings


def convex_lint_tree(files: dict[str, str]) -> list[LintFinding]:
    """Lint {relative_path: source}. Finds convex/schema.ts in the map and uses it."""
    schema = None
    for path, source in files.items():
        if path.endswith("convex/schema.ts") or path == "schema.ts":
            schema = parse_schema(source)
            break
    out: list[LintFinding] = []
    for path, source in sorted(files.items()):
        out.extend(convex_lint(path, source, schema))
    return out
