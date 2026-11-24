import asyncio
import json
import random
import struct
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import syntropy as s  # noqa: E402
import disasm  # noqa: E402


@pytest.fixture(autouse=True)
def _quiet(monkeypatch):
    monkeypatch.setattr(s, "SOUND_ENABLED", False)
    monkeypatch.setattr(s, "NUM_REGS", 32)
    monkeypatch.setattr(s, "REGISTER_SCAN_ACTIONS", True)
    random.seed(1234)


def addi(rd, rs1, imm):
    return s.asm("ADDI", rd, rs1, imm)


def ecall(a7, a0=None, a1_word=None):
    """Program fragment: a7=<a7>; optional a0; then ECALL."""
    prog = []
    if a0 is not None:
        prog.append(addi(10, 0, a0))
    prog.append(addi(17, 0, a7))
    prog.append(0x73)
    return prog


# ── VM robustness ────────────────────────────────────────────

def test_mstore_high_bit_value_does_not_crash():
    g = [s.asm("LUI", 11, 0x80000), addi(10, 0, 1)] + ecall(7)
    bot = s.RVVMBot(g)
    bot.execute(5.0, {})
    assert struct.unpack_from("<i", bot.local_mem, 4)[0] == -2**31


@pytest.mark.parametrize("bad", [float("nan"), float("inf"), float("-inf"), 1e300])
def test_non_finite_sensors_do_not_crash(bad):
    bot = s.RVVMBot(ecall(0, a0=0))
    bot.execute(5.0, {0: bad})
    assert -2**31 <= s.s32(bot.r[10]) <= 2**31 - 1


def test_non_finite_budget_is_safe():
    s.RVVMBot([0x13]).execute(float("nan"), {})


def test_local_memory_persists_between_ticks():
    g = [addi(11, 0, 77), addi(10, 0, 3)] + ecall(7)
    bot = s.RVVMBot(g)
    bot.execute(5.0, {})
    reader = s.RVVMBot(ecall(6, a0=3))
    reader.local_mem = bot.local_mem
    reader.execute(5.0, {})
    assert reader.r[10] == 77


def test_act_move_north_arg0_is_not_dropped():
    # ACT(a0=MOVE=0, a1=0): the all-zero word used to be indistinguishable from "empty".
    bot = s.RVVMBot(ecall(1, a0=0))
    acts = bot.execute(5.0, {})
    assert (s.Action.MOVE, 0, 0.0) in acts


def test_register_scan_can_be_disabled(monkeypatch):
    monkeypatch.setattr(s, "REGISTER_SCAN_ACTIONS", False)
    bot = s.RVVMBot([addi(5, 0, 2)])  # x5 = 2 would be REST-ish via scan
    assert bot.execute(5.0, {}) == []


@pytest.mark.parametrize("n", [1, 2, 4, 8, 32])
def test_reduced_register_file_never_indexes_out_of_range(monkeypatch, n):
    monkeypatch.setattr(s, "NUM_REGS", n)
    for _ in range(50):
        bot = s.RVVMBot(s.random_genome(40))
        bot.execute(8.0, {i: random.random() for i in range(s.NUM_SENSORS)})


def test_sensing_with_reduced_registers_still_works(monkeypatch):
    monkeypatch.setattr(s, "NUM_REGS", 4)
    bot = s.RVVMBot(ecall(0, a0=0))
    bot.execute(5.0, {0: 1.5})
    assert bot.r[(10 % 4) or 1] == 1500


def test_sub_word_loads_stores_respect_bounds():
    # SB/LB at the very last byte of memory must work (used to need 4 bytes).
    g = [s.asm("LUI", 5, 1), addi(5, 5, -1), addi(6, 0, 0x7F),
         s.asm("SB", 5, 6, 0), s.asm("LB", 7, 5, 0)]
    bot = s.RVVMBot(g)
    bot.execute(5.0, {})
    assert bot.r[7] == 0x7F


def test_random_genomes_never_raise():
    for _ in range(300):
        bot = s.RVVMBot(s.random_genome(random.randint(3, 60)))
        bot.execute(8.0, {i: random.uniform(-5, 5) for i in range(s.NUM_SENSORS)})


# ── World behaviour ──────────────────────────────────────────

def _world(n_orgs=0):
    w = s.World()
    w.organisms.clear()
    for _ in range(n_orgs):
        w._spawn(random.randrange(s.WIDTH), random.randrange(s.HEIGHT),
                 s.random_genome(random.randint(10, 30)))
    return w


def test_spatial_index_matches_linear_scan():
    w = _world(150)
    for _ in range(400):
        w._add_resource(random.randrange(s.WIDTH), random.randrange(s.HEIGHT))
    w._dead_this_tick = set()

    def linear_res(x, y):
        best, bd = None, 999
        for (fx, fy) in w.resources:
            d = abs(x - fx) + abs(y - fy)
            if d < bd:
                bd, best = d, (fx, fy)
        return best, bd

    def linear_org(org):
        best, bd = None, 999
        for o in w.organisms:
            if o is org:
                continue
            d = abs(org.x - o.x) + abs(org.y - o.y)
            if d < bd:
                bd, best = d, o
        return best, bd

    w._build_sense_index()
    for o in w.organisms:
        assert w._nearest_resource(o.x, o.y) == linear_res(o.x, o.y)
        assert w._nearest_organism(o) == linear_org(o)


def test_world_survives_faulty_vm(monkeypatch):
    w = _world(5)

    def boom(self, *a, **k):
        raise struct.error("simulated VM fault")

    monkeypatch.setattr(s.RVVMBot, "execute", boom)
    asyncio.run(w.step())
    assert w.vm_faults == 5


def test_world_runs_many_ticks_without_exceptions():
    w = _world(30)
    for _ in range(150):
        asyncio.run(w.step())
        if not w.organisms:
            break


def test_cultural_transmission_copies_local_memory_with_small_noise():
    w = _world(0)
    parent = w._spawn(5, 5, s.random_genome(10))
    for slot in range(s.VM_MEM_SIZE):
        struct.pack_into("<i", parent.vm.local_mem, slot * 4, 1000 * (slot + 1))
    child = w._spawn(5, 5, s.random_genome(10), parent_mem=parent.vm.local_mem)
    for slot in range(s.VM_MEM_SIZE):
        v = struct.unpack_from("<i", child.vm.local_mem, slot * 4)[0]
        assert abs(v - 1000 * (slot + 1)) <= 12


# ── Tooling ──────────────────────────────────────────────────

def test_disasm_lists_genome_and_skips_legacy(tmp_path, capsys):
    genome = [addi(5, 0, 7), 0x73, s.asm("ADD", 6, 5, 5)]
    data = {"results": [
        {"score": 10, "max_energy": 100, "max_movement_speed": 0, "n_registers": 8,
         "metrics": {"ticks": 10, "best_genome": genome, "best_vm": "x"}},
        {"score": 5, "max_energy": 100, "max_movement_speed": 0, "n_registers": 8,
         "metrics": {"ticks": 5, "best_vm": "gen=1 vm=[NOP(0,0)]"}},
    ]}
    p = tmp_path / "search.json"
    p.write_text(json.dumps(data))
    disasm.main([str(p)])
    out = capsys.readouterr()
    assert "addi" in out.out and "add" in out.out
    assert "skipped 1 legacy" in out.err


def test_search_trial_emits_genome():
    import search
    r = search._trial_worker((200, 0, 8, 0.01, 3))
    assert "metrics" in r, r
    assert "best_genome" in r["metrics"]
