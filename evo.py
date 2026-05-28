#!/usr/bin/env python3
"""evo - A self-evolving ecosystem in your terminal.

Organisms with tiny genomes navigate a shifting 2D world, consuming resources,
reproducing with mutation, and adapting as niches open and collapse.

No stable equilibrium. Ever-evolving.
"""

import random
import time
import os
import math
from dataclasses import dataclass
from typing import Dict, List, Tuple, Optional, Set

random.seed()

# --- CONFIG ---
WIDTH = 64
HEIGHT = 22
INITIAL_ORGANISMS = 50
INITIAL_RESOURCES = 150
RESOURCE_REGEN = 4
RESOURCE_VALUE = 2.0
REPRODUCTION_THRESHOLD = 6.0
ENERGY_COST_PER_CHILD = 3.0
MUTATION_RATE = 0.12
ENV_SHIFT_INTERVAL = (30, 60)
TICK_RATE = 0.06

GENES = [
    ("speed", 0, 3),
    ("sense", 0, 4),
    ("aggression", 0, 3),
    ("metabolic", 0, 3),
    ("wander", 0, 3),
    ("hue", 0, 5),
]

GLYPH_SET = "●◆▲■★✦"
COLORS = [
    "\033[31m",
    "\033[33m",
    "\033[32m",
    "\033[36m",
    "\033[34m",
    "\033[35m",
]
RESET = "\033[0m"
BOLD = "\033[1m"
DIM = "\033[2m"


@dataclass
class Organism:
    x: int
    y: int
    genome: list
    energy: float
    age: int
    generation: int
    id: int


class World:
    def __init__(self):
        self.organisms: List[Organism] = []
        self.resources: Dict[Tuple[int, int], float] = {}
        self.tick = 0
        self.next_id = 0
        self.shift_timer = random.randint(*ENV_SHIFT_INTERVAL)

        for _ in range(INITIAL_RESOURCES):
            self._add_resource(
                random.randint(0, WIDTH - 1), random.randint(0, HEIGHT - 1)
            )

        for _ in range(INITIAL_ORGANISMS):
            self._spawn(
                random.randint(0, WIDTH - 1),
                random.randint(0, HEIGHT - 1),
                [random.randint(g[1], g[2]) for g in GENES],
            )

    def _add_resource(self, x: int, y: int):
        self.resources[(x, y)] = RESOURCE_VALUE

    def _spawn(
        self, x: int, y: int, genome: list, energy: float = 5.0, generation: int = 0
    ):
        self.organisms.append(
            Organism(
                x=max(0, min(WIDTH - 1, x)),
                y=max(0, min(HEIGHT - 1, y)),
                genome=genome,
                energy=energy,
                age=0,
                generation=generation,
                id=self.next_id,
            )
        )
        self.next_id += 1

    def _mutate(self, genome: list) -> list:
        new = list(genome)
        for i in range(len(new)):
            if random.random() < MUTATION_RATE:
                g_min, g_max = GENES[i][1], GENES[i][2]
                delta = random.choice([-1, 1])
                new[i] = max(g_min, min(g_max, new[i] + delta))
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

    def step(self):
        self.tick += 1
        self.shift_timer -= 1

        if self.shift_timer <= 0:
            self._shift_environment()
            self.shift_timer = random.randint(*ENV_SHIFT_INTERVAL)

        dead: Set[int] = set()

        random.shuffle(self.organisms)

        for org in self.organisms:
            if org.id in dead:
                continue

            org.age += 1

            # --- SENSE & MOVE ---
            speed = org.genome[0] + 1
            wander = org.genome[4]
            target = self._nearest_resource(org)

            if target:
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

            # --- CONSUME RESOURCE ---
            pos = (org.x, org.y)
            if pos in self.resources:
                org.energy += self.resources.pop(pos)

            # --- METABOLIC COST ---
            org.energy -= 0.08 + org.genome[3] * 0.12

            # --- FIGHT (overlapping organisms) ---
            for other in self.organisms:
                if other is org or other.id in dead:
                    continue
                if other.x == org.x and other.y == org.y:
                    a = org.genome[2]
                    b = other.genome[2]
                    if a > 0 and b > 0:
                        org_power = org.energy * (a + 1) / 4
                        other_power = other.energy * (b + 1) / 4
                        if random.random() < org_power / (org_power + other_power):
                            org.energy += other.energy * 0.25
                            dead.add(other.id)
                        else:
                            other.energy += org.energy * 0.25
                            dead.add(org.id)
                            break
                    elif a > 0 and random.random() < a / 3:
                        org.energy += other.energy * 0.2
                        dead.add(other.id)

            if org.id in dead:
                continue

            # --- STARVATION ---
            if org.energy <= 0:
                dead.add(org.id)
                continue

            # --- OLD AGE ---
            max_age = 180 // (org.genome[3] + 1) + 60
            if org.age > max_age:
                dead.add(org.id)
                continue

            # --- REPRODUCTION ---
            if org.energy >= REPRODUCTION_THRESHOLD:
                neighbors = []
                for dx, dy in [
                    (0, 1),
                    (0, -1),
                    (1, 0),
                    (-1, 0),
                ]:
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
                    child_genome = self._mutate(org.genome)
                    self._spawn(
                        nx, ny, child_genome, ENERGY_COST_PER_CHILD, org.generation + 1
                    )
                    org.energy -= ENERGY_COST_PER_CHILD * 1.1

        # Remove dead
        self.organisms = [o for o in self.organisms if o.id not in dead]

        # --- REGENERATE RESOURCES ---
        for _ in range(RESOURCE_REGEN):
            if len(self.resources) < WIDTH * HEIGHT * 0.25:
                x, y = random.randint(0, WIDTH - 1), random.randint(0, HEIGHT - 1)
                if (x, y) not in self.resources:
                    self._add_resource(x, y)

    def _shift_environment(self):
        remove_n = int(len(self.resources) * random.uniform(0.15, 0.4))
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
                    self._add_resource(x, y)

        if random.random() < 0.3:
            for org in self.organisms:
                org.energy -= random.uniform(0.5, 1.5)

    def render(self) -> str:
        grid = [[" " for _ in range(WIDTH)] for _ in range(HEIGHT)]

        for (x, y), _ in self.resources.items():
            if 0 <= x < WIDTH and 0 <= y < HEIGHT:
                grid[y][x] = f"{DIM}·{RESET}"

        for org in self.organisms:
            if 0 <= org.x < WIDTH and 0 <= org.y < HEIGHT:
                glyph = GLYPH_SET[org.genome[5] % len(GLYPH_SET)]
                color = COLORS[org.genome[5] % len(COLORS)]
                if org.energy > 7:
                    grid[org.y][org.x] = f"{BOLD}{color}{glyph}{RESET}"
                elif org.energy > 3:
                    grid[org.y][org.x] = f"{color}{glyph}{RESET}"
                else:
                    grid[org.y][org.x] = f"{DIM}{color}{glyph}{RESET}"

        lines = [f"{BOLD}╔{'═' * WIDTH}╗{RESET}"]
        for row in grid:
            lines.append(f"{BOLD}║{RESET}{''.join(row)}{BOLD}║{RESET}")
        lines.append(f"{BOLD}╚{'═' * WIDTH}╝{RESET}")

        if self.organisms:
            n = len(self.organisms)
            avg_e = sum(o.energy for o in self.organisms) / n
            max_g = max(o.generation for o in self.organisms)
            avg_spd = sum(o.genome[0] for o in self.organisms) / n
            avg_agg = sum(o.genome[2] for o in self.organisms) / n
            avg_met = sum(o.genome[3] for o in self.organisms) / n
            species = len({tuple(o.genome) for o in self.organisms})
        else:
            avg_e = max_g = avg_spd = avg_agg = avg_met = species = 0

        lines.append(
            f"  Pop:{n:4d}  ⚡:{avg_e:.1f}  Gen:{max_g:3d}  "
            f"Sp:{species:2d}  Speed:{avg_spd:.1f}  "
            f"Agg:{avg_agg:.1f}  Met:{avg_met:.1f}  "
            f"Res:{len(self.resources):3d}  T:{self.tick}"
        )
        lines.append(
            "  " + "  ".join(
                f"{COLORS[i]}{GLYPH_SET[i]}{RESET}={GENES[i][0]}"
                for i in range(len(GENES))
            )
        )
        return "\n".join(lines)


def main():
    world = World()
    print("\033c", end="")
    try:
        while world.organisms:
            print(world.render())
            world.step()
            time.sleep(TICK_RATE)
            print("\033[H", end="")
    except KeyboardInterrupt:
        print("\n\nExtinct. ✦")
        if world.organisms:
            print(f"Final pop: {len(world.organisms)}, "
                  f"Generations: {max(o.generation for o in world.organisms)}")


if __name__ == "__main__":
    main()
