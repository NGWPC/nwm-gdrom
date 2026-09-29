"""Tests for `nwm_gdrom.rule_parser`."""

from __future__ import annotations

import math
from typing import TYPE_CHECKING

import pytest

from nwm_gdrom.rule_parser import (
    ModuleKind,
    OpCode,
    RuleParseError,
    VarCode,
    parse_module,
    parse_module_condition,
)

if TYPE_CHECKING:
    from pathlib import Path


def _write(path: Path, body: str) -> Path:
    path.write_text(body)
    return path


def test_parse_constant_module(tmp_path: Path) -> None:
    p = _write(tmp_path / "40_0.txt", "Release: 35.0377\n")
    mod = parse_module(p)
    assert mod.grand_id == 40
    assert mod.module_id == 0
    assert mod.kind == ModuleKind.EXPR
    assert mod.expr is not None
    assert mod.expr.a_inflow == 0.0
    assert mod.expr.a_storage == 0.0
    assert mod.expr.c == pytest.approx(35.0377)
    assert math.isinf(mod.expr.clamp_min)
    assert mod.expr.clamp_min < 0
    assert mod.tree_branches == ()


def test_parse_linear_module(tmp_path: Path) -> None:
    p = _write(tmp_path / "366_0.txt", "Release = 0.9981 * Inflow + 5.5427\n")
    mod = parse_module(p)
    assert mod.kind == ModuleKind.EXPR
    assert mod.expr is not None
    assert mod.expr.a_inflow == pytest.approx(0.9981)
    assert mod.expr.a_storage == 0.0
    assert mod.expr.c == pytest.approx(5.5427)


def test_parse_linear_negative_coeffs(tmp_path: Path) -> None:
    p = _write(tmp_path / "10045_0.txt", "Release = -1.8976 * Inflow + 2.6837\n")
    mod = parse_module(p)
    assert mod.expr is not None
    assert mod.expr.a_inflow == pytest.approx(-1.8976)
    assert mod.expr.c == pytest.approx(2.6837)


def test_parse_multivariate_linear(tmp_path: Path) -> None:
    """Res_M uses `Release = k1*Inflow + k2*Storage + b`; both coefs populated."""
    p = _write(
        tmp_path / "10043_1.txt", "Release = 0.9531 * Inflow + -0.2061 * Storage + 510.0264\n"
    )
    mod = parse_module(p)
    assert mod.kind == ModuleKind.EXPR
    assert mod.expr is not None
    assert mod.expr.a_inflow == pytest.approx(0.9531)
    assert mod.expr.a_storage == pytest.approx(-0.2061)
    assert mod.expr.c == pytest.approx(510.0264)


def test_parse_nan_constant(tmp_path: Path) -> None:
    p = _write(tmp_path / "543_0.txt", "Release: nan\n")
    mod = parse_module(p)
    assert mod.kind == ModuleKind.EXPR
    assert mod.expr is not None
    assert math.isnan(mod.expr.c)


def test_parse_tree_module_with_constants(tmp_path: Path) -> None:
    body = (
        "if (Inflow <= 9225.8) then Release: 4333.5\n"
        "if (Inflow > 9225.8) and (Storage <= 956320.7) then Release: 2716.9\n"
        "if (Inflow > 9225.8) and (Storage > 956320.7) then Release: 5666.7\n"
    )
    mod = parse_module(_write(tmp_path / "41_0.txt", body))
    assert mod.kind == ModuleKind.TREE
    assert len(mod.tree_branches) == 3

    b0 = mod.tree_branches[0]
    assert len(b0.predicates) == 1
    assert b0.predicates[0].var_code == VarCode.INFLOW
    assert b0.predicates[0].op_code == OpCode.LE
    assert b0.predicates[0].threshold == pytest.approx(9225.8)
    assert b0.release.c == pytest.approx(4333.5)

    b2 = mod.tree_branches[2]
    assert len(b2.predicates) == 2
    assert (b2.predicates[0].var_code, b2.predicates[0].op_code) == (VarCode.INFLOW, OpCode.GT)
    assert (b2.predicates[1].var_code, b2.predicates[1].op_code) == (VarCode.STORAGE, OpCode.GT)
    assert b2.release.c == pytest.approx(5666.7)


def test_parse_tree_with_max_clamp_linear_terminal(tmp_path: Path) -> None:
    """Some Res_M TREE branches terminate in `max(k*Storage + b, 0)`."""
    body = (
        "if (Storage < 140.7708) then Release: 303.1716\n"
        "if (Storage >= 140.7708) then Release: max(0.0404 * Storage + 491.7276, 0)\n"
    )
    mod = parse_module(_write(tmp_path / "10047_2.txt", body))
    assert mod.kind == ModuleKind.TREE
    assert len(mod.tree_branches) == 2

    # Second branch has linear-with-clamp
    b1 = mod.tree_branches[1]
    assert b1.release.a_storage == pytest.approx(0.0404)
    assert b1.release.c == pytest.approx(491.7276)
    assert b1.release.clamp_min == 0.0


def test_parse_condition_file(tmp_path: Path) -> None:
    body = (
        "if (Storage <= 311646.7) and (PDSI <= -3.97) and (DOY <= 92.0) then module: 1\n"
        "if (Storage > 311646.7) and (PDSI <= -3.97) and (DOY <= 92.0) then module: 0\n"
    )
    cond = parse_module_condition(_write(tmp_path / "41.txt", body))
    assert cond.grand_id == 41
    assert len(cond.branches) == 2

    b0 = cond.branches[0]
    assert b0.module_id == 1
    vars_ = [p.var_code for p in b0.predicates]
    assert vars_ == [VarCode.STORAGE, VarCode.PDSI, VarCode.DOY]


def test_parse_empty_predicate_condition_branch(tmp_path: Path) -> None:
    """Some Res_M files contain `if () then module: X` placeholder branches."""
    cond = parse_module_condition(_write(tmp_path / "1010.txt", "if () then module: 0\n"))
    assert cond.grand_id == 1010
    assert len(cond.branches) == 1
    assert cond.branches[0].predicates == ()
    assert cond.branches[0].module_id == 0


def test_parse_rejects_tree_with_pdsi_in_module(tmp_path: Path) -> None:
    p = _write(tmp_path / "99_0.txt", "if (PDSI <= 0) then Release: 1.0\n")
    with pytest.raises(RuleParseError, match="Inflow or Storage"):
        parse_module(p)


def test_parse_rejects_malformed_line(tmp_path: Path) -> None:
    p = _write(tmp_path / "99_0.txt", "garbage content\n")
    with pytest.raises(RuleParseError, match="unrecognized rule line"):
        parse_module(p)


def test_parse_rejects_empty_file(tmp_path: Path) -> None:
    p = _write(tmp_path / "99_0.txt", "\n")
    with pytest.raises(RuleParseError, match="empty"):
        parse_module(p)


def test_parse_rejects_bad_filename(tmp_path: Path) -> None:
    p = _write(tmp_path / "junk.txt", "Release: 1.0\n")
    with pytest.raises(RuleParseError, match="grand_id"):
        parse_module(p)


def test_parse_all_fixture_modules(modules_dir: Path) -> None:
    """Parse every module file in the in-repo fixture set; both kinds must appear."""
    files = sorted(modules_dir.glob("*.txt"))
    assert len(files) > 0, "no fixture module files found"
    counts = {ModuleKind.EXPR: 0, ModuleKind.TREE: 0}
    for f in files:
        mod = parse_module(f)
        counts[mod.kind] += 1
    assert counts[ModuleKind.EXPR] > 0
    assert counts[ModuleKind.TREE] > 0
    assert sum(counts.values()) == len(files)


def test_parse_all_fixture_conditions(conditions_dir: Path) -> None:
    """Parse every condition file in the in-repo fixture set."""
    files = sorted(conditions_dir.glob("*.txt"))
    assert len(files) > 0, "no fixture condition files found"
    for f in files:
        cond = parse_module_condition(f)
        assert len(cond.branches) >= 1
        for br in cond.branches:
            assert br.module_id >= 0


@pytest.mark.fulldata
def test_parse_all_full_corpus_modules(full_modules_dir: Path) -> None:
    """Parse every module file in the full GDROM v2 corpus (4,832 files)."""
    files = sorted(full_modules_dir.glob("*.txt"))
    assert len(files) > 4000, f"expected thousands of module files, saw {len(files)}"
    counts = {ModuleKind.EXPR: 0, ModuleKind.TREE: 0}
    for f in files:
        mod = parse_module(f)
        counts[mod.kind] += 1
    assert counts[ModuleKind.EXPR] > 0
    assert counts[ModuleKind.TREE] > 0
    assert sum(counts.values()) == len(files)


@pytest.mark.fulldata
def test_parse_all_full_corpus_conditions(full_conditions_dir: Path) -> None:
    """Parse every condition file in the full GDROM v2 corpus (1,540 files)."""
    files = sorted(full_conditions_dir.glob("*.txt"))
    assert len(files) > 1000
    for f in files:
        cond = parse_module_condition(f)
        assert len(cond.branches) >= 1
        for br in cond.branches:
            assert br.module_id >= 0
