#!/usr/bin/env python3
"""evo - A self-evolving ecosystem in your terminal.

Organisms with tiny genomes navigate a shifting 2D world, consuming resources,
reproducing with mutation, and adapting as niches open and collapse.

No stable equilibrium. Ever-evolving.
"""

import argparse
import random
import time
import struct
import math
import json
import threading
import subprocess
from dataclasses import dataclass, field
from typing import Dict, List, Tuple, Optional, Set


SEED = 42

EXTINCTION_LOG_FILE = "extinction.json"
SOUND_ENABLED = True
SOUND_VOLUME = 0.3

# --- CONFIG ---
WIDTH = 64
HEIGHT = 26
INITIAL_ORGANISMS = 40
INITIAL_RESOURCES = 80
RESOURCE_REGEN = 4
SEASON_LENGTH = (70, 110)
SUMMER_REGEN = 6
WINTER_REGEN = 2
SUMMER_BASE_COST = 0.03
WINTER_BASE_COST = 0.08
REPRODUCTION_THRESHOLD = 5.0
SEXUAL_THRESHOLD = 3.0
ENERGY_COST_PER_CHILD = 2.5
BASE_MUTATION_RATE = 0.10
ENV_SHIFT_INTERVAL = (30, 60)
MIGRATION_INTERVAL = (80, 150)
MIGRATION_BATCH = (3, 8)
TICK_RATE = 0.06

# Two resource types: common food, rare bounty
RESOURCE_TYPES = {
    "food":  {"value": 1.5, "symbol": "·", "weight": 0.65},
    "bounty":{"value": 5.5, "symbol": "★", "weight": 0.20},
    "corpse":{"value": 2.0, "symbol": "✿", "weight": 0.15},
}
RESOURCE_KEYS = list(RESOURCE_TYPES.keys())

# Mutation rate per gene value (0=low mut, 5=high mut)
MUT_RATES = [0.08, 0.12, 0.16, 0.20, 0.24, 0.30]

GENES = [
    ("speed", 0, 3),
    ("sense", 0, 4),
    ("aggression", 0, 3),
    ("metabolic", 0, 3),
    ("wander", 0, 3),
    ("hue", 0, 5),
    ("mut_rate", 0, 5),
    ("thermal", 0, 4),
    ("diet", 0, 2),
    ("toxin", 0, 3),
    ("lumen", 0, 3),
]

GLYPH_SET = "●◆▲■★✦⬟⬢◈◎◉"
COLORS = [
    "\033[31m",
    "\033[33m",
    "\033[32m",
    "\033[36m",
    "\033[34m",
    "\033[35m",
    "\033[91m",
    "\033[95m",
    "\033[92m",
    "\033[93m",
    "\033[94m",
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

def _species_name(genome: tuple) -> str:
    if genome in _name_cache:
        return _name_cache[genome]
    g = genome
    idx1 = (g[0] * 7 + g[2] * 5 + g[4] * 3 + g[6]) % len(GENUS_ROOTS)
    idx2 = (g[1] * 11 + g[3] * 7 + g[5] * 5 + g[7] * 3 + g[8] * 2 + g[9] * 2 + g[10] * 3) % len(SPECIES_ROOTS)
    variant = (g[0] * 13 + g[3] * 17 + g[6] * 19 + g[10] * 23) % 100
    name = f"{GENUS_ROOTS[idx1]} {SPECIES_ROOTS[idx2]} v.{variant}"
    _name_cache[genome] = name
    return name


@dataclass
class Organism:
    x: int
    y: int
    genome: list
    energy: float
    age: int
    generation: int
    id: int
    fat: float = 0.0
    torpor: bool = False
    memory: List[Tuple[int, int]] = field(default_factory=list)
    pupils: Dict[int, int] = field(default_factory=dict)
    awake: bool = True
    sleep_timer: int = 0
    parasite: bool = False
    host_id: Optional[int] = None
    cause_of_death: str = ""


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
        self.genes_found: List[Set[int]] = [set(range(g[1], g[2]+1)) for g in GENES]
        self.genes_lost: List[str] = []
        self.migration_timer = random.randint(*MIGRATION_INTERVAL)
        self.fossil_lineages: List[Tuple[int, ...]] = []
        self.all_genomes_seen: Set[Tuple[int, ...]] = set()
        self.recorded_fossils: Set[Tuple[int, ...]] = set()
        self.fossil_count = 0
        self.season = "summer"
        self.season_timer = random.randint(*SEASON_LENGTH)
        self.diseased: Set[int] = set()
        self.sonify_counter = 0
        self.predator_memory: Dict[int, Set[int]] = {}  # predator_hue → set of toxic_hues
        self.soil_fertility: Dict[Tuple[int, int], float] = {}  # grazed spots → fertility bonus
        self.immune: Set[int] = set()  # organisms immune to disease
        self.nests: Dict[Tuple[int, int], int] = {}  # position → strength (tick count)
        self.territory: Dict[Tuple[int, int], Dict[int, int]] = {}  # pos → {hue: count}
        self.parasites: Dict[int, int] = {}  # parasite_id → host_id
        self.death_stats: Dict[str, int] = {
            "starvation": 0, "predation": 0, "fighting": 0,
            "old_age": 0, "disease": 0, "parasitism": 0, "unknown": 0,
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
                [random.randint(g[1], g[2]) for g in GENES],
            )

    SOUND_TONES = {
        "mass_death":   (200, 0.15),
        "critical":     (100, 0.25),
        "bottleneck":   (150, 0.20),
        "radiation":    (500, 0.10),
        "disease":      (320, 0.12),
        "epidemic":     (250, 0.20),
        "migration":    (600, 0.10),
        "fossil":       (700, 0.08),
        "season":       (400, 0.10),
        "recovery":     (750, 0.10),
        "env_shift":    (280, 0.15),
        "stress":       (180, 0.20),
        "new_gen":      (880, 0.06),
        "gene_extinct": (130, 0.12),
        "punish":       (550, 0.08),
        "mimicry":      (660, 0.06),
        "transposon":   (920, 0.05),
        "territory":    (300, 0.10),
    }

    @staticmethod
    def _play_tone(freq: float, duration: float, volume: float = 0.3):
        sr = 22050
        n = int(sr * duration)
        decay = 1.0
        data = bytearray()
        for i in range(n):
            t = i / sr
            env = 1.0 - i / n if duration > 0.05 else 1.0
            s = int(volume * env * 32767 * math.sin(2 * math.pi * freq * t))
            data.extend(struct.pack("<h", s))
        try:
            proc = subprocess.Popen(
                ["aplay", "-q", "-f", "S16_LE", "-r", str(sr), "-c", "1"],
                stdin=subprocess.PIPE, stderr=subprocess.DEVNULL
            )
            proc.communicate(input=bytes(data), timeout=1)
        except Exception:
            pass

    def _sound(self, event_type: str):
        if not SOUND_ENABLED:
            return
        tone = self.SOUND_TONES.get(event_type)
        if tone:
            freq, dur = tone
            threading.Thread(
                target=World._play_tone, args=(freq, dur, SOUND_VOLUME), daemon=True
            ).start()

    @staticmethod
    def _play_stereo(l_freq: float, r_freq: float, duration: float, volume: float = 0.3):
        sr = 22050
        n = int(sr * duration)
        data = bytearray()
        for i in range(n):
            t = i / sr
            env = 1.0 - i / n if duration > 0.05 else 1.0
            ls = int(volume * env * 32767 * math.sin(2 * math.pi * l_freq * t))
            rs = int(volume * env * 32767 * math.sin(2 * math.pi * r_freq * t))
            data.extend(struct.pack("<h", ls))
            data.extend(struct.pack("<h", rs))
        try:
            proc = subprocess.Popen(
                ["aplay", "-q", "-f", "S16_LE", "-r", str(sr), "-c", "2"],
                stdin=subprocess.PIPE, stderr=subprocess.DEVNULL
            )
            proc.communicate(input=bytes(data), timeout=1)
        except Exception:
            pass

    def _sonify_tick(self):
        if not SOUND_ENABLED:
            return
        n = len(self.organisms)
        cap = WIDTH * HEIGHT
        pop_density = n / cap if cap else 0
        if n > 0:
            avg_gen = sum(o.generation for o in self.organisms) / n
            gen_ratio = avg_gen / max(1, self.max_gen_ever)
            n_c = sum(1 for o in self.organisms if o.genome[8] == 1)
            n_h = sum(1 for o in self.organisms if o.genome[8] == 0)
            pr_ratio = n_c / max(1, n_h)
            gc = {}
            for o in self.organisms:
                k = tuple(o.genome)
                gc[k] = gc.get(k, 0) + 1
            shannon = -sum((c/n) * math.log(c/n) for c in gc.values()) if gc else 0.0
        else:
            gen_ratio = 0.0
            pr_ratio = 0.0
            shannon = 0.0
        n_parasites = len(self.parasites)
        para_ratio = n_parasites / max(1, n)
        # Left channel: pop density (bass) + predator-prey ratio (mid)
        l_freq = 80 + pop_density * 300
        l_mid = 200 + pr_ratio * 200
        # Right channel: gen ratio (mid) + Shannon diversity (treble)
        r_freq = 120 + gen_ratio * 300
        r_treb = 400 + shannon * 150
        # Parasite density adds a very low undertone
        para_sub = 40 + para_ratio * 60
        threading.Thread(
            target=World._play_multitrack,
            args=(l_freq, l_mid, r_freq, r_treb, para_sub, 0.05, SOUND_VOLUME * 0.4),
            daemon=True
        ).start()

    @staticmethod
    def _play_multitrack(l1: float, l2: float, r1: float, r2: float, sub: float,
                         duration: float, volume: float = 0.3):
        sr = 22050
        n = int(sr * duration)
        data = bytearray()
        for i in range(n):
            t = i / sr
            env = 1.0 - i / n
            ls = int(volume * env * 16384 * (
                0.5 * math.sin(2 * math.pi * l1 * t) +
                0.3 * math.sin(2 * math.pi * l2 * t) +
                0.2 * math.sin(2 * math.pi * sub * t)
            ))
            rs = int(volume * env * 16384 * (
                0.5 * math.sin(2 * math.pi * r1 * t) +
                0.3 * math.sin(2 * math.pi * r2 * t) +
                0.2 * math.sin(2 * math.pi * sub * t)
            ))
            data.extend(struct.pack("<h", ls))
            data.extend(struct.pack("<h", rs))
        try:
            proc = subprocess.Popen(
                ["aplay", "-q", "-f", "S16_LE", "-r", str(sr), "-c", "2"],
                stdin=subprocess.PIPE, stderr=subprocess.DEVNULL
            )
            proc.communicate(input=bytes(data), timeout=1)
        except Exception:
            pass

    def _log_extinction(self, etype: str, pop: int):
        orgs = self.organisms
        n = len(orgs)
        if n > 0:
            avg_e = sum(o.energy for o in orgs) / n
            avg_f = sum(o.fat for o in orgs) / n
            avg_g = [sum(o.genome[i] for o in orgs) / n for i in range(len(GENES))]
            n_herb = sum(1 for o in orgs if o.genome[8] == 0)
            n_carn = sum(1 for o in orgs if o.genome[8] == 1)
            n_omni = sum(1 for o in orgs if o.genome[8] == 2)
            sp = len({tuple(o.genome) for o in orgs})
            gc: Dict[Tuple[int, ...], int] = {}
            for o in orgs:
                k = tuple(o.genome)
                gc[k] = gc.get(k, 0) + 1
            dom = max(gc, key=gc.get) if gc else ()
            dom_g = " ".join(str(v) for v in dom)
        else:
            avg_e = avg_f = n_herb = n_carn = n_omni = sp = 0
            avg_g = [0.0] * len(GENES)
            dom_g = ""
        event = {
            "seed": self.seed_used, "ts": time.strftime("%Y%m%d_%H%M%S"),
            "tick": self.tick, "event": etype, "pop": pop,
            "max_gen": self.max_gen_ever, "season": self.season,
            "avg_energy": round(avg_e, 2), "avg_fat": round(avg_f, 3),
            "avg_spd": round(avg_g[0], 2), "avg_sen": round(avg_g[1], 2),
            "avg_agg": round(avg_g[2], 2), "avg_met": round(avg_g[3], 2),
            "avg_wnd": round(avg_g[4], 2), "avg_hue": round(avg_g[5], 2),
            "avg_mut": round(avg_g[6], 2), "avg_tmp": round(avg_g[7], 2),
            "avg_diet": round(avg_g[8], 2), "avg_tox": round(avg_g[9], 2),
            "avg_lumen": round(avg_g[10], 2) if len(avg_g) > 10 else 0.0,
            "n_herb": n_herb, "n_carn": n_carn, "n_omni": n_omni,
            "min_pop": self.min_pop_ever, "max_age": self.max_age_ever,
            "species": sp, "fossils": self.fossil_count,
            "diseased": len(self.diseased), "resources": len(self.resources),
            "nests": len(self.nests), "sleeping": len([o for o in self.organisms if not o.awake]),
            "territory": len(self.territory), "parasites": len(self.parasites),
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
            x=max(0, min(WIDTH - 1, x)),
            y=max(0, min(HEIGHT - 1, y)),
            genome=genome,
            energy=energy,
            age=0,
            generation=generation,
            id=self.next_id,
        )
        self.organisms.append(org)
        self.all_genomes_seen.add(tuple(genome))
        self.next_id += 1
        return org

    def _temperature_at(self, y: int) -> float:
        t = 1.0 - y / (HEIGHT - 1)
        if self.season == "summer":
            t = min(1.0, t + 0.25)
        else:
            t = max(0.0, t - 0.25)
        return t

    def _mutate(self, genome: list, mut_rate: float = BASE_MUTATION_RATE) -> list:
        new = list(genome)
        for i in range(len(new)):
            if random.random() < mut_rate:
                g_min, g_max = GENES[i][1], GENES[i][2]
                delta = random.choice([-1, 1])
                new[i] = max(g_min, min(g_max, new[i] + delta))
        return new

    def _transpose(self, genome: list, rate: float) -> list:
        new = list(genome)
        if random.random() < rate:
            i = random.randrange(len(new))
            j = random.randrange(len(new))
            if i != j and random.random() < 0.5:
                new[j] = new[i]
            elif i != j:
                new[i], new[j] = new[j], new[i]
        return new

    def _nearest_resource(self, org: Organism) -> Optional[Tuple[int, int]]:
        sense = org.genome[1]
        best_d = sense + 1
        best = None
        x, y = org.x, org.y
        for dx in range(-sense, sense + 1):
            for dy in range(-sense, sense + 1):
                pos = (x + dx, y + dy)
                if pos in self.resources:
                    d = abs(dx) + abs(dy)
                    if d < best_d:
                        best_d = d
                        best = pos
        return best

    def _nearest_organism(self, org: Organism, dead: Set[int]) -> Optional[Organism]:
        sense = org.genome[1]
        best_d = sense + 1
        best = None
        for other in self.organisms:
            if other is org or other.id in dead:
                continue
            d = abs(other.x - org.x) + abs(other.y - org.y)
            if d < best_d:
                best_d = d
                best = other
        return best

    def step(self):
        self.tick += 1
        self.shift_timer -= 1

        pre_pop = len(self.organisms)

        if self.shift_timer <= 0:
            self._shift_environment()
            self.shift_timer = random.randint(*ENV_SHIFT_INTERVAL)

        self.season_timer -= 1
        if self.season_timer <= 0:
            self.season = "winter" if self.season == "summer" else "summer"
            self.season_timer = random.randint(*SEASON_LENGTH)
            self.events.append(
                f"🌤️ Season: {'☀ summer' if self.season == 'summer' else '❄ winter'}"
            )
            self._sound("season")

        dead: Set[int] = set()

        # --- CARRYING CAPACITY (logistic soft ceiling on reproduction) ---
        carry_cap = WIDTH * HEIGHT * 0.4
        n_carry = len(self.organisms)
        carry_pressure = 1.0 + (n_carry / carry_cap) ** 2

        # --- CORPSE DECOMPOSITION (corpses feed adjacent organisms) ---
        for (cx, cy), rtype in list(self.resources.items()):
            if rtype == "corpse":
                for org in self.organisms:
                    if org.id not in dead and abs(org.x - cx) + abs(org.y - cy) <= 1:
                        org.energy += 0.03

        random.shuffle(self.organisms)

        for org in self.organisms:
            if org.id in dead:
                continue

            org.age += 1

            diet = org.genome[8]

            # --- LIFE STAGE: juvenile penalty, elder bonus ---
            if org.age < 3:
                org.energy -= 0.05
            is_elder = org.age > 30

            # --- TORPOR (hibernation during scarcity) ---
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

            # --- SLEEP CYCLES: rest restores energy faster but leaves vulnerable ---
            if not torpid:
                if org.awake:
                    if org.energy < 0.8 and org.age > 5:
                        org.awake = False
                        org.sleep_timer = random.randint(3, 8)
                else:
                    org.sleep_timer -= 1
                    rest_bonus = 0.08 + org.genome[3] * 0.02
                    org.energy += rest_bonus
                    if org.sleep_timer <= 0 or org.energy > 3.0:
                        org.awake = True
            asleep = not org.awake and not torpid

            # --- PARASITE DRAIN: parasites leech energy from host ---
            if org.parasite and org.host_id is not None:
                host = None
                for h in self.organisms:
                    if h.id == org.host_id and h.id not in dead:
                        host = h
                        break
                if host:
                    drain = 0.06 + org.genome[2] * 0.02
                    org.energy += drain
                    host.energy -= drain
                    # Host fights back if aggressive
                    if host.genome[2] > 0 and random.random() < host.genome[2] * 0.1:
                        org.energy -= 0.2
                        if org.energy <= 0:
                            org.cause_of_death = "parasitism"
                            dead.add(org.id)
                            continue
                else:
                    self.parasites.pop(org.id, None)
                    org.host_id = None
                    org.parasite = False

            # --- PARASITE INFECTION: low-energy organisms attach to energy-rich hosts ---
            if not org.parasite and not torpid and not asleep and org.energy < 2.0 and org.genome[2] <= 1:
                for host_candidate in self.organisms:
                    if host_candidate is org or host_candidate.id in dead or host_candidate.parasite:
                        continue
                    if host_candidate.energy > 3.0 and abs(host_candidate.x - org.x) <= 1 and abs(host_candidate.y - org.y) <= 1:
                        org.parasite = True
                        org.host_id = host_candidate.id
                        self.parasites[org.id] = host_candidate.id
                        org.energy += 0.2  # initial feed
                        host_candidate.energy -= 0.5  # establishment cost
                        break

            # --- SENSE & MOVE ---
            speed = org.genome[0] + 1
            wander = org.genome[4]

            # Choose target: organism (carnivore/omnivore) or resource (herbivore/omnivore)
            target = None
            if diet >= 1:
                prey = self._nearest_organism(org, dead)
                if diet == 1:
                    # Pure carnivore: hunt organisms only
                    target = (prey.x, prey.y) if prey else None
                elif diet == 2:
                    # Omnivore: chase closest of organism or resource
                    res_target = self._nearest_resource(org)
                    if prey and res_target:
                        p_dist = abs(prey.x - org.x) + abs(prey.y - org.y)
                        r_dist = abs(res_target[0] - org.x) + abs(res_target[1] - org.y)
                        target = (prey.x, prey.y) if p_dist <= r_dist else res_target
                    elif prey:
                        target = (prey.x, prey.y)
                    elif res_target:
                        target = res_target
            else:
                target = self._nearest_resource(org)

            # --- FEAR RESPONSE: herbivores flee from nearby predators ---
            if diet == 0 and org.genome[1] > 0:
                sense = org.genome[1]
                nearest_pred = None
                min_d = sense + 1
                for other in self.organisms:
                    if other is org or other.id in dead or other.genome[8] < 1:
                        continue
                    d = abs(other.x - org.x) + abs(other.y - org.y)
                    if d < min_d:
                        min_d = d
                        nearest_pred = other
                if nearest_pred:
                    flee_p = 0.6
                    # Sentinel alarm: high-sense herbivores amplify fear
                    for sentinel in self.organisms:
                        if sentinel is org or sentinel.id in dead or sentinel.genome[8] != 0:
                            continue
                        if sentinel.genome[1] >= 3 and abs(sentinel.x - nearest_pred.x) <= 3 and abs(sentinel.y - nearest_pred.y) <= 3:
                            flee_p = 0.9
                            break
                    if random.random() < flee_p:
                        fx = org.x + (org.x - nearest_pred.x)
                        fy = org.y + (org.y - nearest_pred.y)
                        target = (max(0, min(WIDTH-1, fx)), max(0, min(HEIGHT-1, fy)))
                    fx = org.x + (org.x - nearest_pred.x)
                    fy = org.y + (org.y - nearest_pred.y)
                    target = (max(0, min(WIDTH-1, fx)), max(0, min(HEIGHT-1, fy)))
                    org.energy -= 0.05

            if not torpid and target:
                tx, ty = target
                steps = min(speed, abs(tx - org.x) + abs(ty - org.y))
                for _ in range(steps):
                    dx = 1 if tx > org.x else -1 if tx < org.x else 0
                    dy = 1 if ty > org.y else -1 if ty < org.y else 0
                    if random.random() < 0.15 + wander * 0.12:
                        dx += random.choice([-1, 0, 1])
                        dy += random.choice([-1, 0, 1])
                    org.x = max(0, min(WIDTH - 1, org.x + (dx if dx else 0)))
                    org.y = max(0, min(HEIGHT - 1, org.y + (dy if dy else 0)))
                    if (org.x, org.y) in self.resources:
                        break
            elif not torpid:
                # Memory recall: navigate toward remembered food spots
                mem_target = None
                if org.memory:
                    # Filter out spots that already have an organism on them
                    occupied = {(o.x, o.y) for o in self.organisms if o.id not in dead}
                    valid = [m for m in org.memory if m not in occupied]
                    if valid:
                        best_d = speed + 1
                        for m in valid:
                            d = abs(m[0] - org.x) + abs(m[1] - org.y)
                            if d < best_d:
                                best_d = d
                                mem_target = m
                if mem_target:
                    tx, ty = mem_target
                    steps = min(speed, abs(tx - org.x) + abs(ty - org.y))
                    for _ in range(steps):
                        dx = 1 if tx > org.x else -1 if tx < org.x else 0
                        dy = 1 if ty > org.y else -1 if ty < org.y else 0
                        org.x = max(0, min(WIDTH - 1, org.x + dx))
                        org.y = max(0, min(HEIGHT - 1, org.y + dy))
                        if (org.x, org.y) in self.resources:
                            break
                else:
                    for _ in range(speed):
                        org.x = max(
                            0,
                            min(
                                WIDTH - 1,
                                org.x + random.choice([-1, 0, 1]),
                            ),
                        )
                        org.y = max(
                            0,
                            min(
                                HEIGHT - 1,
                                org.y + random.choice([-1, 0, 1]),
                            ),
                        )
                # Memory decay
                if org.memory and random.random() < 0.02:
                    org.memory.pop(random.randrange(len(org.memory)))

            # --- SEASONAL MIGRATION: north/south drift based on thermal mismatch ---
            if not torpid and not asleep and org.genome[4] > 0:
                pref_temp = org.genome[7] / 4.0
                actual_temp = self._temperature_at(org.y)
                mismatch = abs(actual_temp - pref_temp)
                if mismatch > 0.3:
                    migrate_dir = 1 if actual_temp > pref_temp else -1  # 1=move south, -1=move north
                    strength = mismatch * org.genome[4] * 0.3
                    if random.random() < strength:
                        org.y = max(0, min(HEIGHT - 1, org.y + migrate_dir))

            # --- FLOCKING: low-aggression organisms align with nearby similar hues ---
            if not torpid and not asleep and org.genome[2] <= 1 and org.genome[8] == 0:
                avg_dx = 0.0
                avg_dy = 0.0
                count = 0
                for flockmate in self.organisms:
                    if flockmate is org or flockmate.id in dead:
                        continue
                    if abs(flockmate.x - org.x) <= 3 and abs(flockmate.y - org.y) <= 3:
                        hue_diff = abs(org.genome[5] - flockmate.genome[5])
                        if hue_diff <= 2:
                            avg_dx += flockmate.x - org.x
                            avg_dy += flockmate.y - org.y
                            count += 1
                if count > 1 and random.random() < 0.4:
                    dx = int(avg_dx / count)
                    dy = int(avg_dy / count)
                    if dx or dy:
                        org.x = max(0, min(WIDTH - 1, org.x + (1 if dx > 0 else -1 if dx < 0 else 0)))
                        org.y = max(0, min(HEIGHT - 1, org.y + (1 if dy > 0 else -1 if dy < 0 else 0)))

            # --- TERRITORY MARKING: aggression marks position with scent ---
            if org.genome[2] > 0:
                tpos = (org.x, org.y)
                if tpos not in self.territory:
                    self.territory[tpos] = {}
                hue = org.genome[5]
                self.territory[tpos][hue] = self.territory[tpos].get(hue, 0) + 1

            # --- TERRITORY COST: standing on foreign territory costs energy ---
            if org.genome[2] > 0:
                tpos = (org.x, org.y)
                if tpos in self.territory:
                    my_hue = org.genome[5]
                    foreign = sum(c for h, c in self.territory[tpos].items() if h != my_hue)
                    if foreign > 0:
                        org.energy -= 0.02 * min(3, foreign)

            # --- PARASITE HOST DEATH CHECK: when host dies, parasite reproduces ---
            if org.parasite and org.host_id is not None:
                host_dead = org.host_id in dead or not any(o.id == org.host_id for o in self.organisms)
                if host_dead:
                    org.parasite = False
                    org.host_id = None
                    self.parasites.pop(org.id, None)
                    # Bonus energy from host death
                    org.energy += 1.0
                    # Chance to spawn offspring parasite
                    if org.energy > 4.0 and random.random() < 0.3:
                        neighbors = [(org.x+dx, org.y+dy) for dx,dy in [(0,1),(0,-1),(1,0),(-1,0)]
                                     if 0 <= org.x+dx < WIDTH and 0 <= org.y+dy < HEIGHT
                                     and not any(o.x == org.x+dx and o.y == org.y+dy for o in self.organisms if o.id not in dead)]
                        if neighbors:
                            nx, ny = random.choice(neighbors)
                            child = self._spawn(nx, ny, self._mutate(org.genome, MUT_RATES[org.genome[6]]), 1.5, org.generation + 1)
                            child.parasite = True
                            if child.generation > self.max_gen_ever:
                                self.max_gen_ever = child.generation

            # --- NEST BUILDING: spend energy to build permanent shelter ---
            if not torpid and not asleep and org.energy > 3.0:
                npos = (org.x, org.y)
                if npos not in self.nests and random.random() < 0.05:
                    self.nests[npos] = 0
                    org.energy -= 0.5
                elif npos in self.nests:
                    self.nests[npos] += 1

            # --- NEST BENEFIT: nests buffer temperature ---
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

            # --- HORIZONTAL GENE TRANSFER (swap genes with adjacent organisms) ---
            if not torpid and random.random() < 0.03:
                for hgt_other in self.organisms:
                    if hgt_other is org or hgt_other.id in dead:
                        continue
                    if abs(hgt_other.x - org.x) <= 1 and abs(hgt_other.y - org.y) <= 1:
                        i = random.randrange(len(GENES))
                        org.genome[i], hgt_other.genome[i] = hgt_other.genome[i], org.genome[i]
                        break

            # --- CONSUME RESOURCE ---
            pos = (org.x, org.y)
            if not torpid and pos in self.resources:
                rtype = self.resources.pop(pos)
                base_val = RESOURCE_TYPES[rtype]["value"]
                met_bonus = 1.0 + org.genome[3] * 0.2
                # Age-foraging bonus: peaks at middle age
                age_bonus = min(org.age, 30) / 30.0 * 0.3
                met_bonus += age_bonus
                if diet == 0:
                    met_bonus += 0.3
                elif diet == 1 and rtype == "corpse":
                    met_bonus += 0.4
                org.energy += base_val * met_bonus
                # Soil fertility bonus from previous grazing
                fert = self.soil_fertility.get(pos, 0.0)
                if fert > 0 and rtype in ("food", "bounty"):
                    org.energy += fert * 0.2
                    self.soil_fertility[pos] = max(0.0, fert - 0.05)
                # Spatial memory: remember good foraging spots
                if rtype in ("food", "bounty"):
                    org.memory.append(pos)
                    if len(org.memory) > 3:
                        org.memory.pop(0)
                # Niche construction: herbivores enrich soil
                if diet == 0 and rtype == "food":
                    self.soil_fertility[pos] = self.soil_fertility.get(pos, 0.0) + 0.1

            # --- METABOLIC COST ---
            base_cost = SUMMER_BASE_COST if self.season == "summer" else WINTER_BASE_COST
            base_cost += org.genome[3] * 0.15
            size_cost = (org.genome[0] + org.genome[2]) * 0.01
            speed_cost = org.genome[0] * 0.02
            sense_cost = org.genome[1] * 0.025
            agg_cost = org.genome[2] * 0.015
            if diet == 2:
                agg_cost += 0.02
            tox_cost = org.genome[9] * 0.01
            pref_temp = org.genome[7] / 4.0
            actual_temp = self._temperature_at(org.y)
            thermal_cost = abs(actual_temp - pref_temp) * 0.25
            mult = 0.1 if torpid else 1.0
            if asleep:
                mult = 0.0
            org.energy -= (base_cost + size_cost + speed_cost + sense_cost + agg_cost + thermal_cost * (1.0 - nest_bonus) + tox_cost) * mult

            # --- FIGHT (overlapping organisms) ---
            if not torpid:
                for other in self.organisms:
                    if other is org or other.id in dead:
                        continue
                    if other.x == org.x and other.y == org.y:
                        a = org.genome[2]
                        b = other.genome[2]
                        ham = sum(1 for i in range(len(GENES)) if org.genome[i] != other.genome[i])
                        kin_mod = 0.3 if ham <= 2 else 1.0
                        if a > 0 and b > 0:
                            org_power = max(0, org.energy) * (a + 1) / 4 * kin_mod
                            other_power = max(0, other.energy) * (b + 1) / 4 * kin_mod
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
                        elif a > 0 and random.random() < a / 3 * kin_mod:
                            other.cause_of_death = "fighting"
                            org.energy += other.energy * 0.2
                            dead.add(other.id)

            if org.id in dead:
                continue

            # --- HUNTING (carnivores actively attack adjacent organisms) ---
            if not torpid and diet >= 1:
                for other in self.organisms:
                    if other is org or other.id in dead:
                        continue
                    if abs(other.x - org.x) <= 1 and abs(other.y - org.y) <= 1:
                        a = org.genome[2]
                        b = other.genome[2]
                        # Pack hunting: nearby predators coordinate
                        pack_count = 0
                        for packmate in self.organisms:
                            if packmate is org or packmate.id in dead or packmate.genome[8] < 1:
                                continue
                            if abs(packmate.x - other.x) <= 2 and abs(packmate.y - other.y) <= 2:
                                pack_count += 1
                        pack_bonus = 1.0 + pack_count * 0.15
                        atk_mult = (1.5 if diet == 1 else 1.0) * pack_bonus
                        # Prey camouflage: hue matching temperature zone defends
                        prey_hue = other.genome[5]
                        prey_y_zone = int(5 * (1.0 - other.y / (HEIGHT - 1)))
                        camo = 1.0 - abs(prey_hue - prey_y_zone) / 5.0
                        # Herding defense: nearby prey protect each other
                        herd_count = 0
                        for herdmate in self.organisms:
                            if herdmate is other or herdmate.id in dead or herdmate.genome[8] >= 1:
                                continue
                            if abs(herdmate.x - other.x) <= 2 and abs(herdmate.y - other.y) <= 2:
                                herd_count += 1
                        herd_bonus = 1.0 + herd_count * 0.15
                        # Cannibalism: starving predators attack any organism including own species
                        is_cannibal = org.energy < 1.0 and diet >= 1
                        if is_cannibal:
                            ham = sum(1 for i in range(len(GENES)) if org.genome[i] != other.genome[i])
                            if ham <= 1 and random.random() < 0.3:
                                break  # kin recognition inhibits cannibalism of nearly-identical
                        # Prey bioluminescence attracts predators (easier to spot)
                        prey_lumen = other.genome[10]
                        lumen_spot = 1.0 + prey_lumen * 0.3
                        # Predator learned avoidance of toxic hues
                        predator_hue = org.genome[5]
                        learned = self.predator_memory.get(predator_hue, set())
                        if prey_hue in learned and random.random() < 0.4:
                            break  # predator hesitates, doesn't attack
                        # --- MIMICRY: harmless prey mimicking toxic hues gain protection ---
                        mimic_bonus = 1.0
                        if other.genome[9] == 0:
                            nearby_toxic_hues = set()
                            for mim in self.organisms:
                                if mim is other or mim.id in dead:
                                    continue
                                if mim.genome[9] >= 2 and abs(mim.x - other.x) <= 5 and abs(mim.y - other.y) <= 5:
                                    nearby_toxic_hues.add(mim.genome[5])
                            if nearby_toxic_hues and other.genome[5] in nearby_toxic_hues:
                                mimic_bonus = 0.75  # predator confuses mimic with toxic model
                            if nearby_toxic_hues and other.genome[5] in learned:
                                mimic_bonus = 0.6
                        # Müllerian convergence: generalize learned avoidance to adjacent hues
                        if org.genome[5] in learned and other.genome[9] >= 2:
                            for dh in (-1, 1):
                                adj_hue = (other.genome[5] + dh) % 6
                                if predator_hue not in self.predator_memory:
                                    self.predator_memory[predator_hue] = set()
                                self.predator_memory[predator_hue].add(adj_hue)
                        org_power = max(0.1, org.energy) * (a + 1) / 4 * atk_mult * lumen_spot * (1.3 if is_cannibal else 1.0) * mimic_bonus
                        other_power = max(0.1, other.energy) * (b + 1) / 4 * (1.0 + camo * 0.5) * herd_bonus
                        total = org_power + other_power
                        if random.random() < org_power / total:
                            gain = other.energy * 0.6
                            if diet == 1:
                                gain *= 1.3
                            org.energy += gain
                            # Prey toxin damages predator
                            tox = other.genome[9]
                            if tox > 0:
                                org.energy -= tox * 0.4
                                # Predator learns to avoid this hue
                                pred_hue = org.genome[5]
                                if pred_hue not in self.predator_memory:
                                    self.predator_memory[pred_hue] = set()
                                self.predator_memory[pred_hue].add(other.genome[5])
                            other.cause_of_death = "predation"
                            dead.add(other.id)
                            break  # satiated for this tick
                        else:
                            # Failed hunt — prey fights back, predator injured
                            org.energy -= other.energy * 0.2
                            if org.energy <= 0:
                                org.cause_of_death = "predation"
                                dead.add(org.id)
                            break

            if org.id in dead:
                continue

            # --- SYMBIOSIS (herbivore + carnivore mutualism) ---
            if not torpid and diet < 2:
                for sym_other in self.organisms:
                    if sym_other is org or sym_other.id in dead:
                        continue
                    if (diet == 0 and sym_other.genome[8] == 1) or (diet == 1 and sym_other.genome[8] == 0):
                        if abs(sym_other.x - org.x) <= 1 and abs(sym_other.y - org.y) <= 1:
                            org.energy += 0.03
                            break

            # --- KIN SELECTION (similar genomes share energy) ---
            if not torpid and org.energy > 1.5:
                for kin in self.organisms:
                    if kin is org or kin.id in dead:
                        continue
                    if abs(kin.x - org.x) <= 1 and abs(kin.y - org.y) <= 1:
                        ham = sum(1 for i in range(len(GENES)) if org.genome[i] != kin.genome[i])
                        if ham <= 2 and kin.energy < org.energy - 0.5:
                            give = min(0.1, org.energy - 0.5)
                            org.energy -= give
                            kin.energy += give
                            break

            # --- ALTRUISTIC PUNISHMENT: aggressive organisms punish cheaters ---
            if not torpid and org.genome[2] >= 2 and org.energy > 2.0:
                for cheater in self.organisms:
                    if cheater is org or cheater.id in dead or cheater.genome[2] > org.genome[2]:
                        continue
                    if abs(cheater.x - org.x) <= 2 and abs(cheater.y - org.y) <= 2:
                        if cheater.energy > 3.0:
                            starving_nearby = any(
                                o is not org and o.id not in dead and o.energy < 0.5
                                and abs(o.x - cheater.x) <= 2 and abs(o.y - cheater.y) <= 2
                                for o in self.organisms
                            )
                            if starving_nearby:
                                org.energy -= 0.15
                                cheater.energy -= 0.4
                                if cheater.energy <= 0:
                                    cheater.cause_of_death = "fighting"
                                    dead.add(cheater.id)
                                break

            # --- PARENTAL CARE (parent feeds nearby young children) ---
            if org.pupils and org.energy > 1.0:
                expired = []
                for cid, remaining in org.pupils.items():
                    if remaining <= 0:
                        expired.append(cid)
                        continue
                    for child in self.organisms:
                        if child.id == cid and child.id not in dead:
                            if abs(child.x - org.x) <= 2 and abs(child.y - org.y) <= 2:
                                give = min(0.02, org.energy - 0.5)
                                org.energy -= give
                                child.energy += give
                            break
                    org.pupils[cid] = remaining - 1
                for cid in expired:
                    del org.pupils[cid]

            # --- DISEASE ---
            if org.id in self.diseased:
                # Spread BEFORE drain so patient zero infects others before dying
                for other in self.organisms:
                    if other.id in dead or other.id in self.diseased or other.id in self.immune:
                        continue
                    if abs(other.x - org.x) <= 2 and abs(other.y - org.y) <= 2:
                        if random.random() < 0.20:
                            self.diseased.add(other.id)

                met = org.genome[3]
                drain = 0.12 * max(0.1, 1.0 - met * 0.15)
                org.energy -= drain
                if random.random() < 0.05 + met * 0.05:
                    self.diseased.discard(org.id)
                    self.immune.add(org.id)

            if org.id in dead:
                continue

            # Immune wanes over time
            if org.id in self.immune and random.random() < 0.005:
                self.immune.discard(org.id)

            # --- FAT METABOLISM: store excess energy, draw during scarcity ---
            fat_cap = 1.0 + org.genome[3] * 1.5
            if org.energy > 2.0 and org.fat < fat_cap:
                store = min(org.energy - 2.0, fat_cap - org.fat, 0.5)
                org.fat += store
                org.energy -= store
            elif org.energy < 0.5 and org.fat > 0:
                draw = min(org.fat, 1.0)
                org.fat -= draw
                org.energy += draw * 0.7

            # --- STARVATION ---
            if org.energy <= 0:
                org.cause_of_death = "disease" if org.id in self.diseased else "starvation"
                dead.add(org.id)
                continue

            # --- OLD AGE ---
            size = org.genome[0] + org.genome[2]
            max_age = 180 // (org.genome[3] + 1) + 60 + size * 5
            if org.age > max_age:
                org.cause_of_death = "old_age"
                dead.add(org.id)
                continue

            # --- DENSITY-DEPENDENT FERTILITY ---
            local_density = sum(
                1 for o in self.organisms
                if o is not org and o.id not in dead
                and abs(o.x - org.x) + abs(o.y - org.y) <= 3
            )
            density_penalty = 1.0 + max(0, local_density - 3) * 0.15

            # --- REPRODUCTION ---
            repro_thresh = REPRODUCTION_THRESHOLD * density_penalty * carry_pressure
            sex_thresh = SEXUAL_THRESHOLD * density_penalty * carry_pressure
            if is_elder:
                repro_thresh *= 0.8
                sex_thresh *= 0.8
            if (
                org.energy >= repro_thresh
                or (org.energy >= sex_thresh
                    and any(
                        o is not org and o.id not in dead
                        and o.energy >= sex_thresh
                        and abs(o.x - org.x) <= 1 + o.genome[10]  # bioluminescence extends mate detection
                        and abs(o.y - org.y) <= 1 + o.genome[10]
                        for o in self.organisms
                    ))
            ):
                # Try to find a mate for sexual reproduction
                mate = None
                # Only seek mate if both have >= sex_thresh
                if org.energy >= sex_thresh:
                    for other in self.organisms:
                        if other is org or other.id in dead:
                            continue
                        if other.energy >= sex_thresh:
                            dx = abs(other.x - org.x)
                            dy = abs(other.y - org.y)
                            max_range = 1 + max(org.genome[10], other.genome[10])
                            if dx <= max_range and dy <= max_range:
                                mate = other
                                break

                # Find adjacent empty cell
                neighbors = []
                for dx, dy in [(0,1),(0,-1),(1,0),(-1,0)]:
                    nx, ny = org.x + dx, org.y + dy
                    if 0 <= nx < WIDTH and 0 <= ny < HEIGHT:
                        if not any(
                            o.x == nx and o.y == ny
                            for o in self.organisms
                            if o.id not in dead
                        ):
                            neighbors.append((nx, ny))
                if neighbors:
                    nx, ny = random.choice(neighbors)
                    if mate:
                        # SEXUAL: recombine genomes from both parents
                        child_genome = []
                        for i in range(len(GENES)):
                            if random.random() < 0.5:
                                child_genome.append(org.genome[i])
                            else:
                                child_genome.append(mate.genome[i])
                        child_genome = self._mutate(child_genome, MUT_RATES[org.genome[6]])
                        child_genome = self._transpose(child_genome, MUT_RATES[org.genome[6]] * 0.5)
                        child_gen = max(org.generation, mate.generation) + 1
                        size = org.genome[0] + org.genome[2]
                        energy_cost = ENERGY_COST_PER_CHILD * 0.7 * (1.0 + size * 0.08)
                        child = self._spawn(nx, ny, child_genome, energy_cost, child_gen)
                        org.pupils[child.id] = 5
                        if mate is not org:
                            mate.pupils[child.id] = 5
                        org.energy -= energy_cost
                        mate.energy -= energy_cost
                        if child_gen > self.max_gen_ever:
                            self.max_gen_ever = child_gen
                            self.events.append(
                                f"⚡ Gen {self.max_gen_ever} (sexual! "
                                f"{GLYPH_SET[child_genome[5] % len(GLYPH_SET)]})"
                            )
                            self._sound("new_gen")
                    elif org.energy >= repro_thresh:
                        # ASEXUAL: clone + mutate
                        child_genome = self._mutate(org.genome, MUT_RATES[org.genome[6]])
                        child_genome = self._transpose(child_genome, MUT_RATES[org.genome[6]] * 0.5)
                        size = org.genome[0] + org.genome[2]
                        cost_mult = 1.0 + size * 0.08
                        child = self._spawn(nx, ny, child_genome, ENERGY_COST_PER_CHILD * cost_mult, org.generation + 1)
                        org.pupils[child.id] = 5
                        org.energy -= ENERGY_COST_PER_CHILD * 1.1 * cost_mult
                        if org.generation + 1 > self.max_gen_ever:
                            self.max_gen_ever = org.generation + 1
                            self.events.append(
                                f"⚡ Gen {self.max_gen_ever} reached! "
                                f"({GLYPH_SET[child_genome[5] % len(GLYPH_SET)]})"
                            )
                            self._sound("new_gen")

        # Remove dead — but leave corpse resources
        pop_before = len(self.organisms)
        dead_list = []
        kept = []
        for o in self.organisms:
            if o.id in dead:
                dead_list.append(o)
                # Track death cause
                if o.cause_of_death:
                    self.death_stats[o.cause_of_death] = self.death_stats.get(o.cause_of_death, 0) + 1
                else:
                    self.death_stats["unknown"] = self.death_stats.get("unknown", 0) + 1
            else:
                kept.append(o)
        self.organisms = kept
        pop_after = len(self.organisms)
        died = pop_before - pop_after
        # Clean up diseased IDs for dead organisms
        self.diseased &= {o.id for o in self.organisms}
        for o in dead_list:
            if random.random() < 0.5 and (o.x, o.y) not in self.resources:
                bounty_val = min(2.5, 0.3 + o.energy * 0.3)
                # corpseless corpses become food; energy-rich corpses become bounty-ish
                rtype = "corpse" if o.energy > 1.0 else "food"
                self._add_resource(o.x, o.y, rtype)

        # Track max age
        for o in self.organisms:
            if o.age > self.max_age_ever:
                self.max_age_ever = o.age

        # Gene loss detection
        current_gene_vals = [set() for _ in GENES]
        for o in self.organisms:
            for i, v in enumerate(o.genome):
                current_gene_vals[i].add(v)
        lost_this_tick = []
        for i, (expected, actual) in enumerate(zip(self.genes_found, current_gene_vals)):
            lost = expected - actual
            if lost:
                lost_this_tick.append((i, lost))
                self.genes_found[i] = current_gene_vals[i]
        for gene_idx, lost_vals in lost_this_tick:
            for v in lost_vals:
                self.events.append(
                    f"🧬 Extinct: {GENES[gene_idx][0]}={v} "
                    f"(never again)"
                )
                self._sound("gene_extinct")

        # Record history
        self.pop_history.append(pop_after)
        if len(self.pop_history) > 60:
            self.pop_history = self.pop_history[-60:]

        # Sonify tick state
        self.sonify_counter += 1
        if self.sonify_counter % 3 == 0:
            self._sonify_tick()

        # Population crash event
        if died > 5 and pop_after > 0:
            self.events.append(f"💀 {died} died in a single tick")
            self._sound("mass_death")

        # Population recovery event
        if pre_pop < 10 and pop_after > pre_pop and pop_after >= 10:
            self.events.append(f"🌱 Population recovered to {pop_after}")
            self._sound("recovery")

        # Extinction-level event tracking
        if pop_after < self.min_pop_ever:
            self.min_pop_ever = pop_after
            if pop_after <= 3:
                entry = f"⚠ CRITICAL: pop={pop_after} at T={self.tick} (max_gen={self.max_gen_ever})"
                self.extinction_log.append(entry)
                self.events.append(f"⚠ Only {pop_after} organisms remain!")
                self._sound("critical")
                self._log_extinction("CRITICAL", pop_after)
            elif pop_after <= 10:
                entry = f"📉 Bottleneck: pop={pop_after} at T={self.tick} (max_gen={self.max_gen_ever})"
                self.extinction_log.append(entry)
                self.events.append(f"📉 Population bottleneck: {pop_after}")
                self._sound("bottleneck")
                self._log_extinction("BOTTLENECK", pop_after)

        # Radiation spike — injects genetic variation
        if self.tick > 50 and random.random() < 0.008:
            n_mutated = 0
            for org in self.organisms:
                i = random.randint(0, len(GENES) - 1)
                g_min, g_max = GENES[i][1], GENES[i][2]
                delta = random.choice([-1, 1])
                old = org.genome[i]
                org.genome[i] = max(g_min, min(g_max, org.genome[i] + delta))
                if org.genome[i] != old:
                    n_mutated += 1
            if n_mutated > 0:
                self.events.append(f"☢ Radiation spike — {n_mutated} organisms mutated")
                self._sound("radiation")

        # Spontaneous disease outbreak
        if not self.diseased and len(self.organisms) > 25 and random.random() < 0.03:
            candidates = [o for o in self.organisms if o.energy > 1.5]
            if len(candidates) >= 2:
                n = random.randint(2, min(5, len(candidates)))
                victims = random.sample(candidates, n)
                for v in victims:
                    self.diseased.add(v.id)
                self.events.append(f"🦠 Disease outbreak! {len(victims)} infected")
                self._sound("disease")
        elif self.diseased and random.random() < 0.002 and len(self.diseased) > 10:
            self.events.append(f"🦠 Epidemic: {len(self.diseased)} infected")
            self._sound("epidemic")

        # --- MIGRATION (invasion from outside) ---
        self.migration_timer -= 1
        if self.migration_timer <= 0:
            batch = random.randint(*MIGRATION_BATCH)
            for _ in range(batch):
                x = random.randint(0, WIDTH - 1)
                y = random.randint(0, HEIGHT - 1)
                genome = [random.randint(g[1], g[2]) for g in GENES]
                self._spawn(x, y, genome, 4.0)
            self.events.append(
                f"🌊 {batch} invaders arrived from beyond"
            )
            self._sound("migration")
            self.migration_timer = random.randint(*MIGRATION_INTERVAL)

        # --- FOSSIL RECORD: track extinct lineages ---
        current_genomes = {tuple(o.genome) for o in self.organisms}
        newly_extinct = self.all_genomes_seen - current_genomes - self.recorded_fossils
        if newly_extinct:
            for g in newly_extinct:
                self.recorded_fossils.add(g)
                self.fossil_lineages.append(g)
            self.fossil_count += len(newly_extinct)
            self.events.append(
                f"🦴 {len(newly_extinct)} lineage(s) fossilized "
                f"(total: {self.fossil_count})"
            )
            self._sound("fossil")

        # --- REGENERATE RESOURCES ---
        regen = SUMMER_REGEN if self.season == "summer" else WINTER_REGEN
        for _ in range(regen):
            if len(self.resources) < WIDTH * HEIGHT * 0.25:
                x, y = random.randint(0, WIDTH - 1), random.randint(0, HEIGHT - 1)
                if (x, y) not in self.resources:
                    rtype = random.choices(
                        RESOURCE_KEYS,
                        weights=[t["weight"] for t in RESOURCE_TYPES.values()]
                    )[0]
                    self._add_resource(x, y, rtype)

        # Track fitness
        if self.organisms:
            self.fitness_history.append(
                sum(o.energy for o in self.organisms) / len(self.organisms)
            )
        if len(self.fitness_history) > 80:
            self.fitness_history = self.fitness_history[-80:]

        # --- TERRITORY DECAY ---
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

    def _shift_environment(self):
        remove_n = int(len(self.resources) * random.uniform(0.15, 0.4))
        self.events.append(
            f"🌋 Environment shift: -{remove_n} resources, "
            f"+clusters in new locations"
        )
        self._sound("env_shift")
        if self.tick > 100 and random.random() < 0.25:
            self.events.append(
                f"🔥 Environmental stress event — all organisms lose energy"
            )
            self._sound("stress")
        if remove_n > 0 and self.resources:
            for pos in random.sample(
                list(self.resources.keys()), min(remove_n, len(self.resources))
            ):
                del self.resources[pos]

        clusters = random.randint(2, 5)
        for _ in range(clusters):
            cx = random.randint(6, WIDTH - 6)
            cy = random.randint(3, HEIGHT - 3)
            for _ in range(random.randint(5, 15)):
                x = max(0, min(WIDTH - 1, cx + random.randint(-4, 4)))
                y = max(0, min(HEIGHT - 1, cy + random.randint(-2, 2)))
                if (x, y) not in self.resources:
                    rtype = random.choices(RESOURCE_KEYS, weights=[t["weight"] for t in RESOURCE_TYPES.values()])[0]
                    self._add_resource(x, y, rtype)

        if random.random() < 0.3:
            for org in self.organisms:
                org.energy -= random.uniform(0.5, 1.5)

    def render(self) -> str:
        grid = [[" " for _ in range(WIDTH)] for _ in range(HEIGHT)]

        for (x, y), rtype in self.resources.items():
            if 0 <= x < WIDTH and 0 <= y < HEIGHT:
                occupied = any(o.x == x and o.y == y for o in self.organisms)
                if not occupied:
                    grid[y][x] = RESOURCE_TYPES[rtype]["symbol"]

        # Render nests under unoccupied cells
        for (x, y), strength in self.nests.items():
            if 0 <= x < WIDTH and 0 <= y < HEIGHT:
                occupied = any(o.x == x and o.y == y for o in self.organisms)
                if not occupied and grid[y][x] == " ":
                    nest_age = min(4, strength // 10)
                    nest_sym = ["░", "▒", "▓", "█", "█"][nest_age]
                    grid[y][x] = f"{DIM}\033[33m{nest_sym}{RESET}"

        # Render territory as subtle background on unoccupied cells
        TERR_COLORS = ["\033[41m", "\033[42m", "\033[44m", "\033[45m", "\033[46m"]
        for (x, y), hues in self.territory.items():
            if 0 <= x < WIDTH and 0 <= y < HEIGHT:
                occupied = any(o.x == x and o.y == y for o in self.organisms)
                if not occupied and grid[y][x] == " ":
                    dom_hue = max(hues, key=hues.get)
                    tc = TERR_COLORS[dom_hue % len(TERR_COLORS)]
                    grid[y][x] = f"{DIM}{tc} {RESET}"

        # Find sentinel (most-evolved organism)
        sentinel = max(self.organisms, key=lambda o: o.generation) if self.organisms else None
        sentinel_id = sentinel.id if sentinel else -1

        for org in self.organisms:
            if 0 <= org.x < WIDTH and 0 <= org.y < HEIGHT:
                diet = org.genome[8]
                if diet == 1:
                    glyph = "⬢"
                    color = COLORS[org.genome[5] % len(COLORS)]
                elif diet == 2:
                    glyph = "⬟"
                    color = COLORS[org.genome[5] % len(COLORS)]
                else:
                    glyph = GLYPH_SET[org.genome[5] % len(GLYPH_SET)]
                    color = COLORS[org.genome[5] % len(COLORS)]
                lumen = org.genome[10]
                if not org.awake:
                    grid[org.y][org.x] = f"{DIM}\033[44m{color} {RESET}"
                elif org.parasite:
                    grid[org.y][org.x] = f"{BOLD}\033[42m{color}{glyph}{RESET}"
                elif org.id == sentinel_id and org.generation > 0:
                    grid[org.y][org.x] = f"{BOLD}\033[47m\033[30m{glyph}{RESET}"
                elif org.id in self.diseased:
                    grid[org.y][org.x] = f"{BOLD}\033[41m{color}{glyph}{RESET}"
                elif lumen >= 2:
                    grid[org.y][org.x] = f"{BOLD}\033[43m{color}{glyph}{RESET}"
                elif lumen >= 1:
                    grid[org.y][org.x] = f"\033[43m{color}{glyph}{RESET}"
                elif org.torpor:
                    grid[org.y][org.x] = f"{DIM}\033[44m{color}{glyph}{RESET}"
                elif org.fat > 1.5:
                    grid[org.y][org.x] = f"{BOLD}\033[43m{color}{glyph}{RESET}"
                elif org.genome[9] >= 2:
                    grid[org.y][org.x] = f"{BOLD}\033[45m\033[97m{glyph}{RESET}"
                elif org.genome[9] > 0:
                    grid[org.y][org.x] = f"{BOLD}\033[45m{color}{glyph}{RESET}"
                elif org.energy > 7:
                    grid[org.y][org.x] = f"{BOLD}{color}{glyph}{RESET}"
                elif org.energy > 3:
                    grid[org.y][org.x] = f"{color}{glyph}{RESET}"
                else:
                    grid[org.y][org.x] = f"{DIM}{color}{glyph}{RESET}"

        TEMP_COLORS = ["\033[34m", "\033[32m", "\033[33m", "\033[31m"]
        lines = [f"{BOLD}╔{'═' * WIDTH}╗{RESET}"]
        for y, row in enumerate(grid):
            temp_idx = min(3, int((1.0 - y / (HEIGHT - 1)) * 4))
            tc = TEMP_COLORS[temp_idx]
            lines.append(f"{BOLD}{tc}║{RESET}{''.join(row)}{BOLD}{tc}║{RESET}")
        lines.append(f"{BOLD}╚{'═' * WIDTH}╝{RESET}")

        if self.organisms:
            n = len(self.organisms)
            avg_e = sum(o.energy for o in self.organisms) / n
            max_g = max(o.generation for o in self.organisms)
            avg_spd = sum(o.genome[0] for o in self.organisms) / n
            avg_agg = sum(o.genome[2] for o in self.organisms) / n
            avg_met = sum(o.genome[3] for o in self.organisms) / n
            avg_mut = sum(o.genome[6] for o in self.organisms) / n
            avg_tmp = sum(o.genome[7] for o in self.organisms) / n
            avg_diet = sum(o.genome[8] for o in self.organisms) / n
            avg_tox = sum(o.genome[9] for o in self.organisms) / n
            avg_lumen = sum(o.genome[10] for o in self.organisms) / n
            avg_fat = sum(o.fat for o in self.organisms) / n
            n_herb = sum(1 for o in self.organisms if o.genome[8] == 0)
            n_carn = sum(1 for o in self.organisms if o.genome[8] == 1)
            n_omni = sum(1 for o in self.organisms if o.genome[8] == 2)
            n_sleep = sum(1 for o in self.organisms if not o.awake)
            n_parasite = sum(1 for o in self.organisms if o.parasite)
            species = len({tuple(o.genome) for o in self.organisms})

            # Dominant genome
            genome_counts: Dict[Tuple[int, ...], int] = {}
            for o in self.organisms:
                key = tuple(o.genome)
                genome_counts[key] = genome_counts.get(key, 0) + 1
            dominant_key = max(genome_counts, key=genome_counts.get) if genome_counts else ()
            dominant_pct = genome_counts.get(dominant_key, 0) / n * 100
            dominant_glyph = GLYPH_SET[dominant_key[5] % len(GLYPH_SET)] if dominant_key else "?"
            shannon = -sum((c/n) * math.log(c/n) for c in genome_counts.values()) if genome_counts else 0.0
            avg_age = sum(o.age for o in self.organisms) / n
            young = sum(1 for o in self.organisms if o.age <= 5)
            mid = sum(1 for o in self.organisms if 5 < o.age <= 30)
            old = sum(1 for o in self.organisms if o.age > 30)
            avg_carn_spd = sum(o.genome[0] for o in self.organisms if o.genome[8] == 1) / max(1, n_carn)
            avg_herb_spd = sum(o.genome[0] for o in self.organisms if o.genome[8] == 0) / max(1, n_herb)
        else:
            avg_e = max_g = avg_spd = avg_agg = avg_met = avg_mut = avg_tmp = avg_diet = avg_tox = avg_lumen = avg_fat = species = n = 0
            n_herb = n_carn = n_omni = 0
            n_sleep = 0
            n_parasite = 0
            young = mid = old = 0
            dominant_key = ()
            dominant_pct = 0
            dominant_glyph = "?"
            shannon = 0.0
            avg_age = 0.0
            avg_carn_spd = avg_herb_spd = 0.0

        lines.append(
            f"  Pop:{n:4d}  ⚡:{avg_e:.1f}  Gen:{max_g:3d}  Age:{avg_age:.1f}  "
            f"Sp:{species:2d}  H\u2019:{shannon:.2f}  Fos:{self.fossil_count:4d}  "
            f"Y:{young} M:{mid} O:{old}  "
            f"H:{n_herb} C:{n_carn} O:{n_omni}  Spd:{avg_spd:.1f}  "
            f"Agg:{avg_agg:.1f}  Met:{avg_met:.1f}  "
            f"\u03bcMut:{avg_mut:.1f}  Tm:{avg_tmp:.1f}  Tx:{avg_tox:.1f}  "
            f"Lu:{avg_lumen:.1f}  "
            f"Ft:{avg_fat:.2f}  "
            f"Res:{len(self.resources):3d}  Ns:{len(self.nests):2d}  Tr:{len(self.territory):2d}  "
            f"Sl:{n_sleep:2d}  Ps:{n_parasite:2d}  "
            f"Inf:{len(self.diseased):2d}  Imm:{len(self.immune):2d}  "
            f"†S:{self.death_stats.get('starvation',0)} P:{self.death_stats.get('predation',0)} "
            f"F:{self.death_stats.get('fighting',0)} O:{self.death_stats.get('old_age',0)} "
            f"D:{self.death_stats.get('disease',0)} "
            f"{'☀' if self.season == 'summer' else '\u2744'}{'S' if self.season == 'summer' else 'W'}  T:{self.tick}"
        )
        if n_herb > 0 or n_carn > 0:
            lines.append(
                f"  \u2694 carns:{avg_carn_spd:.1f}  herbs:{avg_herb_spd:.1f}  "
                f"gap:{avg_carn_spd - avg_herb_spd:+.1f}"
            )

        # Population sparkline (compact)
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

        # Fitness sparkline (rolling avg energy)
        if len(self.fitness_history) > 5:
            feats = self.fitness_history[-40:]
            mn, mx = min(feats), max(feats)
            rng = mx - mn if mx > mn else 1
            fitline = ""
            for f in feats:
                idx = int((f - mn) / rng * 7)
                fitline += "▁▂▃▄▅▆▇█"[idx]
            lines.append(f"  └fit              {fitline}")

        # Gene frequency bars (compact histogram per gene)
        if self.organisms:
            labels = ["spd", "sen", "agg", "met", "wnd", "hue", "mut", "tmp", "die", "tox"]
            bar_parts = []
            for i, (label, (_, g_min, g_max)) in enumerate(zip(labels, GENES)):
                counts = [0] * (g_max - g_min + 1)
                for o in self.organisms:
                    counts[o.genome[i]] += 1
                max_c = max(counts) if max(counts) > 0 else 1
                bars = "".join(
                    " ▁▂▃▄▅▆▇█"[min(7, int(c / max_c * 7))]
                    for c in counts
                )
                bar_parts.append(f"{label} {bars}")
            lines.append("  " + "  ".join(bar_parts))

        # Dominant genome line
        if dominant_key:
            genome_str = " ".join(str(g) for g in dominant_key)
            lines.append(
                f"  {BOLD}{dominant_glyph}{RESET} {_species_name(dominant_key)}: [{genome_str}] "
                f"({dominant_pct:.0f}% of pop)"
            )

        # Sentinel genome (most-evolved organism)
        if sentinel and sentinel.generation > 0:
            g = sentinel.genome
            tag = " 🦠" if sentinel.id in self.diseased else ""
            lines.append(
                f"  {BOLD}\033[47m\033[30m{GLYPH_SET[g[5] % len(GLYPH_SET)]}"
                f"\033[0m {_species_name(tuple(g))}: [{g[0]} {g[1]} {g[2]} {g[3]} {g[4]} {g[5]} {g[6]} {g[7]} {g[8]} {g[9]} {g[10]}]"
                f"  gen={sentinel.generation}  age={sentinel.age}  ⚡={sentinel.energy:.1f}{tag}"
            )

        # Events (last 3)
        self.events = self.events[-3:]
        for ev in self.events:
            lines.append(f"  {ev}")
        lines.append(
            "  " + " ".join(
                f"{COLORS[i]}{GLYPH_SET[i]}{RESET}{GENES[i][0]}"
                for i in range(min(len(GENES), len(GLYPH_SET)))
            ) + f"  {BOLD}·{RESET}food {BOLD}★{RESET}bounty {BOLD}✿{RESET}corpse"
        )
        return "\n".join(lines)


def main():
    global SOUND_ENABLED, SOUND_VOLUME, TICK_RATE, EXTINCTION_LOG_FILE, SEED, WIDTH, HEIGHT
    parser = argparse.ArgumentParser(description="Evolutionary ecosystem simulator")
    parser.add_argument("--volume", type=float, default=SOUND_VOLUME,
                        help=f"sound volume 0-1 (default: {SOUND_VOLUME})")
    parser.add_argument("--no-sound", action="store_true",
                        help="disable all sounds")
    parser.add_argument("--tick-rate", type=float, default=TICK_RATE,
                        help=f"seconds per tick (default: {TICK_RATE})")
    parser.add_argument("--log", default=EXTINCTION_LOG_FILE,
                        help=f"extinction log file (default: {EXTINCTION_LOG_FILE})")
    parser.add_argument("--seed", type=int, default=SEED,
                        help=f"random seed (default: {SEED})")
    parser.add_argument("--width", type=int, default=WIDTH,
                        help=f"grid width (default: {WIDTH})")
    parser.add_argument("--height", type=int, default=HEIGHT,
                        help=f"grid height (default: {HEIGHT})")
    parser.add_argument("--continuous", action="store_true",
                        help="auto-restart after extinction, accumulate log across runs")
    args = parser.parse_args()
    if args.no_sound:
        SOUND_ENABLED = False
    SOUND_VOLUME = max(0.0, min(1.0, args.volume))
    TICK_RATE = max(0.01, args.tick_rate)
    EXTINCTION_LOG_FILE = args.log
    SEED = args.seed
    WIDTH = max(16, min(256, args.width))
    HEIGHT = max(8, min(64, args.height))
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
        print("\033c", end="")
        interrupted = False
        try:
            while world.organisms:
                print(world.render())
                world.step()
                time.sleep(TICK_RATE)
                print("\033[H", end="")
        except KeyboardInterrupt:
            interrupted = True

        total_extinct = sum(
            len(set(range(g[1], g[2]+1)) - world.genes_found[i])
            for i, g in enumerate(GENES)
        )
        print(f"\n\n{'═' * 40}")
        if not world.organisms:
            print(f"  ✦  Extinction at T={world.tick} (run {run_count})")
            world._log_extinction("TOTAL_EXTINCTION", 0)
        elif interrupted:
            print(f"  ✦  Halted after {world.tick} ticks (run {run_count})")
            world._log_extinction("SNAPSHOT", len(world.organisms))
        print(f"  Pop: {len(world.organisms)}  "
              f"Generations: {world.max_gen_ever}  "
              f"Max age: {world.max_age_ever}  "
              f"Min pop: {world.min_pop_ever}")
        print(f"  Species now: {len({tuple(o.genome) for o in world.organisms})}  "
              f"Infected: {len(world.diseased)}  "
              f"Fossil lineages: {world.fossil_count}")
        if total_extinct:
            print(f"  Gene values lost: {total_extinct}")
        if world.extinction_log:
            print(f"\n  {'─' * 36}")
            print(f"  Extinction events ({len(world.extinction_log)} total):")
            for entry in world.extinction_log[-5:]:
                print(f"  {entry}")

        if not continuous or interrupted:
            print(f"\n  Extinction log written to {EXTINCTION_LOG_FILE}")
            if not world.organisms:
                print(f"\n  Extinction cause: Last {len(world.organisms)} organism(s)")
                if world.organisms:
                    last = world.organisms[0]
                    print(f"  Genome: [{last.genome[0]} {last.genome[1]} {last.genome[2]} "
                          f"{last.genome[3]} {last.genome[4]} {last.genome[5]} "
                          f"{last.genome[6]} {last.genome[7]} {last.genome[8]} {last.genome[9]} "
                          f"{last.genome[10]}]")
                    print(f"  Age: {last.age}  Energy: {last.energy:.1f}")
            print(f"{'═' * 40}")
            break

        print(f"  🔄 Run {run_count} done — restarting with seed {SEED}")
        print(f"{'═' * 40}")
        time.sleep(0.5)
        print("\033c", end="")



if __name__ == "__main__":
    main()
