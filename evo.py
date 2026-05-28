#!/usr/bin/env python3
"""evo - VM-genome evolutionary ecosystem.

Each organism carries a Turing-complete VM as its genome.
Energy = gas. Traits emerge from VM programs, not hardcoded genes.
Async organisms, per-organism audio channels, day/night toroidal world.
"""

import asyncio
import argparse
import random
import time
import struct
import math
import json
import threading
import sys
from dataclasses import dataclass, field
from typing import Dict, List, Tuple, Optional, Set

SEED = 42
EXTINCTION_LOG_FILE = "extinction.json"
SOUND_ENABLED = True
SOUND_VOLUME = 0.3

WIDTH = 72
HEIGHT = 26
INITIAL_ORGANISMS = 40
INITIAL_RESOURCES = 60
RESOURCE_REGEN = 3
SEASON_LENGTH = (50, 80)
SUMMER_REGEN = 4
WINTER_REGEN = 1
SUMMER_BASE_COST = 0.06
WINTER_BASE_COST = 0.12
REPRODUCTION_THRESHOLD = 6.0
ENERGY_COST_PER_CHILD = 3.0
ENV_SHIFT_INTERVAL = (30, 60)
DAY_LENGTH = 120
MAX_SYSTEM_ENERGY = 200
MAX_MOVEMENT_SPEED = 0
MIGRATION_INTERVAL = (80, 150)
MIGRATION_BATCH = (3, 8)
TICK_RATE = 0.06

RESOURCE_TYPES = {
    "food":  {"value": 1.5, "symbol": "·", "weight": 0.65},
    "bounty": {"value": 5.5, "symbol": "★", "weight": 0.20},
    "corpse": {"value": 2.0, "symbol": "✿", "weight": 0.15},
}
RESOURCE_KEYS = list(RESOURCE_TYPES.keys())

# VM opcodes
class Op:
    NOP, MOV, ADD, SUB, MUL, DIV = range(6)
    JMP, JZ, JG, JL = range(6, 10)
    SENSE, ACT = 10, 11
    PUSH, POP, CALL, RET = 12, 13, 14, 15
    HALT, RAND, ENERGY = 16, 17, 18
    MOD, CMP = 19, 20
    AND, OR, XOR, NOT = 21, 22, 23, 24
    IND = 25
    MIN, MAX = 26, 27
    ABS, NEG = 28, 29
    DUP, JNE = 30, 31
    SWAP, GEN = 32, 33
    PICK, DEPTH = 34, 35
    PC, SETPC = 36, 37
    SQRT, EXP = 38, 39
    TICK, DROP, OVER = 40, 41, 42
    SHL, SHR, BIT = 43, 44, 45
    TOTAL = 46

# Per-opcode instruction cost (budget consumed per execution)
OP_COST = [
    0.002,  # NOP
    0.004,  # MOV
    0.006, 0.006,  # ADD, SUB
    0.012, 0.016,  # MUL, DIV
    0.006,         # JMP
    0.012, 0.012, 0.012,  # JZ, JG, JL
    0.014, 0.014,  # SENSE, ACT
    0.006, 0.006,  # PUSH, POP
    0.014, 0.010,  # CALL, RET
    0.002,         # HALT
    0.016, 0.006,  # RAND, ENERGY
    0.016, 0.008,  # MOD, CMP
    0.008, 0.008, 0.008, 0.006,  # AND, OR, XOR, NOT
    0.012,         # IND
    0.010, 0.010,  # MIN, MAX
    0.006, 0.006,  # ABS, NEG
    0.008, 0.012,  # DUP, JNE
    0.008, 0.010,  # SWAP, GEN
    0.008, 0.006,  # PICK, DEPTH
    0.006, 0.008,  # PC, SETPC
    0.014, 0.016,  # SQRT, EXP
    0.004, 0.004, 0.006,  # TICK, DROP, OVER
    0.008, 0.008, 0.006,  # SHL, SHR, BIT
]

class Sensor:
    FOOD_X, FOOD_Y, FOOD_DIST = 0, 1, 2
    ORG_X, ORG_Y, ORG_DIST = 3, 4, 5
    TEMP, DAYLIGHT, MOISTURE = 6, 7, 8
    ENERGY, AGE, POP_DENSITY = 9, 10, 11
    PRESSURE, SEASON, LATITUDE = 12, 13, 14
    FAT, HEALTH = 15, 16
    ACT_EAT, ACT_ATTACK = 17, 18
    TRACE = 19
    TRACE_DX, TRACE_DY = 20, 21
    SIGNAL = 22

class Action:
    MOVE_N, MOVE_S, MOVE_E, MOVE_W = 0, 1, 2, 3
    MOVE_TOWARD_FOOD, MOVE_AWAY_ORG = 4, 5
    EAT, ATTACK, REPRODUCE = 6, 7, 8
    REST, SOUND = 9, 10
    EMIT = 11
    TOTAL = 12

NUM_REGS = 4
NUM_SENSORS = 23
NUM_ACTIONS = 12



GLYPH_SET = "●◆▲■★✦⬟⬢◈◎◉"
COLORS = [
    "\033[31m", "\033[33m", "\033[32m", "\033[36m",
    "\033[34m", "\033[35m", "\033[91m", "\033[95m",
    "\033[92m", "\033[93m", "\033[94m",
]
RESET = "\033[0m"
BOLD = "\033[1m"
DIM = "\033[2m"

GENUS_ROOTS = [
    "Velox", "Altus", "Lentus", "Celer", "Fortis",
    "Sapiens", "Audax", "Placidus", "Mobilis", "Rusticus",
]
SPECIES_ROOTS = [
    "herba", "carnis", "omnis", "toxica", "therma",
    "chroma", "mutans", "agilis", "dorma", "vigil",
]
_name_cache: Dict[Tuple[int, ...], str] = {}


@dataclass
class GenomeVM:
    genome: List[int]
    regs: List[float] = field(default_factory=lambda: [0.0] * NUM_REGS)
    pc: int = 0
    stack: List[int] = field(default_factory=list)
    running: bool = True
    instr_count: int = 0

    def clone_mutated(self, rate: float = 0.06) -> 'GenomeVM':
        ng = list(self.genome)
        for i in range(len(ng)):
            if random.random() < rate:
                ng[i] = max(0, min(255, ng[i] + random.choice([-2, -1, 1, 2])))
        if random.random() < rate * 0.4 and len(ng) < 600:
            idx = (random.randrange(0, len(ng) + 1) // 3) * 3
            ng[idx:idx] = [random.randint(0, Op.TOTAL - 1),
                           random.randint(0, 255), random.randint(0, 255)]
        if random.random() < rate * 0.2 and len(ng) > 9:
            idx = (random.randrange(0, len(ng)) // 3) * 3
            ng = ng[:idx] + ng[idx + 3:]
        return GenomeVM(genome=ng)

    def crossover(self, other: 'GenomeVM') -> 'GenomeVM':
        pt = (random.randrange(0, min(len(self.genome), len(other.genome)), 3))
        return GenomeVM(genome=self.genome[:pt] + other.genome[pt:])

    def _rg(self, v: int) -> float:
        return self.regs[v % NUM_REGS]

    def _sr(self, v: int, val: float):
        self.regs[v % NUM_REGS] = val

    def _val(self, v: int) -> float:
        return self._rg(v) if v < 64 else float(v) / 16.0

    def execute(self, budget: float, senses: Dict[int, float], tick: int = 0) -> List[Tuple[int, int]]:
        self.regs = [0.0] * NUM_REGS
        self.pc = 0
        self.stack = []
        self.running = True
        self.instr_count = 0
        used = 0.0
        actions = []
        glen = len(self.genome)

        while self.running and used < budget and self.instr_count < 200:
            self.instr_count += 1

            if self.pc < 0 or self.pc >= glen - 2:
                break
            op = int(self.genome[self.pc]) % Op.TOTAL
            a1 = int(self.genome[self.pc + 1]) % 256
            a2 = int(self.genome[self.pc + 2]) % 256
            self.pc += 3
            if self.pc >= glen:
                self.pc = 0

            used += OP_COST[op]

            ridx = a1 % NUM_REGS
            v = self._val(a2)
            rv = self._rg(a1)

            if op == Op.NOP:
                pass
            elif op == Op.MOV:
                self._sr(ridx, v)
            elif op == Op.ADD:
                self._sr(ridx, rv + v)
            elif op == Op.SUB:
                self._sr(ridx, rv - v)
            elif op == Op.MUL:
                self._sr(ridx, rv * max(-50, min(50, v)))
            elif op == Op.DIV:
                if abs(v) > 0.001:
                    self._sr(ridx, rv / v)
            elif op == Op.JMP:
                self.pc = (a1 % max(3, glen)) // 3 * 3
            elif op == Op.JZ:
                if abs(rv) < 0.001:
                    self.pc = (a2 % max(3, glen)) // 3 * 3
            elif op == Op.JG:
                if rv > 0:
                    self.pc = (a2 % max(3, glen)) // 3 * 3
            elif op == Op.JL:
                if rv < 0:
                    self.pc = (a2 % max(3, glen)) // 3 * 3
            elif op == Op.SENSE:
                sid = a1 % NUM_SENSORS
                used += (a1 / 256.0) * 0.004
                self._sr(a2 % NUM_REGS, senses.get(sid, 0.0))
            elif op == Op.ACT:
                act_id = a1 % Action.TOTAL
                used += (a1 / 256.0) * 0.004
                actions.append((act_id, a2, a1 / 256.0))
            elif op == Op.PUSH:
                self.stack.append(int(rv))
            elif op == Op.POP:
                if self.stack:
                    self._sr(ridx, float(self.stack.pop()))
            elif op == Op.CALL:
                self.stack.append(self.pc)
                self.pc = (a1 % max(3, glen)) // 3 * 3
            elif op == Op.RET:
                if self.stack:
                    self.pc = self.stack.pop()
                else:
                    self.running = False
            elif op == Op.HALT:
                self.running = False
            elif op == Op.RAND:
                self._sr(ridx, random.random() * 10.0)
            elif op == Op.ENERGY:
                self._sr(ridx, budget - used)
            elif op == Op.MOD:
                if abs(v) > 0.001:
                    self._sr(ridx, rv % v)
            elif op == Op.CMP:
                self._sr(ridx, 1.0 if rv > v else (-1.0 if rv < v else 0.0))
            elif op == Op.AND:
                self._sr(ridx, float(int(rv) & int(v)))
            elif op == Op.OR:
                self._sr(ridx, float(int(rv) | int(v)))
            elif op == Op.XOR:
                self._sr(ridx, float(int(rv) ^ int(v)))
            elif op == Op.NOT:
                self._sr(ridx, float(~int(rv)))
            elif op == Op.IND:
                self._sr(ridx, self._rg(int(v) % NUM_REGS))
            elif op == Op.MIN:
                self._sr(ridx, min(rv, v))
            elif op == Op.MAX:
                self._sr(ridx, max(rv, v))
            elif op == Op.ABS:
                self._sr(ridx, abs(rv))
            elif op == Op.NEG:
                self._sr(ridx, -rv)
            elif op == Op.DUP:
                if self.stack:
                    self.stack.append(self.stack[-1])
            elif op == Op.JNE:
                if abs(rv) >= 0.001:
                    self.pc = (a2 % max(3, glen)) // 3 * 3
            elif op == Op.SWAP:
                r2 = a2 % NUM_REGS
                self.regs[ridx], self.regs[r2] = self.regs[r2], self.regs[ridx]
            elif op == Op.GEN:
                idx = a1 % max(1, len(self.genome))
                self._sr(a2 % NUM_REGS, float(self.genome[idx]))
            elif op == Op.PICK:
                if self.stack:
                    d = a1 % len(self.stack)
                    self._sr(a2 % NUM_REGS, float(self.stack[-d - 1]))
                else:
                    self._sr(a2 % NUM_REGS, 0.0)
            elif op == Op.DEPTH:
                self._sr(a2 % NUM_REGS, float(len(self.stack)))
            elif op == Op.PC:
                self._sr(a2 % NUM_REGS, float(self.pc))
            elif op == Op.SETPC:
                new_pc = int(abs(rv))
                self.pc = (new_pc % max(3, glen)) // 3 * 3
            elif op == Op.SQRT:
                self._sr(a2 % NUM_REGS, math.sqrt(max(0.0, rv)))
            elif op == Op.EXP:
                self._sr(a2 % NUM_REGS, math.exp(max(-10.0, min(10.0, rv))))
            elif op == Op.TICK:
                self._sr(a2 % NUM_REGS, float(tick))
            elif op == Op.DROP:
                if self.stack:
                    self.stack.pop()
            elif op == Op.OVER:
                if len(self.stack) >= 2:
                    self.stack.append(self.stack[-2])
            elif op == Op.SHL:
                self._sr(ridx, float(int(rv) << (a2 % 16)))
            elif op == Op.SHR:
                self._sr(ridx, float(int(rv) >> (a2 % 16)))
            elif op == Op.BIT:
                self._sr(a2 % NUM_REGS, 1.0 if (int(abs(rv)) >> (a1 & 7)) & 1 else 0.0)

        return actions


# Audio mixer — per-organism stereo audio in background thread
class AudioMixer:
    SR = 22050

    def __init__(self):
        self.orgs: Dict[int, Tuple[float, float, float, float, float, float, float]] = {}
        self.ambient: Dict[str, Tuple[float, float, float, float, float, float, float]] = {}
        self.stingers: Dict[str, Tuple[float, float, int, int]] = {}
        self.lock = threading.Lock()
        self._running = True
        self._task = None
        self._proc = None

    def set_org(self, oid: int, freq_bass: float, vol_bass: float,
                freq_mid: float, vol_mid: float,
                freq_treble: float, vol_treble: float):
        with self.lock:
            if vol_bass > 0.001 or vol_mid > 0.001 or vol_treble > 0.001:
                self.orgs[oid] = (freq_bass, vol_bass, freq_mid, vol_mid,
                                  freq_treble, vol_treble, time.time())
            else:
                self.orgs.pop(oid, None)

    def set_ambient(self, key: str,
                    freq_bass: float, vol_bass: float,
                    freq_mid: float, vol_mid: float,
                    freq_treble: float, vol_treble: float):
        with self.lock:
            if vol_bass > 0.0001 or vol_mid > 0.0001 or vol_treble > 0.0001:
                self.ambient[key] = (freq_bass, vol_bass,
                                     freq_mid, vol_mid,
                                     freq_treble, vol_treble, time.time())
            else:
                self.ambient.pop(key, None)

    def set_stinger(self, key: str, freq: float, vol: float, duration: float):
        with self.lock:
            samples = max(1, int(self.SR * duration))
            self.stingers[key] = (freq, vol, samples, samples)

    def remove_org(self, oid: int):
        with self.lock:
            self.orgs.pop(oid, None)

    async def start(self):
        if not SOUND_ENABLED:
            return
        try:
            self._proc = await asyncio.create_subprocess_exec(
                "aplay", "-q", "-f", "S16_LE", "-r", str(self.SR), "-c", "2",
                stdin=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL
            )
        except FileNotFoundError:
            self._running = False
            return
        self._task = asyncio.create_task(self._mix_loop())

    async def stop(self):
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        if self._proc and self._proc.stdin:
            try:
                self._proc.stdin.close()
                await self._proc.wait()
            except Exception:
                pass

    async def _mix_loop(self):
        buf_size = 512
        phases: Dict[tuple, float] = {}

        while self._running:
            master = SOUND_VOLUME
            with self.lock:
                orgs = dict(self.orgs)
                amb = dict(self.ambient)
                stings = dict(self.stingers)

            total = len(orgs) + len(amb) + len(stings)
            if total == 0:
                await asyncio.sleep(0.05)
                continue

            data = bytearray()
            for _ in range(buf_size):
                left = 0.0
                right = 0.0

                for oid, (fb, vb, fm, vm_, ft, vt_, _ts) in orgs.items():
                    spread = (oid % 100) / 100.0
                    l_gain = math.cos(spread * math.pi * 0.5)
                    r_gain = math.sin(spread * math.pi * 0.5)
                    for bi, (f, bv) in enumerate([(fb, vb), (fm, vm_), (ft, vt_)]):
                        if f < 1 or bv < 0.0001:
                            continue
                        pk = ('org', oid, bi)
                        p = phases.get(pk, 0.0)
                        p += 2 * math.pi * f / self.SR
                        phases[pk] = p
                        s = math.sin(p) * bv * master * 0.015
                        left += s * l_gain
                        right += s * r_gain

                for key, (fb, vb, fm, vm_, ft, vt_, _ts) in amb.items():
                    for bi, (f, bv) in enumerate([(fb, vb), (fm, vm_), (ft, vt_)]):
                        if f < 1 or bv < 0.0001:
                            continue
                        pk = ('amb', key, bi)
                        p = phases.get(pk, 0.0)
                        p += 2 * math.pi * f / self.SR
                        phases[pk] = p
                        s = math.sin(p) * bv * master * 0.02
                        left += s
                        right += s

                expired = []
                for skey, (f, sv, remain, total_s) in list(stings.items()):
                    if remain <= 0:
                        expired.append(skey)
                        continue
                    pk = ('st', skey, 0)
                    p = phases.get(pk, 0.0)
                    p += 2 * math.pi * f / self.SR
                    phases[pk] = p
                    env = remain / total_s
                    s = math.sin(p) * sv * env * master * 0.04
                    left += s
                    right += s
                    stings[skey] = (f, sv, remain - 1, total_s)

                with self.lock:
                    for skey in expired:
                        self.stingers.pop(skey, None)
                        phases.pop(('st', skey, 0), None)

                peak = max(abs(left), abs(right), 0.001)
                scale = 16384 / peak
                data.extend(struct.pack("<h", int(left * scale)))
                data.extend(struct.pack("<h", int(right * scale)))

            if self._proc and self._proc.stdin:
                try:
                    self._proc.stdin.write(bytes(data))
                    await asyncio.wait_for(self._proc.stdin.drain(), timeout=0.5)
                except Exception:
                    await asyncio.sleep(0.01)


def _species_name(genome: tuple) -> str:
    if genome in _name_cache:
        return _name_cache[genome]
    g = genome
    idx1 = (g[0] * 7 + g[3] * 5 + g[6] * 3 + g[9]) % len(GENUS_ROOTS) if len(g) > 9 else hash(tuple(g[:6])) % len(GENUS_ROOTS)
    idx2 = (g[1] * 11 + g[4] * 7 + g[7] * 5 + g[10] * 3 + g[12] * 2) % len(SPECIES_ROOTS) if len(g) > 12 else hash(tuple(g[3:6])) % len(SPECIES_ROOTS)
    variant = (g[0] * 13 + g[3] * 17 + g[6] * 19 + g[9] * 23) % 100 if len(g) > 9 else hash(tuple(g[:3])) % 100
    name = f"{GENUS_ROOTS[idx1]} {SPECIES_ROOTS[idx2]} v.{variant}"
    _name_cache[genome] = name
    return name


@dataclass
class Organism:
    x: int
    y: int
    vm: GenomeVM
    energy: float
    age: int
    generation: int
    id: int
    fat: float = 0.0
    torpor: bool = False
    awake: bool = True
    sleep_timer: int = 0
    cause_of_death: str = ""
    freq_bass: float = 0.0
    freq_mid: float = 0.0
    freq_treble: float = 0.0
    vol_bass: float = 0.0
    vol_mid: float = 0.0
    vol_treble: float = 0.0
    action_counts: Dict[int, float] = field(default_factory=dict)
    last_regs: List[float] = field(default_factory=lambda: [0.0] * NUM_REGS)

    @property
    def genome(self) -> list:
        return self.vm.genome


def _tdist(a: int, b: int, size: int) -> int:
    return abs(a - b)


def _wx(x: int) -> int:
    return max(0, min(WIDTH - 1, x)) if WIDTH else 0


def _wy(y: int) -> int:
    return max(0, min(HEIGHT - 1, y)) if HEIGHT else 0


_next_id = 0


def _next_oid() -> int:
    global _next_id
    _next_id += 1
    return _next_id


def random_genome(length: int = 30) -> List[int]:
    return [random.randint(0, 255) for _ in range(length)]


class World:
    def __init__(self):
        self.organisms: List[Organism] = []
        self.resources: Dict[Tuple[int, int], str] = {}
        self.tick = 0
        self.next_id = 0
        self.shift_timer = random.randint(*ENV_SHIFT_INTERVAL)
        self.events: List[str] = []
        self.extinction_log: List[str] = []
        self.pop_history: List[int] = []
        self.fitness_history: List[float] = []
        self._extinction_log_initialized = False
        self.max_gen_ever = 0
        self.max_age_ever = 0
        self.min_pop_ever = INITIAL_ORGANISMS
        self.migration_timer = random.randint(*MIGRATION_INTERVAL)
        self.fossil_lineages: List[Tuple[int, ...]] = []
        self.all_genomes_seen: Set[Tuple[int, ...]] = set()
        self.recorded_fossils: Set[Tuple[int, ...]] = set()
        self.fossil_count = 0
        self.daylight_phase = 0.0
        self.daylight = 1.0
        self.moisture = 0.5
        self.pressure = 0.7
        self.temp_diurnal = 0.0
        self.season = "summer"
        self.season_timer = random.randint(*SEASON_LENGTH)
        self.diseased: Set[int] = set()
        self.mixer = AudioMixer()
        self.predator_memory: Dict[int, Set[int]] = {}
        self.soil_fertility: Dict[Tuple[int, int], float] = {}
        self.immune: Set[int] = set()
        self.nests: Dict[Tuple[int, int], int] = {}
        self.territory: Dict[Tuple[int, int], Dict[int, int]] = {}
        self.traces: Dict[Tuple[int, int], float] = {}
        self.signal_buffers: Dict[Tuple[int, int], List[float]] = {}
        self.death_stats: Dict[str, int] = {
            "starvation": 0, "predation": 0, "fighting": 0,
            "old_age": 0, "disease": 0, "unknown": 0,
        }
        self.seed_used = SEED

        for _ in range(INITIAL_RESOURCES):
            rtype = random.choices(RESOURCE_KEYS, weights=[t["weight"] for t in RESOURCE_TYPES.values()])[0]
            self._add_resource(
                random.randint(0, WIDTH - 1), random.randint(0, HEIGHT - 1), rtype
            )

        for _ in range(INITIAL_ORGANISMS):
            self._spawn(
                random.randint(0, WIDTH - 1),
                random.randint(0, HEIGHT - 1),
                random_genome(random.randint(18, 60)),
            )

    SOUND_TONES = {
        "mass_death":   (200, 0.15),
        "critical":     (100, 0.25),
        "bottleneck":   (150, 0.20),
        "migration":    (600, 0.10),
        "fossil":       (700, 0.08),
        "season":       (400, 0.10),
        "recovery":     (750, 0.10),
        "env_shift":    (280, 0.15),
        "stress":       (180, 0.20),
        "new_gen":      (880, 0.06),
    }

    def _sound(self, event_type: str):
        if not SOUND_ENABLED:
            return
        tone = self.SOUND_TONES.get(event_type)
        if tone:
            freq, dur = tone
            self.mixer.set_stinger(event_type, freq, SOUND_VOLUME, dur)

    def _log_extinction(self, etype: str, pop: int):
        orgs = self.organisms
        n = len(orgs)
        if n > 0:
            avg_e = sum(o.energy for o in orgs) / n
            avg_f = sum(o.fat for o in orgs) / n
            sp = len({tuple(o.genome) for o in orgs})
            gc: Dict[Tuple[int, ...], int] = {}
            for o in orgs:
                k = tuple(o.genome)
                gc[k] = gc.get(k, 0) + 1
            dom = max(gc, key=gc.get) if gc else ()
            dom_g = " ".join(str(v) for v in dom[:6])
        else:
            avg_e = avg_f = sp = 0
            dom_g = ""
        event = {
            "seed": self.seed_used, "ts": time.strftime("%Y%m%d_%H%M%S"),
            "tick": self.tick, "event": etype, "pop": pop,
            "max_gen": self.max_gen_ever, "season": self.season,
            "avg_energy": round(avg_e, 2), "avg_fat": round(avg_f, 3),
            "species": sp, "fossils": self.fossil_count,
            "diseased": len(self.diseased), "resources": len(self.resources),
            "nests": len(self.nests), "territory": len(self.territory),
            "dominant_genome": dom_g,
        }
        line = json.dumps(event)
        if not self._extinction_log_initialized:
            try:
                with open(EXTINCTION_LOG_FILE, "rb") as f:
                    f.seek(-1, 2)
                    last = f.read(1)
                if last != b"]":
                    raise FileNotFoundError
                with open(EXTINCTION_LOG_FILE, "r+b") as f:
                    f.seek(-1, 2)
                    f.write(b",\n" + line.encode() + b"\n]")
            except (FileNotFoundError, OSError):
                with open(EXTINCTION_LOG_FILE, "w") as f:
                    f.write("[\n" + line + "\n]")
            self._extinction_log_initialized = True
        else:
            with open(EXTINCTION_LOG_FILE, "r+b") as f:
                f.seek(-1, 2)
                f.write(b",\n" + line.encode() + b"\n]")

    def _add_resource(self, x: int, y: int, rtype: str = "food"):
        self.resources[(x, y)] = rtype

    def _spawn(
        self, x: int, y: int, genome: list, energy: float = 3.0, generation: int = 0
    ) -> Organism:
        org = Organism(
            x=_wx(x), y=_wy(y),
            vm=GenomeVM(genome=genome),
            energy=energy,
            age=0,
            generation=generation,
            id=_next_oid(),
            freq_bass=random.uniform(30, 100),
            freq_mid=random.uniform(150, 600),
            freq_treble=random.uniform(700, 4000),
            vol_bass=0.0,
            vol_mid=0.0,
            vol_treble=0.0,
        )
        self.organisms.append(org)
        self.all_genomes_seen.add(tuple(genome))
        return org

    def _temperature_at(self, y: int) -> float:
        lat = 1.0 - 2.0 * y / (HEIGHT - 1)
        base = 1.0 - abs(lat)
        if self.season == "summer":
            base = min(1.0, base + 0.15)
        else:
            base = max(0.0, base - 0.15)
        diurnal = math.cos(2 * math.pi * (self.daylight_phase - 0.25))
        return max(0.0, min(1.0, base + diurnal * 0.15))

    def _daylight_at(self, x: int, phase: float) -> float:
        terminator = phase * WIDTH
        dist_to_term = ((x - terminator) % WIDTH)
        if dist_to_term < WIDTH / 2:
            return 0.5 + 0.5 * math.cos(2 * math.pi * dist_to_term / WIDTH)
        else:
            night_dist = dist_to_term - WIDTH / 2
            return 0.5 - 0.5 * math.cos(2 * math.pi * night_dist / WIDTH)

    def compute_senses(self, org: Organism) -> Dict[int, float]:
        senses = {}
        best_food = None
        best_fd = 999
        for (fx, fy) in self.resources:
            d = _tdist(org.x, fx, WIDTH) + _tdist(org.y, fy, HEIGHT)
            if d < best_fd:
                best_fd = d
                best_food = (fx, fy)
        if best_food:
            senses[Sensor.FOOD_X] = float(best_food[0])
            senses[Sensor.FOOD_Y] = float(best_food[1])
            senses[Sensor.FOOD_DIST] = float(best_fd)
        else:
            senses[Sensor.FOOD_DIST] = 99.0

        best_org = None
        best_od = 999
        for other in self.organisms:
            if other is org or other.id in self._dead_this_tick:
                continue
            d = _tdist(org.x, other.x, WIDTH) + _tdist(org.y, other.y, HEIGHT)
            if d < best_od:
                best_od = d
                best_org = other
        if best_org:
            senses[Sensor.ORG_X] = float(best_org.x)
            senses[Sensor.ORG_Y] = float(best_org.y)
            senses[Sensor.ORG_DIST] = float(best_od)
        else:
            senses[Sensor.ORG_DIST] = 99.0

        phase = (self.tick % DAY_LENGTH) / DAY_LENGTH
        daylight = self._daylight_at(org.x, phase)
        senses[Sensor.DAYLIGHT] = daylight
        senses[Sensor.TEMP] = self._temperature_at(org.y)
        senses[Sensor.MOISTURE] = 0.3 + 0.7 * (1.0 - daylight)
        senses[Sensor.ENERGY] = org.energy
        senses[Sensor.AGE] = float(org.age)
        senses[Sensor.POP_DENSITY] = len(self.organisms) / (WIDTH * HEIGHT)
        senses[Sensor.PRESSURE] = self.pressure
        senses[Sensor.SEASON] = 1.0 if self.season == "summer" else 0.0
        senses[Sensor.LATITUDE] = 1.0 - 2.0 * org.y / (HEIGHT - 1)
        senses[Sensor.FAT] = org.fat
        senses[Sensor.HEALTH] = min(1.0, org.energy / 5.0)
        senses[Sensor.ACT_EAT] = org.action_counts.get(Action.EAT, 0)
        senses[Sensor.ACT_ATTACK] = org.action_counts.get(Action.ATTACK, 0)

        # Trace sensors — strength and direction toward nearest trace
        best_trace = 0.0
        ox, oy = org.x, org.y
        best_tx, best_ty = ox, oy
        for dx in range(-3, 4):
            for dy in range(-3, 4):
                tx, ty = ox + dx, oy + dy
                if 0 <= tx < WIDTH and 0 <= ty < HEIGHT:
                    s = self.traces.get((tx, ty), 0.0)
                    if s > best_trace:
                        best_trace = s
                        best_tx, best_ty = tx, ty
        senses[Sensor.TRACE] = min(1.0, best_trace / 5.0)
        if best_trace > 0:
            senses[Sensor.TRACE_DX] = max(-1.0, min(1.0, (best_tx - ox) / 3.0))
            senses[Sensor.TRACE_DY] = max(-1.0, min(1.0, (best_ty - oy) / 3.0))
        else:
            senses[Sensor.TRACE_DX] = 0.0
            senses[Sensor.TRACE_DY] = 0.0
        # Signal sensor — incoming EMIT data
        sig = self.signal_buffers.get((org.x, org.y), 0.0)
        senses[Sensor.SIGNAL] = min(1.0, sig / 10.0)
        return senses

    def apply_action(self, org: Organism, action_id: int, _arg: int):
        dissipation = 1.0 + max(0, org.energy - 5.0) * 0.15

        if action_id == Action.MOVE_N:
            org.y = _wy(org.y - 1)
            org.energy -= 0.02 * dissipation
        elif action_id == Action.MOVE_S:
            org.y = _wy(org.y + 1)
            org.energy -= 0.02 * dissipation
        elif action_id == Action.MOVE_E:
            org.x = _wx(org.x + 1)
            org.energy -= 0.02 * dissipation
        elif action_id == Action.MOVE_W:
            org.x = _wx(org.x - 1)
            org.energy -= 0.02 * dissipation

        elif action_id == Action.MOVE_TOWARD_FOOD:
            best = None
            best_d = 999
            for (fx, fy) in self.resources:
                d = _tdist(org.x, fx, WIDTH) + _tdist(org.y, fy, HEIGHT)
                if d < best_d:
                    best_d = d
                    best = (fx, fy)
            if best:
                fx, fy = best
                dx = 1 if fx > org.x else -1 if fx < org.x else 0
                dy = 1 if fy > org.y else -1 if fy < org.y else 0
                org.x = _wx(org.x + dx)
                org.y = _wy(org.y + dy)
                if dx or dy:
                    org.energy -= 0.02 * dissipation

        elif action_id == Action.MOVE_AWAY_ORG:
            for other in self.organisms:
                if other is org:
                    continue
                d = _tdist(org.x, other.x, WIDTH) + _tdist(org.y, other.y, HEIGHT)
                if d <= 3:
                    fx = org.x + (org.x - other.x)
                    fy = org.y + (org.y - other.y)
                    org.x = _wx(fx)
                    org.y = _wy(fy)
                    org.energy -= 0.02 * dissipation
                    break

        elif action_id == Action.EAT:
            pos = (org.x, org.y)
            if pos in self.resources:
                rtype = self.resources.pop(pos)
                val = RESOURCE_TYPES[rtype]["value"]
                org.energy += val
            else:
                for other in self.organisms:
                    if other is org or other.energy <= 0:
                        continue
                    if other.x == org.x and other.y == org.y and other.energy < org.energy * 0.6:
                        gain = other.energy * 0.4
                        org.energy += gain
                        other.energy -= gain
                        if other.energy <= 0:
                            other.cause_of_death = "predation"
                        break

        elif action_id == Action.ATTACK:
            for other in self.organisms:
                if other is org:
                    continue
                if other.x == org.x and other.y == org.y:
                    power = max(0.1, org.energy) * 0.3
                    other.energy -= power
                    if other.energy <= 0:
                        other.cause_of_death = "predation"
                        org.energy += other.energy * 0.5
                    break

        elif action_id == Action.REPRODUCE:
            if org.energy < ENERGY_COST_PER_CHILD * 0.6:
                return
            neighbors = []
            for dx, dy in [(0, 1), (0, -1), (1, 0), (-1, 0)]:
                nx, ny = _wx(org.x + dx), _wy(org.y + dy)
                if not any(o.x == nx and o.y == ny for o in self.organisms):
                    neighbors.append((nx, ny))
            if not neighbors:
                return
            nx, ny = random.choice(neighbors)
            child_g = org.generation + 1

            # Look for a mate on adjacent cells
            mate = None
            for dx, dy in [(0, 1), (0, -1), (1, 0), (-1, 0)]:
                mx, my = _wx(org.x + dx), _wy(org.y + dy)
                for o in self.organisms:
                    if o is not org and o.x == mx and o.y == my and o.energy >= 2.0:
                        mate = o
                        break
                if mate:
                    break

            if mate:
                cost = ENERGY_COST_PER_CHILD * 0.6
                org.energy -= cost
                mate.energy -= cost * 0.5
                child_vm = org.vm.crossover(mate.vm)
                child_vm = child_vm.clone_mutated()
                self._spawn(nx, ny, child_vm.genome,
                            energy=2.5, generation=child_g)
            else:
                cost = ENERGY_COST_PER_CHILD * 0.6
                org.energy -= cost
                child_vm = org.vm.clone_mutated()
                self._spawn(nx, ny, child_vm.genome,
                            energy=2.0, generation=child_g)
                if child_g > self.max_gen_ever:
                    self.max_gen_ever = child_g
                    self.events.append(f"Gen {child_g} reached!")
                    self._sound("new_gen")

        elif action_id == Action.REST:
            org.energy += 0.3 / dissipation

        elif action_id == Action.SOUND:
            vol = min(1.0, max(0.0, org.energy / 10.0))
            s = _arg % 256
            org.freq_bass = 40 + (s % 60)
            org.freq_mid = 180 + ((s * 7) % 400)
            org.freq_treble = 800 + ((s * 31) % 3200)
            org.vol_bass = vol * 0.4
            org.vol_mid = vol * 0.3
            org.vol_treble = vol * 0.2
            self.mixer.set_org(org.id,
                               org.freq_bass, org.vol_bass,
                               org.freq_mid, org.vol_mid,
                               org.freq_treble, org.vol_treble)

        elif action_id == Action.EMIT:
            reg_idx = _arg % NUM_REGS
            val = org.last_regs[reg_idx]
            for dx in range(-2, 3):
                for dy in range(-2, 3):
                    tx, ty = _wx(org.x + dx), _wy(org.y + dy)
                    self.signal_buffers[(tx, ty)] = self.signal_buffers.get((tx, ty), 0.0) + abs(val)

    async def step(self):
        self.tick += 1
        self.shift_timer -= 1

        pre_pop = len(self.organisms)

        if self.shift_timer <= 0:
            self._shift_environment()
            self.shift_timer = random.randint(*ENV_SHIFT_INTERVAL)

        # Daylight phase (0-1 rotation)
        self.daylight_phase = ((self.tick % DAY_LENGTH) / DAY_LENGTH)
        self.daylight = 0.5 + 0.5 * math.cos(2 * math.pi * self.daylight_phase)
        diurnal_temp = math.cos(2 * math.pi * (self.daylight_phase - 0.25))
        self.temp_diurnal = diurnal_temp
        pressure_diurnal = math.cos(2 * math.pi * self.daylight_phase) * 0.08
        self.pressure += random.uniform(-0.03, 0.03) + pressure_diurnal * 0.04
        self.pressure += (0.5 - self.pressure) * 0.015
        self.pressure = max(0.1, min(1.0, self.pressure))
        if self.daylight < 0.3:
            self.moisture = min(1.0, self.moisture + 0.006)
        elif self.daylight > 0.6:
            self.moisture = max(0.0, self.moisture - 0.003 * (1.0 + diurnal_temp * 0.5))
        if self.pressure < 0.35 and self.daylight < 0.4:
            self.moisture = min(1.0, self.moisture + 0.15)

        # Seasons
        self.season_timer -= 1
        if self.season_timer <= 0:
            self.season = "winter" if self.season == "summer" else "summer"
            self.season_timer = random.randint(*SEASON_LENGTH)
            self.events.append(f"Season: {'summer' if self.season == 'summer' else 'winter'}")
            self._sound("season")

        # Environmental ambient audio — low freq events → bass, high freq events → treble
        p_dev = abs(self.pressure - 0.5) * 2.0
        self.mixer.set_ambient("pressure", 40 + p_dev * 40, p_dev * 0.12, 0, 0, 0, 0)
        self.mixer.set_ambient("moisture", 90 + self.moisture * 80, self.moisture * 0.08,
                               0, 0, 0, 0)
        temp_warm = max(0.0, self.temp_diurnal)
        temp_cold = max(0.0, -self.temp_diurnal)
        self.mixer.set_ambient("temperature", 50, temp_cold * 0.06,
                               350 + temp_warm * 250, temp_warm * 0.07,
                               0, 0)
        dl_treb = 1000 + self.daylight * 2000
        self.mixer.set_ambient("daylight", 0, 0, 0, 0,
                               dl_treb, self.daylight * 0.06)
        season_bass = 35 if self.season == "summer" else 50
        self.mixer.set_ambient("season", season_bass, 0.04, 0, 0, 0, 0)

        # Carrying capacity
        carry_cap = WIDTH * HEIGHT * 0.4
        n_carry = len(self.organisms)
        carry_pressure = 1.0 + (n_carry / carry_cap) ** 2

        # Corpse decomposition
        for (cx, cy), rtype in list(self.resources.items()):
            if rtype == "corpse":
                for org in self.organisms:
                    if _tdist(org.x, cx, WIDTH) + _tdist(org.y, cy, HEIGHT) <= 1:
                        org.energy += 0.03

        # Build dead set for sensor computation
        self._dead_this_tick: Set[int] = set()

        # Run all organism VMs concurrently
        async def _run_org(org) -> Tuple[Organism, List[Tuple[int, int]], Dict[int, float]]:
            senses = self.compute_senses(org)
            budget = min(org.energy * 0.4, 8.0)
            actions = org.vm.execute(budget, senses, tick=self.tick)
            org.last_regs = org.vm.regs.copy()
            return (org, actions, senses)

        results = await asyncio.gather(*[_run_org(o) for o in self.organisms])

        # Apply actions and organism-level systems
        dead: Set[int] = set()

        for org, actions, senses in results:
            if org.id in dead:
                continue

            org.age += 1

            # Environmental sensitivity — extreme heat/cold/pressure drain energy
            temp = senses.get(Sensor.TEMP, 0.5)
            press = senses.get(Sensor.PRESSURE, 0.5)
            temp_stress = abs(temp - 0.5) * 2.0
            press_stress = abs(press - 0.5) * 2.0
            env_drain = (temp_stress * 0.04 + press_stress * 0.02)
            if env_drain > 0:
                org.energy -= env_drain

            # Life stage
            if org.age < 3:
                org.energy -= 0.05
            is_elder = org.age > 30

            # Torpor
            if org.torpor:
                if org.energy >= 3.0:
                    org.torpor = False
                    torpid = False
                else:
                    torpid = True
            elif org.energy <= 0.5:
                org.torpor = True
                torpid = True
            else:
                torpid = False

            # Sleep cycles
            asleep = False
            if not torpid:
                if org.awake:
                    if org.energy < 0.8 and org.age > 5:
                        org.awake = False
                        org.sleep_timer = random.randint(3, 8)
                else:
                    org.sleep_timer -= 1
                    org.energy += 0.08
                    if org.sleep_timer <= 0 or org.energy > 3.0:
                        org.awake = True
            asleep = not org.awake and not torpid

            # Apply VM actions
            if not torpid and not asleep:
                moved_n = 0
                for act_id, arg, _intensity in actions:
                    is_move = act_id in (Action.MOVE_N, Action.MOVE_S, Action.MOVE_E,
                                         Action.MOVE_W, Action.MOVE_TOWARD_FOOD, Action.MOVE_AWAY_ORG)
                    if is_move and MAX_MOVEMENT_SPEED > 0 and moved_n >= MAX_MOVEMENT_SPEED:
                        continue
                    self.apply_action(org, act_id, arg)
                    if is_move:
                        moved_n += 1

            # Decay and update action counts for trait evaluation
            decayed = {}
            for aid, c in org.action_counts.items():
                c2 = c * 0.85
                if c2 > 0.1:
                    decayed[aid] = c2
            org.action_counts = decayed
            if not torpid and not asleep:
                for act_id, *__ in actions:
                    org.action_counts[act_id] = org.action_counts.get(act_id, 0) + 1.0

            # Genome-determined drift when VM produces no movement actions
            if not torpid and not asleep:
                moved = any(
                    a[0] in (Action.MOVE_N, Action.MOVE_S, Action.MOVE_E,
                             Action.MOVE_W, Action.MOVE_TOWARD_FOOD, Action.MOVE_AWAY_ORG)
                    for a in actions
                )
                if not moved:
                    dx = (org.genome[0] % 3) - 1 if org.genome else 0
                    dy = (org.genome[min(1, len(org.genome)-1)] % 3) - 1 if len(org.genome) > 1 else 0
                    org.x = _wx(org.x + dx)
                    org.y = _wy(org.y + dy)

            # Nest building
            if not torpid and not asleep and org.energy > 3.0:
                npos = (org.x, org.y)
                if npos not in self.nests and random.random() < 0.05:
                    self.nests[npos] = 0
                    org.energy -= 0.5
                elif npos in self.nests:
                    self.nests[npos] += 1

            nest_bonus = 0.0
            if (org.x, org.y) in self.nests:
                nest_bonus = 0.3
                self.nests[(org.x, org.y)] += 1
                if random.random() < 0.005:
                    del self.nests[(org.x, org.y)]
            if len(self.nests) > WIDTH * HEIGHT * 0.08:
                for _ in range(5):
                    k = random.choice(list(self.nests.keys()))
                    del self.nests[k]

            # Territory marking
            hue = (org.id + org.generation) % 6
            tpos = (org.x, org.y)
            if tpos not in self.territory:
                self.territory[tpos] = {}
            self.territory[tpos][hue] = self.territory[tpos].get(hue, 0) + 1

            # Territory cost
            if tpos in self.territory:
                foreign = sum(c for h, c in self.territory[tpos].items() if h != hue)
                if foreign > 0:
                    org.energy -= 0.02 * min(3, foreign)

            # Fight overlapping organisms
            if not torpid:
                for other in self.organisms:
                    if other is org or other.id in dead:
                        continue
                    if other.x == org.x and other.y == org.y:
                        a = min(3, org.vm.instr_count // 20)
                        b = min(3, other.vm.instr_count // 20)
                        org_power = max(0, org.energy) * (a + 1) / 4
                        other_power = max(0, other.energy) * (b + 1) / 4
                        total = org_power + other_power
                        if total > 0 and random.random() < org_power / total:
                            other.cause_of_death = "fighting"
                            org.energy += other.energy * 0.25
                            dead.add(other.id)
                        else:
                            org.cause_of_death = "fighting"
                            other.energy += org.energy * 0.25
                            dead.add(org.id)
                            break

            if org.id in dead:
                continue

            # Weak auto-eat at 30% efficiency (bootstrap only)
            if not torpid:
                pos = (org.x, org.y)
                if pos in self.resources:
                    rtype = self.resources.pop(pos)
                    age_bonus = min(org.age, 20) / 20.0 * 0.2
                    org.energy += RESOURCE_TYPES[rtype]["value"] * (0.3 + age_bonus)

            # Metabolic cost
            base_cost = SUMMER_BASE_COST if self.season == "summer" else WINTER_BASE_COST
            mult = 0.1 if torpid else 1.0
            if asleep:
                mult = 0.0
            org.energy -= (base_cost + 0.02) * mult

            # HGT: horizontal genome transfer
            if not torpid and random.random() < 0.02:
                for other in self.organisms:
                    if other is org or other.id in dead:
                        continue
                    if _tdist(other.x, org.x, WIDTH) <= 1 and _tdist(other.y, org.y, HEIGHT) <= 1:
                        i = random.randrange(min(len(org.genome), len(other.genome)))
                        org.vm.genome[i], other.vm.genome[i] = other.vm.genome[i], org.vm.genome[i]
                        break

            # Random spontaneous mutation
            if random.random() < 0.004:
                i = random.randrange(len(org.genome))
                org.vm.genome[i] = max(0, min(255, org.vm.genome[i] + random.choice([-1, 1])))

            # Disease
            if org.id in self.diseased:
                for other in self.organisms:
                    if other.id in dead or other.id in self.diseased or other.id in self.immune:
                        continue
                    if _tdist(other.x, org.x, WIDTH) <= 2 and _tdist(other.y, org.y, HEIGHT) <= 2:
                        if random.random() < 0.20:
                            self.diseased.add(other.id)
                drain = 0.12
                org.energy -= drain
                if random.random() < 0.05:
                    self.diseased.discard(org.id)
                    self.immune.add(org.id)

            if org.id in dead:
                continue

            if org.id in self.immune and random.random() < 0.005:
                self.immune.discard(org.id)

            # Fat metabolism
            fat_cap = 2.0
            if org.energy > 2.0 and org.fat < fat_cap:
                store = min(org.energy - 2.0, fat_cap - org.fat, 0.5)
                org.fat += store
                org.energy -= store
            elif org.energy < 0.5 and org.fat > 0:
                draw = min(org.fat, 1.0)
                org.fat -= draw
                org.energy += draw * 0.7

            # Starvation
            if org.energy <= 0:
                org.cause_of_death = "disease" if org.id in self.diseased else "starvation"
                dead.add(org.id)
                continue

            # Old age
            max_age = 120 + (org.vm.instr_count % 100)
            if org.age > max_age:
                org.cause_of_death = "old_age"
                dead.add(org.id)
                continue

            # Reproduction
            density = sum(
                1 for o in self.organisms
                if o is not org and o.id not in dead
                and _tdist(o.x, org.x, WIDTH) + _tdist(o.y, org.y, HEIGHT) <= 3
            )
            density_penalty = 1.0 + max(0, density - 3) * 0.15
            repro_thresh = REPRODUCTION_THRESHOLD * density_penalty * carry_pressure
            if is_elder:
                repro_thresh *= 0.8
            if org.energy >= repro_thresh:
                vm_repro = any(act_id == Action.REPRODUCE for act_id, *__ in actions)
                # Weak auto-reproduce fallback when far above threshold
                if not vm_repro and org.energy >= repro_thresh * 2.0 and random.random() < 0.10:
                    self.apply_action(org, Action.REPRODUCE, 0)

            # Mutation pressure from reproduction overhead
            if org.energy >= repro_thresh * 1.5 and random.random() < 0.03:
                if len(org.vm.genome) < 600 and random.random() < 0.5:
                    idx = (random.randrange(0, len(org.vm.genome) + 1) // 3) * 3
                    org.vm.genome[idx:idx] = [random.randint(0, Op.TOTAL - 1),
                                              random.randint(0, 255), random.randint(0, 255)]

        # Remove dead
        pop_before = len(self.organisms)
        dead_list = []
        kept = []
        for o in self.organisms:
            if o.id in dead:
                dead_list.append(o)
                if o.cause_of_death:
                    self.death_stats[o.cause_of_death] = self.death_stats.get(o.cause_of_death, 0) + 1
                else:
                    self.death_stats["unknown"] = self.death_stats.get("unknown", 0) + 1
            else:
                kept.append(o)
        self.organisms = kept
        pop_after = len(self.organisms)
        died = pop_before - pop_after
        self.diseased &= {o.id for o in self.organisms}

        # Trace deposit and decay
        for o in self.organisms:
            self.traces[(o.x, o.y)] = self.traces.get((o.x, o.y), 0.0) + 0.5
        decayed = {}
        for pos, strength in self.traces.items():
            s = strength * 0.95
            if s > 0.01:
                decayed[pos] = s
        self.traces = decayed

        # Signal buffer decay
        dec_sig = {}
        for pos, val in self.signal_buffers.items():
            v = val * 0.85
            if v > 0.01:
                dec_sig[pos] = v
        self.signal_buffers = dec_sig

        # Corpse resources
        for o in dead_list:
            self.mixer.remove_org(o.id)
            if random.random() < 0.5 and (o.x, o.y) not in self.resources:
                rtype = "corpse" if o.energy > 1.0 else "food"
                self._add_resource(o.x, o.y, rtype)

        # Track max age
        for o in self.organisms:
            if o.age > self.max_age_ever:
                self.max_age_ever = o.age

        # Fossil record
        current_genomes = {tuple(o.genome) for o in self.organisms}
        newly_extinct = self.all_genomes_seen - current_genomes - self.recorded_fossils
        if newly_extinct:
            for g in newly_extinct:
                self.recorded_fossils.add(g)
                self.fossil_lineages.append(g)
            self.fossil_count += len(newly_extinct)
            self.events.append(f"{len(newly_extinct)} lineage(s) fossilized (total: {self.fossil_count})")
            self._sound("fossil")

        # History
        self.pop_history.append(pop_after)
        if len(self.pop_history) > 60:
            self.pop_history = self.pop_history[-60:]

        # Population events
        if died > 5 and pop_after > 0:
            self.events.append(f"{died} died in a single tick")
            self._sound("mass_death")
        if pre_pop < 10 and pop_after > pre_pop and pop_after >= 10:
            self.events.append(f"Population recovered to {pop_after}")
            self._sound("recovery")
        if pop_after < self.min_pop_ever:
            self.min_pop_ever = pop_after
            if pop_after <= 3:
                self.extinction_log.append(f"CRITICAL: pop={pop_after} at T={self.tick}")
                self.events.append(f"Only {pop_after} organisms remain!")
                self._sound("critical")
                self._log_extinction("CRITICAL", pop_after)
            elif pop_after <= 10:
                self.extinction_log.append(f"Bottleneck: pop={pop_after} at T={self.tick}")
                self.events.append(f"Population bottleneck: {pop_after}")
                self._sound("bottleneck")
                self._log_extinction("BOTTLENECK", pop_after)

        # Regenerate resources
        regen = SUMMER_REGEN if self.season == "summer" else WINTER_REGEN
        for _ in range(regen):
            if len(self.resources) < WIDTH * HEIGHT * 0.25:
                x, y = random.randint(0, WIDTH - 1), random.randint(0, HEIGHT - 1)
                if (x, y) not in self.resources:
                    rtype = random.choices(RESOURCE_KEYS, weights=[t["weight"] for t in RESOURCE_TYPES.values()])[0]
                    self._add_resource(x, y, rtype)

        # System energy cap
        total_energy = sum(o.energy + o.fat for o in self.organisms)
        if total_energy > MAX_SYSTEM_ENERGY:
            ratio = MAX_SYSTEM_ENERGY / total_energy
            for o in self.organisms:
                o.energy *= ratio
                o.fat *= ratio

        # Fitness history
        if self.organisms:
            self.fitness_history.append(sum(o.energy for o in self.organisms) / len(self.organisms))
        if len(self.fitness_history) > 80:
            self.fitness_history = self.fitness_history[-80:]

        # Territory decay
        decayed = []
        for tpos, hues in self.territory.items():
            for h in list(hues.keys()):
                hues[h] -= 1
                if hues[h] <= 0:
                    del hues[h]
            if not hues:
                decayed.append(tpos)
        for tpos in decayed:
            del self.territory[tpos]
        if len(self.territory) > WIDTH * HEIGHT * 0.15:
            for _ in range(10):
                k = random.choice(list(self.territory.keys()))
                del self.territory[k]

        # Migration
        self.migration_timer -= 1
        if self.migration_timer <= 0:
            batch = random.randint(*MIGRATION_BATCH)
            for _ in range(batch):
                x = random.randint(0, WIDTH - 1)
                y = random.randint(0, HEIGHT - 1)
                self._spawn(x, y, random_genome(random.randint(18, 60)), 4.0)
            self.events.append(f"{batch} invaders arrived from beyond")
            self._sound("migration")
            self.migration_timer = random.randint(*MIGRATION_INTERVAL)

    def _shift_environment(self):
        remove_n = int(len(self.resources) * random.uniform(0.15, 0.4))
        self.events.append(f"Environment shift: -{remove_n} resources, +clusters")
        self._sound("env_shift")
        if self.tick > 100 and random.random() < 0.25:
            self.events.append("Environmental stress event")
            self._sound("stress")
        if remove_n > 0 and self.resources:
            for pos in random.sample(list(self.resources.keys()), min(remove_n, len(self.resources))):
                del self.resources[pos]
        clusters = random.randint(2, 5)
        for _ in range(clusters):
            cx = random.randint(6, WIDTH - 6)
            cy = random.randint(3, HEIGHT - 3)
            for _ in range(random.randint(5, 15)):
                x = _wx(cx + random.randint(-4, 4))
                y = _wy(cy + random.randint(-2, 2))
                if (x, y) not in self.resources:
                    rtype = random.choices(RESOURCE_KEYS, weights=[t["weight"] for t in RESOURCE_TYPES.values()])[0]
                    self._add_resource(x, y, rtype)
        if random.random() < 0.3:
            for org in self.organisms:
                org.energy -= random.uniform(0.5, 1.5)

    def render(self) -> str:
        phase = (self.tick % DAY_LENGTH) / DAY_LENGTH

        grid = [[" " for _ in range(WIDTH)] for _ in range(HEIGHT)]

        # Resources
        for (x, y), rtype in self.resources.items():
            occupied = any(o.x == x and o.y == y for o in self.organisms)
            if not occupied:
                grid[y][x] = RESOURCE_TYPES[rtype]["symbol"]

        # Nests under unoccupied cells
        for (x, y), strength in self.nests.items():
            occupied = any(o.x == x and o.y == y for o in self.organisms)
            if not occupied and grid[y][x] == " ":
                nest_age = min(4, strength // 10)
                nest_sym = ["░", "▒", "▓", "█", "█"][nest_age]
                grid[y][x] = f"\033[33m{nest_sym}{RESET}"

        # Territory as background on unoccupied
        TERR_COLORS = ["\033[41m", "\033[42m", "\033[44m", "\033[45m", "\033[46m"]
        for (x, y), hues in self.territory.items():
            occupied = any(o.x == x and o.y == y for o in self.organisms)
            if not occupied and grid[y][x] == " ":
                dom_hue = max(hues, key=hues.get)
                tc = TERR_COLORS[dom_hue % len(TERR_COLORS)]
                grid[y][x] = f"{tc} {RESET}"

        # Organisms
        sentinel = max(self.organisms, key=lambda o: o.generation) if self.organisms else None
        sentinel_id = sentinel.id if sentinel else -1

        for org in self.organisms:
            hue = (org.id + org.generation) % len(GLYPH_SET)
            glyph = GLYPH_SET[hue]
            color = COLORS[hue % len(COLORS)]
            dl = self._daylight_at(org.x, phase)

            if org.id == sentinel_id and org.generation > 0:
                grid[org.y][org.x] = f"{BOLD}\033[47m\033[30m{glyph}{RESET}"
            elif org.id in self.diseased:
                grid[org.y][org.x] = f"{BOLD}\033[41m{color}{glyph}{RESET}"
            elif dl < 0.2:
                grid[org.y][org.x] = f"{DIM}{color}{glyph}{RESET}"
            elif dl < 0.5:
                grid[org.y][org.x] = f"{color}{glyph}{RESET}"
            elif org.torpor:
                grid[org.y][org.x] = f"{DIM}{color}{glyph}{RESET}"
            elif org.energy > 7:
                grid[org.y][org.x] = f"{BOLD}{color}{glyph}{RESET}"
            elif org.energy > 3:
                grid[org.y][org.x] = f"{color}{glyph}{RESET}"
            else:
                grid[org.y][org.x] = f"{DIM}{color}{glyph}{RESET}"

        def _hl(dl: float) -> str:
            if dl > 0.6:
                return f"{BOLD}\033[103m\033[30m═{RESET}"
            elif dl > 0.3:
                return f"{BOLD}\033[43m\033[30m═{RESET}"
            elif dl > 0.15:
                return f"{BOLD}\033[45m\033[37m═{RESET}"
            return f"{BOLD}\033[44m\033[37m═{RESET}"

        top_bar = f"{BOLD}╔{RESET}" + "".join(_hl(self._daylight_at(x, phase)) for x in range(WIDTH)) + f"{BOLD}╗{RESET}"
        bot_bar = f"{BOLD}╚{RESET}" + "".join(_hl(self._daylight_at(x, phase)) for x in range(WIDTH)) + f"{BOLD}╝{RESET}"

        lines = [top_bar]
        for y, row in enumerate(grid):
            lines.append(f"{BOLD}║{RESET}{''.join(row)}{BOLD}║{RESET}")
        lines.append(bot_bar)

        # Status
        n = len(self.organisms)
        if n > 0:
            avg_e = sum(o.energy for o in self.organisms) / n
            max_g = self.max_gen_ever
            sp = len({tuple(o.genome) for o in self.organisms})
            avg_age = sum(o.age for o in self.organisms) / n
            shannon = 0.0
            if sp > 0:
                gc: Dict[str, int] = {}
                for o in self.organisms:
                    k = str(o.genome[:6])
                    gc[k] = gc.get(k, 0) + 1
                shannon = -sum((c/n) * math.log(c/n) for c in gc.values())
            lines.append(
                f"  Pop:{n:4d}  ⚡:{avg_e:.1f}  Gen:{max_g:3d}  Age:{avg_age:.1f}  "
                f"Sp:{sp:2d}  H\u2019:{shannon:.2f}  Fos:{self.fossil_count:4d}  "
                f"Res:{len(self.resources):3d}  "
                f"{'☀ sum' if self.season == 'summer' else '\u2744 win'}  "
                f"T:{self.tick}  tot:{sum(o.energy+o.fat for o in self.organisms):.0f}/{MAX_SYSTEM_ENERGY:.0f}"
            )

            # Day-night bar (fit within typical terminal width)
            n = min(WIDTH, 60)
            step = max(1, WIDTH // n)
            dn_bar = ""
            for x in range(0, WIDTH, step):
                dl = self._daylight_at(x, phase)
                if dl > 0.6:
                    dn_bar += "░"
                elif dl > 0.3:
                    dn_bar += "▒"
                else:
                    dn_bar += "█"
            lines.append(f"  [dn] {dn_bar}")

            # Population sparkline
            if self.pop_history:
                max_pop = max(self.pop_history)
                min_pop = min(self.pop_history)
                span = max_pop - min_pop if max_pop > min_pop else 1
                SPARK = "▁▂▃▄▅▆▇█"
                sparkline = ""
                window = self.pop_history[-min(60, len(self.pop_history)):]
                for p in window:
                    idx = int((p - min_pop) / span * (len(SPARK) - 1))
                    sparkline += SPARK[idx]
                lines.append(f"  └{'─' * min(50, len(window))}  {sparkline}")

            # Fitness sparkline
            if len(self.fitness_history) > 5:
                feats = self.fitness_history[-40:]
                mn, mx = min(feats), max(feats)
                rng = mx - mn if mx > mn else 1
                fitline = ""
                for f in feats:
                    idx = int((f - mn) / rng * 7)
                    fitline += "▁▂▃▄▅▆▇█"[idx]
                lines.append(f"  └fit              {fitline}")

            # Dominant genome fingerprint
            gc: Dict[str, int] = {}
            for o in self.organisms:
                k = str(o.genome[:6])
                gc[k] = gc.get(k, 0) + 1
            dom = max(gc, key=gc.get) if gc else "?"
            pct = gc.get(dom, 0) / n * 100 if n else 0
            lines.append(f"  dom VM[:6]: {dom} ({pct:.0f}%)")

            # Sentinel
            if sentinel and sentinel.generation > 0:
                OP_NAMES = ["NOP","MOV","ADD","SUB","MUL","DIV","JMP","JZ","JG","JL",
                            "SENSE","ACT","PUSH","POP","CALL","RET","HALT","RAND","ENERGY",
                            "MOD","CMP","AND","OR","XOR","NOT","IND","MIN","MAX","ABS","NEG","DUP","JNE",
                            "SWAP","GEN","PICK","DPTH","PC","SETPC","SQRT","EXP","TICK","DROP","OVER",
                            "SHL","SHR","BIT"]
                g = sentinel.genome
                decoded = []
                for i in range(0, min(len(g), 54), 3):
                    if i + 2 >= len(g):
                        break
                    op = g[i] % Op.TOTAL
                    a1 = g[i+1] % 256
                    a2 = g[i+2] % 256
                    decoded.append(f"{OP_NAMES[op]}({a1},{a2})")
                prog = " ".join(decoded[:18])
                lines.append(
                    f"  sentinel: gen={sentinel.generation} age={sentinel.age} "
                    f"⚡={sentinel.energy:.1f} len={len(sentinel.genome)}"
                )
                lines.append(f"  └vm: {prog}")
                if len(decoded) > 18:
                    lines.append(f"  └...({len(decoded)-18} more)")

        # Events
        self.events = self.events[-3:]
        for ev in self.events:
            lines.append(f"  {ev}")

        return "\n".join(lines)


async def main():
    global SOUND_ENABLED, SOUND_VOLUME, TICK_RATE, EXTINCTION_LOG_FILE, SEED, WIDTH, HEIGHT, MAX_SYSTEM_ENERGY
    parser = argparse.ArgumentParser(description="VM-genome evolutionary ecosystem")
    parser.add_argument("--volume", type=float, default=SOUND_VOLUME)
    parser.add_argument("--no-sound", action="store_true")
    parser.add_argument("--tick-rate", type=float, default=TICK_RATE)
    parser.add_argument("--log", default=EXTINCTION_LOG_FILE)
    parser.add_argument("--seed", type=int, default=SEED)
    parser.add_argument("--width", type=int, default=WIDTH)
    parser.add_argument("--height", type=int, default=HEIGHT)
    parser.add_argument("--max-energy", type=float, default=None)
    parser.add_argument("--max-movement-speed", type=int, default=0,
                    help="max movement actions per tick per organism (0=unlimited)")
    parser.add_argument("--continuous", action="store_true")
    args = parser.parse_args()
    if args.no_sound:
        SOUND_ENABLED = False
    SOUND_VOLUME = max(0.0, min(1.0, args.volume))
    TICK_RATE = max(0.01, args.tick_rate)
    EXTINCTION_LOG_FILE = args.log
    SEED = args.seed
    WIDTH = max(16, min(256, args.width))
    HEIGHT = max(8, min(64, args.height))
    if args.max_energy is not None:
        MAX_SYSTEM_ENERGY = max(10, args.max_energy)
    if args.max_movement_speed > 0:
        MAX_MOVEMENT_SPEED = args.max_movement_speed
    continuous = args.continuous

    run_count = 0
    while True:
        run_count += 1
        if run_count > 1:
            SEED = random.randint(0, 2**31)
            _name_cache.clear()
        random.seed(SEED)

        world = World()
        world.seed_used = SEED
        await world.mixer.start()
        print("\033c", end="")
        interrupted = False
        try:
            while world.organisms:
                sys.stdout.write("\033[H")
                for line in world.render().split("\n"):
                    sys.stdout.write(line + "\033[K\n")
                sys.stdout.write("\033[J")
                sys.stdout.flush()
                await world.step()
                await asyncio.sleep(TICK_RATE)
        except KeyboardInterrupt:
            interrupted = True

        await world.mixer.stop()
        total_extinct = 0
        print(f"\n{'═' * 40}")
        if not world.organisms:
            print(f"  Extinction at T={world.tick} (run {run_count})")
            world._log_extinction("TOTAL_EXTINCTION", 0)
        elif interrupted:
            print(f"  Halted after {world.tick} ticks (run {run_count})")
            world._log_extinction("SNAPSHOT", len(world.organisms))
        print(f"  Pop: {len(world.organisms)}  "
              f"Generations: {world.max_gen_ever}  "
              f"Max age: {world.max_age_ever}  "
              f"Min pop: {world.min_pop_ever}")
        ds = world.death_stats
        total_d = sum(ds.values()) or 1
        causes = "  ".join(f"{k}:{v} ({v*100//total_d}%)" for k, v in ds.items())
        print(f"  Deaths: {causes}")
        print(f"  Species now: {len({tuple(o.genome) for o in world.organisms})}  "
              f"Infected: {len(world.diseased)}  "
              f"Fossil lineages: {world.fossil_count}")
        if world.extinction_log:
            print(f"  Extinction events ({len(world.extinction_log)} total):")
            for entry in world.extinction_log[-5:]:
                print(f"  {entry}")

        # Best VM
        if world.organisms:
            best = max(world.organisms, key=lambda o: o.generation)
            if best.generation > 0:
                OP_NAMES = ["NOP","MOV","ADD","SUB","MUL","DIV","JMP","JZ","JG","JL",
                            "SENSE","ACT","PUSH","POP","CALL","RET","HALT","RAND","ENERGY",
                            "MOD","CMP","AND","OR","XOR","NOT","IND","MIN","MAX","ABS","NEG","DUP","JNE",
                            "SWAP","GEN","PICK","DPTH","PC","SETPC","SQRT","EXP","TICK","DROP","OVER",
                            "SHL","SHR","BIT"]
                g = best.genome
                decoded = []
                for i in range(0, min(len(g), 54), 3):
                    if i + 2 >= len(g):
                        break
                    op = g[i] % Op.TOTAL
                    a1 = g[i+1] % 256
                    a2 = g[i+2] % 256
                    decoded.append(f"{OP_NAMES[op]}({a1},{a2})")
                prog = " ".join(decoded[:18])
                print(f"  Best VM: gen={best.generation} age={best.age:.0f} "
                      f"⚡={best.energy:.1f} len={len(best.genome)}")
                print(f"  └vm: {prog}")
                if len(decoded) > 18:
                    print(f"  └...({len(decoded)-18} more)")

        if not continuous or interrupted:
            print(f"\n  Extinction log written to {EXTINCTION_LOG_FILE}")
            print(f"{'═' * 40}")
            break

        print(f"  Run {run_count} done — restarting with seed {SEED}")
        print(f"{'═' * 40}")
        time.sleep(0.5)
        print("\033c", end="")


if __name__ == "__main__":
    asyncio.run(main())
