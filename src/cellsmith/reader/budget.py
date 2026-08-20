# filepath: src/cellsmith/reader/budget.py
# %% [ai_schema:pointer]
# CellSmith-annotated file. Cells marked with `# %% [<cell_id>]`.
# To modify safely: load `CELLSMITH_PATCH_SCHEMA.md` at the project root
# for the full JSON patch schema (incl. required `changelog` block).
# Run `cellsmith status` first — if it errors, ignore these markers and
# edit the file directly per the user's request.
# %% [ai_schema:end]

# %% [module:init:start]
"""Character budgeting for a compiled read.

The budget exists to bound what an agent ingests, but a trace that stops in
the middle of a function is worse than useless — the agent cannot patch what
it cannot see in full. So the budget is only ever evaluated at a cell
boundary, and a cell that overruns by less than the grace buffer is committed
whole rather than dropped over a few bytes.
"""
# %% [module:init:end]

# %% [imports:start]
from dataclasses import dataclass, field
from typing import List

from cellsmith.reader.schema import GRACE_BUFFER
# %% [imports:end]


@dataclass
# %% [class:Budget:start]
class Budget:
    """Tracks spend against a character limit, in whole cells only."""

    max_characters: int
    grace: int = GRACE_BUFFER
    spent: int = 0
    pinned_spend: int = 0
    exhausted: bool = False
    dropped: List[str] = field(default_factory=list)

# %% [method:Budget.charge:start]
    def charge(self, cost: int, key: str = "", *, pinned: bool = False) -> bool:
        """Try to commit `cost` characters. Returns True when committed.

        Pinned cells (`trace_keep`) are always committed and still counted, so
        the budget reports honestly even when the caller overrides it.
        """
        if pinned:
            # The entry cell, `trace_keep` pins and each file's imports are
            # rendered whether or not they fit — a trace missing its own entry
            # point is useless. They are counted, but tracked separately so
            # the reported overage is explainable rather than mysterious.
            self.spent += cost
            self.pinned_spend += cost
            return True

        if self.exhausted:
            self.dropped.append(key)
            return False

        projected = self.spent + cost
        if projected <= self.max_characters:
            self.spent = projected
            return True

        if projected <= self.max_characters + self.grace:
            # Within the grace buffer: absorb this cell whole, then stop.
            self.spent = projected
            self.exhausted = True
            return True

        self.exhausted = True
        self.dropped.append(key)
        return False
# %% [method:Budget.charge:end]

    @property
# %% [method:Budget.truncated:start]
    def truncated(self) -> bool:
        """True when at least one cell was dropped for want of budget."""
        return bool(self.dropped)
# %% [method:Budget.truncated:end]

# %% [method:Budget.summary:start]
    def summary(self) -> str:
        state = "truncated" if self.truncated else "complete"
        line = (
            f"{self.spent} / {self.max_characters} chars "
            f"(whitespace excluded), {state}"
        )
        if self.pinned_spend:
            line += f"; {self.pinned_spend} pinned (exempt)"
        return line
# %% [method:Budget.summary:end]
# %% [class:Budget:end]
