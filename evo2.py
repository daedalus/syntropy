#!/usr/bin/env python3
"""
evo2 - VM-genome evolutionary ecosystem prototype.

Each organism carries a Turing-complete stack VM as its genome.
Energy = gas. Traits are emergent, not hardcoded.
Async organisms. Per-organism audio channels.
Day/night toroidal world with diurnal environment.
"""

import asyncio
import random
import math
import struct
import threading
import subprocess
import time
import sys
from typing import List, Dict, Tuple, Optional, Set
from dataclasses import dataclass, field

random.seed(42)

# === CONFIG ===
WIDTH = 72
HEIGHT = 26
INITIAL_ORGS = 36
INITIAL_RESOURCES = 70
RESOURCE_REGEN = 4
TICK_RATE = 0.10
DAY_LENGTH = 100
SOUND_ENABLED = True
SOUND_VOLUME = 0.3

# === TOROIDAL HELPERS ===
def _wx(x: int) -> int:
    return x % WIDTH if WIDTH else 0

def _wy(y: int) -> int:
    return y % HEIGHT if HEIGHT else 0

def _tdist(a: int, b: int, size: int) -> int:
    d = abs(a - b)
    return min(d, size - d)

# === VM DEFINITION ===
# Instruction format: 3 ints per instruction [opcode, arg1, arg2]
# Genome = flat list of ints, PC advances by 3

NUM_REGS = 4
NUM_SENSORS = 12
NUM_ACTIONS = 12

class Op:
    NOP, MOV, ADD, SUB, MUL, DIV = range(6)
    JMP, JZ, JG, JL = range(6, 10)
    SENSE, ACT = 10, 11
    PUSH, POP, CALL, RET = 12, 13, 14, 15
    HALT, RAND, ENERGY = 16, 17, 18
    TOTAL = 19

OP_NAMES = {v: k for k, v in vars(Op).items() if isinstance(v, int) and v < Op.TOTAL}

class Sensor:
    FOOD_X, FOOD_Y, FOOD_DIST = 0, 1, 2
    ORG_X, ORG_Y, ORG_DIST = 3, 4, 5
    TEMP, DAYLIGHT, MOISTURE = 6, 7, 8
    ENERGY, AGE, POP_DENSITY = 9, 10, 11

class Action:
    MOVE_N, MOVE_S, MOVE_E, MOVE_W = 0, 1, 2, 3
    MOVE_TOWARD_FOOD, MOVE_AWAY_ORG = 4, 5
    EAT, ATTACK, REPRODUCE = 6, 7, 8
    REST, SOUND = 9, 10
    TOTAL = 11

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

    def execute(self, budget: float, senses: Dict[int, float]) -> List[Tuple[int, int]]:
        self.regs = [0.0] * NUM_REGS
        self.pc = 0
        self.stack = []
        self.running = True
        self.instr_count = 0
        used = 0.0
        actions = []
        glen = len(self.genome)

        while self.running and used < budget and self.instr_count < 300:
            self.instr_count += 1
            used += 0.015

            if self.pc < 0 or self.pc >= glen - 2:
                break
            op = int(self.genome[self.pc]) % Op.TOTAL
            a1 = int(self.genome[self.pc + 1]) % 256
            a2 = int(self.genome[self.pc + 2]) % 256
            self.pc += 3
            if self.pc >= glen:
                self.pc = 0  # wrap program

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
                self._sr(a2 % NUM_REGS, senses.get(sid, 0.0))
            elif op == Op.ACT:
                act_id = a1 % NUM_ACTIONS
                actions.append((act_id, a2))
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

        return actions

# === ORGANISM ===
@dataclass
class Organism:
    id: int
    x: int
    y: int
    energy: float
    vm: GenomeVM
    age: int = 0
    generation: int = 0
    alive: bool = True
    fat: float = 0.0
    cause_of_death: str = ""
    freq_offset: float = 0.0  # Hz offset for audio channel
    sound_active: bool = False
    sound_vol: float = 0.0

_next_id = 0

def _next_oid() -> int:
    global _next_id
    _next_id += 1
    return _next_id

def random_genome(length: int = 30) -> List[int]:
    return [random.randint(0, 255) for _ in range(length)]

# === AUDIO MIXER ===
class AudioMixer:
    """Per-organism audio mixer running in background thread."""

    def __init__(self):
        self.orgs: Dict[int, Tuple[float, float, float]] = {}
        self.lock = threading.Lock()
        self.running = True
        self.thread = threading.Thread(target=self._mix_loop, daemon=True)
        self.thread.start()

    def set_org(self, oid: int, freq: float, vol: float):
        with self.lock:
            if vol > 0.01:
                self.orgs[oid] = (freq, vol, time.time())
            else:
                self.orgs.pop(oid, None)

    def remove_org(self, oid: int):
        with self.lock:
            self.orgs.pop(oid, None)

    def stop(self):
        self.running = False

    def _mix_loop(self):
        sr = 22050
        buf_size = 512
        vol = SOUND_VOLUME * 0.15
        while self.running:
            with self.lock:
                orgs = dict(self.orgs)
            n = len(orgs)
            if n == 0:
                time.sleep(0.05)
                continue
            data = bytearray()
            for _ in range(buf_size):
                t = time.time()
                left = 0.0
                right = 0.0
                for oid, (freq, v, _ts) in orgs.items():
                    phase = 2 * math.pi * freq * t
                    spread = (oid % 100) / 100.0
                    l_gain = math.cos(spread * math.pi * 0.5)
                    r_gain = math.sin(spread * math.pi * 0.5)
                    s = math.sin(phase) * v * vol
                    left += s * l_gain
                    right += s * r_gain
                peak = max(abs(left), abs(right), 0.001)
                scale = 16384 / peak
                data.extend(struct.pack("<h", int(left * scale)))
                data.extend(struct.pack("<h", int(right * scale)))
            try:
                proc = subprocess.Popen(
                    ["aplay", "-q", "-f", "S16_LE", "-r", str(sr), "-c", "2"],
                    stdin=subprocess.PIPE, stderr=subprocess.DEVNULL
                )
                proc.communicate(input=bytes(data), timeout=1)
            except Exception:
                pass

# === WORLD ===
GLYPH_SET = "●◆▲■★✦⬟⬢◈◎◉◆"
COLORS = [
    "\033[31m", "\033[33m", "\033[32m", "\033[36m",
    "\033[34m", "\033[35m", "\033[91m", "\033[95m",
    "\033[92m", "\033[93m", "\033[94m",
]
RESET = "\033[0m"
BOLD = "\033[1m"
DIM = "\033[2m"

RESOURCE_TYPES = {
    "food":  {"value": 1.2, "symbol": "·", "weight": 0.65},
    "bounty": {"value": 4.0, "symbol": "★", "weight": 0.20},
    "corpse": {"value": 1.8, "symbol": "✿", "weight": 0.15},
}
RESOURCE_KEYS = list(RESOURCE_TYPES.keys())


@dataclass
class World:
    organisms: List[Organism] = field(default_factory=list)
    resources: Dict[Tuple[int, int], str] = field(default_factory=dict)
    tick: int = 0
    pop_history: List[int] = field(default_factory=list)
    max_gen_ever: int = 0
    max_age_ever: int = 0
    mixer: AudioMixer = field(default_factory=AudioMixer)

    def __post_init__(self):
        for _ in range(INITIAL_RESOURCES):
            rtype = random.choices(RESOURCE_KEYS,
                                   weights=[t["weight"] for t in RESOURCE_TYPES.values()])[0]
            self._add_resource(random.randint(0, WIDTH - 1),
                               random.randint(0, HEIGHT - 1), rtype)
        for _ in range(INITIAL_ORGS):
            self._spawn(random.randint(0, WIDTH - 1),
                        random.randint(0, HEIGHT - 1),
                        random_genome(random.randint(15, 60)))

    def _add_resource(self, x: int, y: int, rtype: str = "food"):
        self.resources[(x, y)] = rtype

    def _spawn(self, x: int, y: int, genome: List[int],
               energy: float = 3.0, generation: int = 0) -> Organism:
        org = Organism(
            id=_next_oid(),
            x=_wx(x), y=_wy(y),
            energy=energy,
            vm=GenomeVM(genome=genome),
            generation=generation,
            freq_offset=random.uniform(-100, 100),
        )
        self.organisms.append(org)
        return org

    def _temperature_at(self, y: int, daylight: float) -> float:
        lat = 1.0 - 2.0 * y / (HEIGHT - 1)
        base = 1.0 - abs(lat)
        diurnal = daylight * 0.3
        return max(0.0, min(1.0, base + diurnal))

    def compute_senses(self, org: Organism) -> Dict[int, float]:
        senses = {}
        # Food distance
        best_food = None
        best_fd = 999
        for (fx, fy), rtype in self.resources.items():
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

        # Nearest other organism
        best_org = None
        best_od = 999
        for other in self.organisms:
            if other is org or not other.alive:
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

        # Environment
        phase = (self.tick % DAY_LENGTH) / DAY_LENGTH
        daylight = self._daylight_at(org.x, phase)
        senses[Sensor.DAYLIGHT] = daylight
        senses[Sensor.TEMP] = self._temperature_at(org.y, daylight)
        senses[Sensor.MOISTURE] = self._moisture_at(org.x, phase)
        senses[Sensor.ENERGY] = org.energy
        senses[Sensor.AGE] = float(org.age)
        senses[Sensor.POP_DENSITY] = len(self.organisms) / (WIDTH * HEIGHT)

        return senses

    def _daylight_at(self, x: int, phase: float) -> float:
        terminator = phase * WIDTH
        dist_to_term = ((x - terminator) % WIDTH)
        if dist_to_term < WIDTH / 2:
            return 0.5 + 0.5 * math.cos(2 * math.pi * dist_to_term / WIDTH)
        else:
            night_dist = dist_to_term - WIDTH / 2
            return 0.5 - 0.5 * math.cos(2 * math.pi * night_dist / WIDTH)

    def _moisture_at(self, x: int, phase: float) -> float:
        dl = self._daylight_at(x, phase)
        return 0.3 + 0.7 * (1.0 - dl)

    def apply_action(self, org: Organism, action_id: int, arg: int):
        speed = 1 + (org.vm.instr_count % 4)

        if action_id == Action.MOVE_N:
            org.y = _wy(org.y - speed)
        elif action_id == Action.MOVE_S:
            org.y = _wy(org.y + speed)
        elif action_id == Action.MOVE_E:
            org.x = _wx(org.x + speed)
        elif action_id == Action.MOVE_W:
            org.x = _wx(org.x - speed)

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
                steps = min(speed, best_d)
                for _ in range(steps):
                    dx = 1 if fx > org.x else -1 if fx < org.x else 0
                    dy = 1 if fy > org.y else -1 if fy < org.y else 0
                    org.x = _wx(org.x + dx)
                    org.y = _wy(org.y + dy)

        elif action_id == Action.MOVE_AWAY_ORG:
            for other in self.organisms:
                if other is org or not other.alive:
                    continue
                d = _tdist(org.x, other.x, WIDTH) + _tdist(org.y, other.y, HEIGHT)
                if d <= 3:
                    fx = org.x + (org.x - other.x)
                    fy = org.y + (org.y - other.y)
                    org.x = _wx(fx)
                    org.y = _wy(fy)
                    break

        elif action_id == Action.EAT:
            pos = (org.x, org.y)
            if pos in self.resources:
                rtype = self.resources.pop(pos)
                val = RESOURCE_TYPES[rtype]["value"]
                org.energy += val

        elif action_id == Action.ATTACK:
            for other in self.organisms:
                if other is org or not other.alive:
                    continue
                if other.x == org.x and other.y == org.y:
                    power = max(0.1, org.energy) * 0.3
                    other.energy -= power
                    if other.energy <= 0:
                        other.alive = False
                        other.cause_of_death = "predation"
                        org.energy += other.energy * 0.5
                    break

        elif action_id == Action.REPRODUCE:
            if org.energy < 3.0:
                return
            neighbors = []
            for dx, dy in [(0, 1), (0, -1), (1, 0), (-1, 0)]:
                nx, ny = _wx(org.x + dx), _wy(org.y + dy)
                if not any(o.x == nx and o.y == ny for o in self.organisms if o.alive):
                    neighbors.append((nx, ny))
            if neighbors:
                nx, ny = random.choice(neighbors)
                child_vm = org.vm.clone_mutated()
                child = self._spawn(nx, ny, child_vm.genome,
                                    energy=org.energy * 0.4,
                                    generation=org.generation + 1)
                org.energy *= 0.6
                if child.generation > self.max_gen_ever:
                    self.max_gen_ever = child.generation

        elif action_id == Action.REST:
            org.energy += 0.1

        elif action_id == Action.SOUND:
            freq = 100 + (arg % 200)
            vol = min(1.0, max(0.0, org.energy / 10.0))
            org.freq_offset = float(freq)
            org.sound_vol = vol
            self.mixer.set_org(org.id, 80 + org.freq_offset, vol * 0.3)

    async def step(self):
        self.tick += 1

        # Run all organism VMs concurrently
        async def _run_org(org):
            if not org.alive:
                return (org, [])
            senses = self.compute_senses(org)
            budget = min(org.energy * 0.4, 8.0)
            actions = org.vm.execute(budget, senses)
            return (org, actions)

        results = await asyncio.gather(*[_run_org(o) for o in self.organisms])

        # Apply actions
        for org, actions in results:
            if not org.alive:
                continue
            org.age += 1
            for act_id, arg in actions:
                self.apply_action(org, act_id, arg)

            # Metabolic cost
            base_cost = 0.04
            org.energy -= base_cost

            # Starvation check
            if org.energy <= 0:
                org.alive = False
                org.cause_of_death = "starvation"
                continue

            # Old age
            max_age = 120 + (org.vm.instr_count % 100)
            if org.age > max_age:
                org.alive = False
                org.cause_of_death = "old_age"
                continue

        # Remove dead, spawn resources
        dead = [o for o in self.organisms if not o.alive]
        self.organisms = [o for o in self.organisms if o.alive]
        for o in dead:
            self.mixer.remove_org(o.id)
            if random.random() < 0.5:
                self._add_resource(o.x, o.y, "corpse")

        # Regenerate resources
        for _ in range(RESOURCE_REGEN):
            if len(self.resources) < WIDTH * HEIGHT * 0.2:
                x, y = random.randint(0, WIDTH - 1), random.randint(0, HEIGHT - 1)
                if (x, y) not in self.resources:
                    rtype = random.choices(RESOURCE_KEYS,
                                           weights=[t["weight"] for t in RESOURCE_TYPES.values()])[0]
                    self._add_resource(x, y, rtype)

        self.pop_history.append(len(self.organisms))
        if len(self.pop_history) > 60:
            self.pop_history = self.pop_history[-60:]

        # Migration (invasion from outside)
        if self.tick % 70 == 0 and len(self.organisms) < WIDTH * HEIGHT * 0.3:
            for _ in range(random.randint(1, 4)):
                self._spawn(random.randint(0, WIDTH - 1),
                            random.randint(0, HEIGHT - 1),
                            random_genome(random.randint(15, 60)), 3.0)

    def render(self) -> str:
        grid = [[" " for _ in range(WIDTH)] for _ in range(HEIGHT)]
        phase = (self.tick % DAY_LENGTH) / DAY_LENGTH

        # Render resources
        for (x, y), rtype in self.resources.items():
            occupied = any(o.x == x and o.y == y for o in self.organisms if o.alive)
            if not occupied:
                grid[y][x] = RESOURCE_TYPES[rtype]["symbol"]

        # Render organisms
        for org in self.organisms:
            if not org.alive:
                continue
            glyph = GLYPH_SET[org.id % len(GLYPH_SET)]
            color = COLORS[org.id % len(COLORS)]
            dl = self._daylight_at(org.x, phase)
            if dl < 0.2:
                grid[org.y][org.x] = f"{DIM}\033[44m{color}{glyph}{RESET}"
            elif dl < 0.4:
                grid[org.y][org.x] = f"{DIM}{color}{glyph}{RESET}"
            elif org.energy > 5:
                grid[org.y][org.x] = f"{BOLD}{color}{glyph}{RESET}"
            else:
                grid[org.y][org.x] = f"{color}{glyph}{RESET}"

        # Build borders with temperature colors
        TEMP_COLORS = ["\033[34m", "\033[32m", "\033[33m", "\033[31m"]
        lines = [f"{BOLD}╔{'═' * WIDTH}╗{RESET}"]

        for y, row in enumerate(grid):
            temp_idx = min(3, int(abs(1.0 - 2.0 * y / (HEIGHT - 1)) * 4))
            tc = TEMP_COLORS[temp_idx]
            lines.append(f"{BOLD}{tc}║{RESET}{''.join(row)}{BOLD}{tc}║{RESET}")

        lines.append(f"{BOLD}╚{'═' * WIDTH}╝{RESET}")

        # Status bar
        n = len(self.organisms)
        avg_e = sum(o.energy for o in self.organisms) / max(1, n)
        max_g = self.max_gen_ever
        sp = len({tuple(o.vm.genome) for o in self.organisms})
        phase_str = f"☀{'🌙' if phase < 0.25 else '☀' if phase < 0.5 else '🌙' if phase < 0.75 else '☀'}"

        # Day/night meter
        term = int(phase * WIDTH)
        dn_bar = ""
        for x in range(WIDTH):
            dl = self._daylight_at(x, phase)
            if dl > 0.6:
                dn_bar += "░"
            elif dl > 0.3:
                dn_bar += "▒"
            else:
                dn_bar += "█"

        lines.append(
            f"  Pop:{n:3d}  ⚡:{avg_e:.1f}  Gen:{max_g:3d}  Sp:{sp:2d}  "
            f"Res:{len(self.resources):3d}  T:{self.tick:4d}"
        )
        lines.append(f"  [day-night terminator]  {dn_bar}")
        lines.append(f"  phase:{phase:.3f}  n_orgs:{n}")

        # Dominant genome
        if self.organisms:
            gc: Dict[str, int] = {}
            for o in self.organisms:
                k = str(o.vm.genome[:6])  # first 2 instructions as fingerprint
                gc[k] = gc.get(k, 0) + 1
            dom = max(gc, key=gc.get) if gc else "?"
            pct = gc.get(dom, 0) / n * 100 if n else 0
            lines.append(f"  dom genome[:6]: {dom} ({pct:.0f}%)")

        return "\n".join(lines)


# === MAIN ===
async def main():
    world = World()
    print("\033c", end="")
    try:
        while world.organisms:
            sys.stdout.write("\033[H")
            sys.stdout.write(world.render())
            sys.stdout.flush()
            await world.step()
            await asyncio.sleep(TICK_RATE)
    except KeyboardInterrupt:
        pass
    finally:
        world.mixer.stop()
    print(f"\nExtinction at T={world.tick}. Generations: {world.max_gen_ever}")


if __name__ == "__main__":
    asyncio.run(main())
