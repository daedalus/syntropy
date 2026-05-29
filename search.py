#!/usr/bin/env python3
"""Hyperparameter search for syntropy ecosystem simulation.

Sweeps over --max-energy, --max-movement-speed, --n-registers
and reports the best configuration by tick-to-extinction.

Usage:
  python3 search.py
  python3 search.py --method grid
  python3 search.py --trials 50 --tick-rate 0.02 --timeout 300 --workers 4
"""

import concurrent.futures
import itertools
import json
import random
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import syntropy


OP_NAMES = ["NOP","MOV","ADD","SUB","MUL","DIV","JMP","JZ","JG","JL",
            "SENSE","ACT","PUSH","POP","CALL","RET","HALT","RAND","ENERGY",
            "MOD","CMP","AND","OR","XOR","NOT","IND","MIN","MAX","ABS","NEG","DUP","JNE",
            "SWAP","GEN","PICK","DPTH","PC","SETPC","SQRT","EXP","TICK","DROP","OVER",
            "SHL","SHR","BIT","MLOAD","MSTORE","GLOAD","GSTORE"]


def _trial_worker(args):
    """Run one trial in an isolated subprocess. Returns entry dict."""
    energy, speed, regs, tick_rate, timeout = args
    import asyncio
    import random as rnd
    import sys as _sys
    from pathlib import Path as _Path
    _sys.path.insert(0, str(_Path(__file__).parent))
    import importlib
    import syntropy as _syn
    importlib.reload(_syn)

    _syn.SOUND_ENABLED = False
    _syn.TICK_RATE = max(0.01, tick_rate)
    _syn.MAX_SYSTEM_ENERGY = max(10, energy)
    _syn.MAX_MOVEMENT_SPEED = max(0, speed)
    _syn.NUM_REGS = max(1, min(64, regs))
    _syn.SEED = 42
    rnd.seed(42)

    async def _run():
        world = _syn.World()
        await world.mixer.start()
        t0 = time.time()
        timed_out = False
        try:
            while world.organisms and not timed_out:
                await world.step()
                await asyncio.sleep(_syn.TICK_RATE)
                if (time.time() - t0) >= timeout:
                    timed_out = True
        finally:
            await world.mixer.stop()

        best_vm = ""
        if world.organisms:
            best_org = max(world.organisms, key=lambda o: o.generation)
            if best_org.generation > 0:
                g = best_org.genome
                decoded = []
                for i in range(0, min(len(g), 54), 3):
                    if i + 2 >= len(g):
                        break
                    op = g[i] % _syn.Op.TOTAL
                    a1 = g[i + 1] % 256
                    a2 = g[i + 2] % 256
                    decoded.append(f"{OP_NAMES[op]}({a1},{a2})")
                prog = " ".join(decoded[:18])
                best_vm = f"gen={best_org.generation} age={best_org.age:.0f} " \
                          f"len={len(best_org.genome)} vm=[{prog}]"

        return {
            "ticks": world.tick,
            "pop": len(world.organisms),
            "generations": world.max_gen_ever,
            "max_age": round(world.max_age_ever, 1),
            "min_pop": world.min_pop_ever,
            "species": len({tuple(o.genome) for o in world.organisms}),
            "fossils": world.fossil_count,
            "timed_out": timed_out,
            "best_vm": best_vm,
            "wall_time": round(time.time() - t0, 2),
        }

    try:
        metrics = asyncio.run(_run())
    except Exception as e:
        return {"error": str(e), "energy": energy, "speed": speed, "regs": regs}

    s = metrics.get("ticks", 0)
    return {
        "max_energy": energy,
        "max_movement_speed": speed,
        "n_registers": regs,
        "metrics": metrics,
        "score": round(s, 1),
    }


def _fmt_speed(s):
    return "\u221e" if s == 0 else str(s)


def main():
    import argparse
    ap = argparse.ArgumentParser(description="Hyperparameter search for syntropy")
    ap.add_argument("--method", choices=["grid", "random"], default="random")
    ap.add_argument("--trials", type=int, default=30)
    ap.add_argument("--tick-rate", type=float, default=0.01,
                    help="seconds per tick (min 0.01)")
    ap.add_argument("--timeout", type=int, default=600,
                    help="max seconds per trial")
    ap.add_argument("--workers", type=int, default=1,
                    help="number of parallel processes (default: 1)")
    ap.add_argument("--output", default=None,
                    help="save results to JSON")
    ap.add_argument("--search-seed", type=int, default=42)
    args = ap.parse_args()

    random.seed(args.search_seed)

    energy_vals = [100, 200, 400, 800, 1600]
    speed_vals = [0, 20, 30, 40, 50]
    reg_vals = [2, 4, 8, 16]

    if args.method == "grid":
        combos = list(itertools.product(energy_vals, speed_vals, reg_vals))
    else:
        combos = [
            (random.choice(energy_vals), random.choice(speed_vals),
             random.choice(reg_vals))
            for _ in range(args.trials)
        ]

    print(f"Searching {len(combos)} combinations with {args.workers} workers:",
          flush=True)
    print(f"  max-energy:        {energy_vals}")
    print(f"  max-movement-speed: {[_fmt_speed(v) for v in speed_vals]}  (0=unlimited)")
    print(f"  n-registers:       {reg_vals}")
    print(f"  tick-rate:         {args.tick_rate}")
    print(f"  timeout:           {args.timeout}s", flush=True)
    print()

    results = []
    best = {"score": -1}
    t_start = time.time()

    trial_args = [
        (e, s, r, args.tick_rate, args.timeout)
        for e, s, r in combos
    ]

    with concurrent.futures.ProcessPoolExecutor(
        max_workers=args.workers
    ) as pool:
        fut_map = {pool.submit(_trial_worker, ta): ta for ta in trial_args}
        done = 0
        for fut in concurrent.futures.as_completed(fut_map):
            done += 1
            e, s, r, _, _ = fut_map[fut]
            label = f"[{done}/{len(combos)}]"
            print(f"{label} E={e:>4} M={_fmt_speed(s):>1} R={r:>2}  ",
                  end="", flush=True)

            try:
                entry = fut.result()
            except Exception as exc:
                print(f"ERROR: {exc}", flush=True)
                continue

            if "error" in entry:
                print(f"ERROR: {entry['error']}", flush=True)
                continue

            m = entry["metrics"]
            to_tag = " TIMEOUT" if m["timed_out"] else ""
            print(f"ticks={m['ticks']:>6}  gen={m['generations']:>4}  "
                  f"min_pop={m['min_pop']:>3}  "
                  f"{m.get('wall_time',0):>5.1f}s{to_tag}",
                  flush=True)
            if m.get("best_vm"):
                print(f"  best VM: {m['best_vm']}", flush=True)

            results.append(entry)

            if entry["score"] > best["score"]:
                best = entry
                print(f"  \u2190 NEW BEST  (score={entry['score']:.0f})",
                      flush=True)

    total_t = time.time() - t_start
    results.sort(key=lambda r: r["score"], reverse=True)

    print()
    print("=" * 75)
    print(f"  SEARCH COMPLETE  ({len(combos)} trials in {total_t:.0f}s)")
    print("=" * 75)
    print()

    print("TOP 10:")
    hdr = (f"  {'E':>4} {'M':>4} {'R':>2}  {'score':>7}  {'ticks':>6}  "
           f"{'gen':>4}  {'min_pop':>3}  {'species':>3}  {'timeout':>7}")
    print(hdr)
    print("  " + "-" * (len(hdr) - 2))
    for r in results[:10]:
        m = r["metrics"]
        to = "YES" if m.get("timed_out") else "no"
        print(f"  {r['max_energy']:>4} {_fmt_speed(r['max_movement_speed']):>4} "
              f"{r['n_registers']:>2}  "
              f"{r['score']:>7.0f}  {m['ticks']:>6}  "
              f"{m['generations']:>4}  {m['min_pop']:>3}  "
              f"{m['species']:>3}  {to:>7}")
        if m.get("best_vm"):
            print(f"  VM: {m['best_vm']}", flush=True)

    print()
    b = best
    print(f"BEST:  E={b['max_energy']}  "
          f"M={_fmt_speed(b['max_movement_speed'])}  "
          f"R={b['n_registers']}  score={b['score']:.0f}")
    if best.get("metrics", {}).get("best_vm"):
        print(f"  VM: {best['metrics']['best_vm']}")

    if args.output:
        with open(args.output, "w") as f:
            json.dump({
                "args": vars(args),
                "total_time": round(total_t, 1),
                "best": best,
                "results": results,
            }, f, indent=2)
        print(f"Saved to {args.output}")


if __name__ == "__main__":
    main()
