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
ENVIRONMENTAL_SOUNDS_ENABLED = True
SOUND_VOLUME = 0.3
SHARED_MEM_ENABLED = False   # experimental: shared memory + symbolic signals (opt-in)
SHARED_MEM_SIZE = 64
FIGHT_OVERLAP_ENABLED = False

WIDTH = 72
HEIGHT = 26
INITIAL_ORGANISMS = 1
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
MAX_AGE = 0  # 0 = use existing per-organism age limit
MIGRATION_INTERVAL = (80, 150)
MIGRATION_BATCH = (3, 8)
TICK_RATE = 0.06
MUTATION_RATE = 0.06
VM_MEM_SIZE = 16

RESOURCE_TYPES = {
    "food":  {"value": 1.5, "symbol": "·", "weight": 0.65},
    "bounty": {"value": 5.5, "symbol": "★", "weight": 0.20},
    "corpse": {"value": 2.0, "symbol": "✿", "weight": 0.15},
}
RESOURCE_KEYS = list(RESOURCE_TYPES.keys())

# RISC-V RV32I helpers (from vmfight.py)
MASK32 = 0xFFFFFFFF

def u32(x):
    return x & MASK32

def s32(x):
    x &= MASK32
    return x if x < 0x80000000 else x - 0x100000000

def sext(v, n):
    v &= (1 << n) - 1
    sign = 1 << (n - 1)
    return (v ^ sign) - sign

def sra32(v, sh):
    return u32(s32(v) >> sh)

FUNCT3 = {"ADDI":0,"SLTI":2,"SLTIU":3,"XORI":4,"ORI":6,"ANDI":7,
          "SLLI":1,"SRLI":5,"SRAI":5,
          "ADD":0,"SUB":0,"SLL":1,"SLT":2,"SLTU":3,"XOR":4,"SRL":5,"SRA":5,"OR":6,"AND":7,
          "BEQ":0,"BNE":1,"BLT":4,"BGE":5,
          "LW":2,"LB":0,"SW":2,"SB":0,
          "JALR":0}
FUNCT7 = {"ADD":0,"SUB":0x20,"SLL":0,"SLT":0,"SLTU":0,"XOR":0,"SRL":0,"SRA":0x20,"OR":0,"AND":0}

def enc_i(imm12, rs1, f3, rd, op):
    return ((imm12&0xFFF)<<20)|(rs1<<15)|(f3<<12)|(rd<<7)|op
def enc_r(f7, rs2, rs1, f3, rd):
    return (f7<<25)|(rs2<<20)|(rs1<<15)|(f3<<12)|(rd<<7)|0x33
def enc_s(imm12, rs2, rs1, f3):
    return ((imm12>>5)<<25)|(rs2<<20)|(rs1<<15)|(f3<<12)|((imm12&0x1f)<<7)|0x23
def enc_b(imm13, rs2, rs1, f3):
    i = imm13&0x1fff
    return ((i>>12)<<31)|(((i>>5)&0x3f)<<25)|(rs2<<20)|(rs1<<15)|(f3<<12)|(((i>>1)&0xf)<<8)|((i>>11)<<7)|0x63
def enc_u(imm20, rd, op):
    return ((imm20<<12))|(rd<<7)|op
def enc_j(imm21, rd):
    i = imm21&0x1fffff
    return (((i>>20)&1)<<31)|(((i>>1)&0x3ff)<<21)|(((i>>11)&1)<<20)|(((i>>12)&0xff)<<12)|(rd<<7)|0x6f

def asm(mn, *a):
    if mn == "ECALL": return 0x00000073
    if mn == "NOP":   return 0x00000013
    if mn == "EBREAK": return 0x00100073
    if mn in ("LUI","AUIPC"): return enc_u(a[1], a[0], 0x37 if mn=="LUI" else 0x17)
    if mn == "JAL":  return enc_j(a[1]*4, a[0])
    if mn in ("BEQ","BNE","BLT","BGE"):
        return enc_b(a[2]*4, a[1], a[0], FUNCT3[mn])
    if mn in ("SW","SB"): return enc_s(a[2], a[1], a[0], FUNCT3[mn])
    if mn in ("LW","LB"): return enc_i(a[2], a[1], FUNCT3[mn], a[0], 0x03)
    if mn in ("ADDI","SLTI","SLTIU","XORI","ORI","ANDI"):
        return enc_i(a[2], a[1], FUNCT3[mn], a[0], 0x13)
    if mn in ("SLLI","SRLI","SRAI"):
        s = a[2]&0x1f | (0x400 if mn=="SRAI" else 0)
        return enc_i(s, a[1], FUNCT3[mn], a[0], 0x13)
    if mn == "JALR": return enc_i(a[2], a[1], 0, a[0], 0x67)
    if mn in ("ADD","SUB","SLL","SLT","SLTU","XOR","SRL","SRA","OR","AND"):
        return enc_r(FUNCT7[mn], a[2], a[1], FUNCT3[mn], a[0])

INSTR_SET = [
    "ADD","SUB","AND","OR","XOR","SLL","SRL","SRA","SLT","SLTU",
    "ADDI","ANDI","ORI","XORI","SLLI","SRLI","SRAI","SLTI","SLTIU",
    "LW","SW","LB","SB",
    "BEQ","BNE","BLT","BGE",
    "JAL","JALR","LUI","AUIPC",
    "ECALL","NOP"
]

# ──────────────────────────────────────────
# RV32 Decoder / Disassembler
# ──────────────────────────────────────────

_RV32_REGS = ["zero","ra","sp","gp","tp","t0","t1","t2","s0","s1","a0","a1","a2","a3","a4","a5",
              "a6","a7","s2","s3","s4","s5","s6","s7","s8","s9","s10","s11","t3","t4","t5","t6"]

def decode_rv32(inst: int) -> dict:
    """Decode a RV32 instruction word into a structured dict.
    Returns {mnemonic, rd, rs1, rs2, imm, opcode, funct3, funct7, raw, raw_str, args_str, is_compressed, ...}
    """
    opcode = inst & 0x7F
    rd = (inst >> 7) & 0x1F
    f3 = (inst >> 12) & 7
    rs1 = (inst >> 15) & 0x1F
    rs2 = (inst >> 20) & 0x1F
    f7 = (inst >> 25) & 0x7F

    def rname(i):
        return _RV32_REGS[i]

    rv = {"raw": inst, "raw_str": f"{inst:08x}", "opcode": opcode, "rd": rd, "rs1": rs1, "rs2": rs2,
          "funct3": f3, "funct7": f7, "imm": 0, "mnemonic": "", "args_str": "",
          "is_compressed": False, "rd_name": rname(rd), "rs1_name": rname(rs1), "rs2_name": rname(rs2)}

    def a(r=None):
        if r: rv["mnemonic"] = r
        return rv

    if opcode == 0x6F:  # JAL
        imm = j_extract(inst)
        rv["imm"], rv["args_str"] = imm, f"{rname(rd)}, {imm}"
        return a("jal")
    if opcode == 0x67:  # JALR
        imm = i_imm(inst)
        rv["imm"], rv["args_str"] = imm, f"{rname(rd)}, {rname(rs1)}, {imm}"
        return a("jalr")
    if opcode == 0x63:  # BRANCH
        imm = b_extract(inst)
        rv["imm"] = imm
        brn = {0: "beq", 1: "bne", 4: "blt", 5: "bge", 6: "bltu", 7: "bgeu"}
        mn = brn.get(f3)
        rv["args_str"] = f"{rname(rs1)}, {rname(rs2)}, {imm}" if mn else ""
        return a(mn)
    if opcode == 0x03:  # LOAD
        imm = i_imm(inst)
        rv["imm"] = imm
        ld = {0: "lb", 1: "lh", 2: "lw", 3: "ld", 4: "lbu", 5: "lhu"}
        mn = ld.get(f3)
        rv["args_str"] = f"{rname(rd)}, {imm}({rname(rs1)})" if mn else ""
        return a(mn)
    if opcode == 0x23:  # STORE
        imm = s_imm(inst)
        rv["imm"] = imm
        st = {0: "sb", 1: "sh", 2: "sw", 3: "sd"}
        mn = st.get(f3)
        rv["args_str"] = f"{rname(rs2)}, {imm}({rname(rs1)})" if mn else ""
        return a(mn)
    if opcode == 0x13:  # OP-IMM
        imm = i_imm(inst)
        rv["imm"] = imm
        sh = imm & 0x1F
        is_srai = (imm >> 10) & 1
        tbl = {0: ("addi", f"{rname(rd)}, {rname(rs1)}, {imm}"),
               1: ("slli", f"{rname(rd)}, {rname(rs1)}, {sh}"),
               2: ("slti", f"{rname(rd)}, {rname(rs1)}, {imm}"),
               3: ("sltiu", f"{rname(rd)}, {rname(rs1)}, {imm}"),
               4: ("xori", f"{rname(rd)}, {rname(rs1)}, {imm}"),
               5: ("srli" if not is_srai else "srai", f"{rname(rd)}, {rname(rs1)}, {sh}"),
               6: ("ori", f"{rname(rd)}, {rname(rs1)}, {imm}"),
               7: ("andi", f"{rname(rd)}, {rname(rs1)}, {imm}")}
        if f3 in tbl:
            mn, args = tbl[f3]
            if f3 == 5: mn = "srai" if is_srai else "srli"
            rv["args_str"] = args
            return a(mn)
        return a()
    if opcode == 0x33:  # OP
        f7_30 = (f7 >> 5) & 1
        tbl = {0: ("add" if not f7_30 else "sub"),
               1: "sll", 2: "slt", 3: "sltu", 4: "xor",
               5: ("srl" if not f7_30 else "sra"),
               6: "or", 7: "and"}
        if f3 in tbl:
            mn = tbl[f3]
            rv["args_str"] = f"{rname(rd)}, {rname(rs1)}, {rname(rs2)}"
            # M-extension: check funct7=1
            if f7 == 1 and f3 <= 7:
                mtbl = {0: "mul", 1: "mulh", 2: "mulhsu", 3: "mulhu",
                        4: "div", 5: "divu", 6: "rem", 7: "remu"}
                return a(mtbl[f3])
            return a(mn)
        return a()
    if opcode == 0x37:  # LUI
        rv["imm"] = inst & 0xFFFFF000
        rv["args_str"] = f"{rname(rd)}, {rv['imm']:#x}"
        return a("lui")
    if opcode == 0x17:  # AUIPC
        rv["imm"] = inst & 0xFFFFF000
        rv["args_str"] = f"{rname(rd)}, {rv['imm']:#x}"
        return a("auipc")
    if opcode == 0x73:  # SYSTEM
        if inst == 0x00000073: return a("ecall")
        if inst == 0x00100073: return a("ebreak")
        if inst == 0x30200073: return a("mret")
        if inst == 0x10500073: return a("wfi")
        csr = inst >> 20
        sys3 = {1: "csrrw", 2: "csrrs", 3: "csrrc", 5: "csrrwi", 6: "csrrsi", 7: "csrrci"}
        if f3 in sys3:
            rv["imm"], rv["args_str"] = csr, f"{rname(rd)}, {csr}, {rname(rs1)}"
            return a(sys3[f3])
        return a("csr")
    if opcode == 0x0F:  # FENCE
        if inst == 0x0ff0000f: return a("fence.i")
        if inst == 0x00000013: return a("nop")
        return a("fence")
    # RV32F / RV32D
    if opcode == 0x53:
        rm = f3; rms = ["rne", "rtz", "rdn", "rup", "rmm", "", "", "dyn"][rm]
        rr = f", {rms}" if rms else ""
        ffloat = {
            0x00: "fadd.s", 0x01: "fadd.d", 0x04: "fsub.s", 0x05: "fsub.d",
            0x08: "fmul.s", 0x09: "fmul.d", 0x0C: "fdiv.s", 0x0D: "fdiv.d",
        }
        if f7 in ffloat:
            rv["args_str"] = f"f{rd}, f{rs1}, f{rs2}{rr}"
            return a(ffloat[f7])
        if f7 == 0x10:
            if f3 == 0: return a("fsgnj.s")
            if f3 == 1: return a("fsgnjn.s")
            if f3 == 2: return a("fsgnjx.s")
        if f7 == 0x14:
            if f3 == 0: rv["args_str"] = f"f{rd}, f{rs1}, f{rs2}"; return a("fmin.s")
            if f3 == 1: rv["args_str"] = f"f{rd}, f{rs1}, f{rs2}"; return a("fmax.s")
        if f7 == 0x50:
            if f3 == 0: rv["args_str"] = f"f{rd}, f{rs1}, f{rs2}"; return a("fle.s")
            if f3 == 1: rv["args_str"] = f"f{rd}, f{rs1}, f{rs2}"; return a("flt.s")
            if f3 == 2: rv["args_str"] = f"f{rd}, f{rs1}, f{rs2}"; return a("feq.s")
        if f7 == 0x20:
            if f3 == 0: rv["args_str"] = f"f{rd}, f{rs1}{rr}"; return a("fcvt.w.s")
            if f3 == 1: rv["args_str"] = f"f{rd}, f{rs1}{rr}"; return a("fcvt.wu.s")
        if f7 == 0x60:
            rv["args_str"] = f"f{rd}, f{rs1}{rr}"; return a("fcvt.s.w")
        if f7 == 0x68:
            rv["args_str"] = f"f{rd}, f{rs1}{rr}"; return a("fcvt.s.wu")
        return a()
    if opcode in (0x07,):  # FLW / FLD
        imm = i_imm(inst)
        rv["imm"] = imm
        if f3 == 2: rv["args_str"] = f"f{rd}, {imm}({rname(rs1)})"; return a("flw")
        if f3 == 3: rv["args_str"] = f"f{rd}, {imm}({rname(rs1)})"; return a("fld")
        return a()
    if opcode in (0x27,):  # FSW / FSD
        imm = s_imm(inst)
        rv["imm"] = imm
        if f3 == 2: rv["args_str"] = f"f{rs2}, {imm}({rname(rs1)})"; return a("fsw")
        if f3 == 3: rv["args_str"] = f"f{rs2}, {imm}({rname(rs1)})"; return a("fsd")
        return a()
    if opcode in (0x2B, 0x2F):  # AMO
        if f3 == 2:
            f5 = (inst >> 27) & 0x1F
            amotbl = {2: "lr.w", 3: "sc.w", 1: "amoswap.w", 0: "amoadd.w", 4: "amoxor.w",
                      12: "amoand.w", 8: "amoor.w", 16: "amomin.w", 20: "amomax.w",
                      24: "amominu.w", 28: "amomaxu.w"}
            if f5 in amotbl:
                mn = amotbl[f5]
                if f5 == 2: rv["args_str"] = f"{rname(rd)}, ({rname(rs1)})"
                else: rv["args_str"] = f"{rname(rd)}, {rname(rs2)}, ({rname(rs1)})"
                return a(mn)
        return a()
    # RV64 word ops (opcodes 0x3B, 0x1B)
    if opcode == 0x3B:
        if f3 == 0 and f7 == 0:
            rv["args_str"] = f"{rname(rd)}, {rname(rs1)}, {rname(rs2)}"
            return a("addw")
        if f3 == 0 and f7 == 0x20:
            rv["args_str"] = f"{rname(rd)}, {rname(rs1)}, {rname(rs2)}"
            return a("subw")
        return a()
    if opcode == 0x1B:
        if f3 == 0:
            rv["args_str"] = f"{rname(rd)}, {rname(rs1)}, {i_imm(inst)}"
            return a("addiw")
        return a()
    return a()  # unknown


def rv32_to_py(inst: int) -> str:
    """Translate a single RV32 instruction to a Python expression.
    Uses r[N] register notation (no ABI names) for Pythonic look.
    """
    d = decode_rv32(inst)
    mn = d["mnemonic"]
    if not mn:
        return f"pass  # 0x{inst:08x}"

    r = lambda i: f"r{i}"
    rd, rs1, rs2 = r(d["rd"]), r(d["rs1"]), r(d["rs2"])
    imm = d["imm"]

    # ── RV32I: ALU-reg ──
    if mn == "add":   return f"{rd} = {rs1} + {rs2}"
    if mn == "sub":   return f"{rd} = {rs1} - {rs2}"
    if mn == "and":   return f"{rd} = {rs1} & {rs2}"
    if mn == "or":    return f"{rd} = {rs1} | {rs2}"
    if mn == "xor":   return f"{rd} = {rs1} ^ {rs2}"
    if mn == "sll":   return f"{rd} = {rs1} << {rs2}"
    if mn == "srl":   return f"{rd} = {rs1} >> {rs2}"
    if mn == "sra":   return f"{rd} = sra({rs1}, {rs2})"
    if mn == "slt":   return f"{rd} = 1 if {rs1} < {rs2} else 0"
    if mn == "sltu":  return f"{rd} = 1 if ({rs1} & 0xFFFFFFFF) < ({rs2} & 0xFFFFFFFF) else 0"

    # ── RV32I: ALU-imm ──
    if mn == "addi":  return "pass" if (d["rd"] | d["rs1"] | imm) == 0 else f"{rd} = {rs1} + {imm}"
    if mn == "andi":  return f"{rd} = {rs1} & {imm}"
    if mn == "ori":   return f"{rd} = {rs1} | {imm}"
    if mn == "xori":  return f"{rd} = {rs1} ^ {imm}"
    if mn == "slli":  return f"{rd} = {rs1} << {imm & 0x1F}"
    if mn == "srli":  return f"{rd} = ({rs1} & 0xFFFFFFFF) >> {imm & 0x1F}"
    if mn == "srai":  return f"{rd} = sra({rs1}, {imm & 0x1F})"
    if mn == "slti":  return f"{rd} = 1 if {rs1} < {imm} else 0"
    if mn == "sltiu": return f"{rd} = 1 if ({rs1} & 0xFFFFFFFF) < ({imm} & 0xFFFFFFFF) else 0"

    # ── RV32I: Load / Store ──
    if mn == "lw":    return f"{rd} = mem[{rs1} + {imm}]"
    if mn == "lbu":   return f"{rd} = mem[{rs1} + {imm}] & 0xFF"
    if mn == "lb":    return f"{rd} = sext(mem[{rs1} + {imm}], 8)"
    if mn == "lh":    return f"{rd} = sext(mem[{rs1} + {imm}], 16)"
    if mn == "lhu":   return f"{rd} = mem[{rs1} + {imm}] & 0xFFFF"
    if mn == "sw":    return f"mem[{rs1} + {imm}] = {rs2}"
    if mn == "sb":    return f"mem[{rs1} + {imm}] = {rs2} & 0xFF"
    if mn == "sh":    return f"mem[{rs1} + {imm}] = {rs2} & 0xFFFF"

    # ── RV32I: Branch ──  (imm is a relative offset)
    if mn == "beq":   return f"if {rs1} == {rs2}: pc += {imm}"
    if mn == "bne":   return f"if {rs1} != {rs2}: pc += {imm}"
    if mn == "blt":   return f"if {rs1} < {rs2}: pc += {imm}"
    if mn == "bge":   return f"if {rs1} >= {rs2}: pc += {imm}"
    if mn == "bltu":  return f"if ({rs1} & 0xFFFFFFFF) < ({rs2} & 0xFFFFFFFF): pc += {imm}"
    if mn == "bgeu":  return f"if ({rs1} & 0xFFFFFFFF) >= ({rs2} & 0xFFFFFFFF): pc += {imm}"

    # ── RV32I: Jump ──
    if mn == "jal":   return f"pc += {imm}" if d["rd"] == 0 else f"{rd} = pc+4; pc += {imm}"
    if mn == "jalr":
        if d["rd"] == 0: return f"pc = {rs1} + {imm}"
        return f"{rd} = pc+4; pc = {rs1} + {imm}"

    # ── RV32I: Upper immediate ──
    if mn == "lui":   return f"{rd} = {imm:#x}"
    if mn == "auipc": return f"{rd} = pc + {imm:#x}"

    # ── RV32I: System ──
    if mn == "nop":   return "pass"
    if mn == "ecall": return "pass  # ecall"
    if mn == "ebreak":return "pass  # ebreak"
    if mn == "mret":  return "pass  # mret"
    if mn == "wfi":   return "pass  # wfi"
    if mn in ("fence", "fence.i"): return "pass  # fence"

    # ── RV32I: CSR ──
    csr_ops = {"csrrw": " = ", "csrrs": " |= ", "csrrc": " &= ~",
               "csrrwi": " = ", "csrrsi": " |= ", "csrrci": " &= ~"}
    if mn in csr_ops:
        op = csr_ops[mn]
        if mn in ("csrrwi", "csrrsi", "csrrci"):
            return f"{rd} = csr[{imm}]; csr[{imm}]{op}{d['rs1']}"
        return f"{rd} = csr[{imm}]; csr[{imm}]{op}{rs1}"
    if mn == "csr": return f"pass  # csr {imm}"

    # ── RV32M ──
    if mn == "mul":   return f"{rd} = {rs1} * {rs2}"
    if mn == "mulh":  return f"{rd} = ({rs1} * {rs2}) >> 32"
    if mn == "mulhsu":return f"{rd} = (s64({rs1}) * ({rs2} & 0xFFFFFFFF)) >> 32"
    if mn == "mulhu": return f"{rd} = (({rs1} & 0xFFFFFFFF) * ({rs2} & 0xFFFFFFFF)) >> 32"
    if mn == "div":   return f"{rd} = {rs1} // {rs2}"
    if mn == "divu":  return f"{rd} = ({rs1} & 0xFFFFFFFF) // ({rs2} & 0xFFFFFFFF)"
    if mn == "rem":   return f"{rd} = {rs1} % {rs2}"
    if mn == "remu":  return f"{rd} = ({rs1} & 0xFFFFFFFF) % ({rs2} & 0xFFFFFFFF)"

    return f"pass  # {mn}"


def disasm_rv32(inst: int, addr: int = 0, show_addr: bool = False) -> str:
    """Disassemble a single RV32 instruction word to a string."""
    d = decode_rv32(inst)
    if not d["mnemonic"]:
        out = f"nop     # 0x{inst:08x}"
    else:
        out = f"{d['mnemonic']:8s} {d['args_str']}".strip()
    if show_addr:
        out = f"{addr:#010x}  {inst:08x}  {out}"
    return out


def rand_inst():
    """Generate a random RV32 instruction."""
    mn = random.choice(INSTR_SET)
    rd = random.randint(0, 31); rs1 = random.randint(0, 31); rs2 = random.randint(0, 31)
    imm = random.randint(-2048, 2047)
    if mn in ("ADD","SUB","AND","OR","XOR","SLL","SRL","SRA","SLT","SLTU"):
        return asm(mn, rd, rs1, rs2)
    if mn in ("ADDI","SLTI","SLTIU","XORI","ORI","ANDI"):
        return asm(mn, rd, rs1, imm)
    if mn in ("SLLI","SRLI","SRAI"):
        return asm(mn, rd, rs1, random.randint(0, 31))
    if mn in ("LW","LB"): return asm(mn, rd, rs1, imm)
    if mn in ("SW","SB"): return asm(mn, rs1, rs2, imm)
    if mn in ("BEQ","BNE","BLT","BGE"):
        return asm(mn, rs1, rs2, random.randint(-16, 15))
    if mn == "JAL":  return asm("JAL", rd, random.randint(-16, 15))
    if mn == "JALR": return asm("JALR", rd, rs1, random.randint(-2048, 2047))
    if mn in ("LUI","AUIPC"): return asm(mn, rd, random.randint(0, 1048575))
    if mn == "ECALL": return 0x00000073
    return 0x00000013
    mn = random.choice(INSTR_SET)
    rd = random.randint(0,31); rs1 = random.randint(0,31); rs2 = random.randint(0,31)
    imm = random.randint(-2048, 2047)
    if mn in ("ADD","SUB","AND","OR","XOR","SLL","SRL","SRA","SLT","SLTU"):
        return asm(mn, rd, rs1, rs2)
    if mn in ("ADDI","SLTI","SLTIU","XORI","ORI","ANDI"):
        return asm(mn, rd, rs1, imm)
    if mn in ("SLLI","SRLI","SRAI"):
        return asm(mn, rd, rs1, random.randint(0,31))
    if mn in ("LW","LB"): return asm(mn, rd, rs1, imm)
    if mn in ("SW","SB"): return asm(mn, rs1, rs2, imm)
    if mn in ("BEQ","BNE","BLT","BGE"):
        return asm(mn, rs1, rs2, random.randint(-16,15))
    if mn == "JAL":  return asm("JAL", rd, random.randint(-16,15))
    if mn == "JALR": return asm("JALR", rd, rs1, random.randint(-2048,2047))
    if mn in ("LUI","AUIPC"): return asm(mn, rd, random.randint(0,1048575))
    if mn == "ECALL": return 0x00000073
    return 0x00000013

def b_extract(inst):
    imm = (((inst>>31)&1)<<12)|(((inst>>25)&0x3f)<<5)|(((inst>>8)&0xf)<<1)|(((inst>>7)&1)<<11)
    return sext(imm, 13)
def j_extract(inst):
    imm = (((inst>>31)&1)<<20)|(((inst>>21)&0x3ff)<<1)|(((inst>>20)&1)<<11)|(((inst>>12)&0xff)<<12)
    return sext(imm, 21)
def i_imm(inst): return sext(inst>>20, 12)
def s_imm(inst): return sext(((inst>>25)<<5)|((inst>>7)&0x1f), 12)

# RV memory map for syntropy
SENSE_BASE = 0x400
ACTION_BASE = 0x500
ACTION_CNT_ADDR = 0x540
BUDGET_ADDR = 0x544
TICK_ADDR = 0x548
SHARED_MEM_BASE = 0x600
SENSE_SCALE = 1000

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
    SYMBOL_ID, SYMBOL_VAL = 23, 24   # typed symbol channel
    FOOD_DIR = 25   # 0=N, 1=E, 2=S, 3=W to nearest food
    ORG_DIR = 26    # 0=N, 1=E, 2=S, 3=W to nearest organism
    FACING = 27     # last direction the organism acted in

class Action:
    MOVE = 0     # move 1 cell in direction (arg % 4): 0=N, 1=E, 2=S, 3=W
    ATTACK = 1   # attack adjacent cell in direction (arg % 4)
    EAT = 2      # eat resource at current position
    REPRODUCE = 3
    REST = 4
    SOUND = 5
    EMIT = 6
    SYMEMIT = 7  # broadcast (symbol_id, value) pair to nearby cells
    HGT = 8      # swap a genome byte with a neighboring organism
    TOTAL = 9

NUM_REGS = 32  # RISC-V RV32I has 32 registers (x0-x31)
NUM_SENSORS = 28
NUM_ACTIONS = 9

DIR_VECS = [(0, -1), (1, 0), (0, 1), (-1, 0)]  # N, E, S, W
DIR_GLYPHS = ["▲", "▶", "▼", "◀"]



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


class RVVMBot:
    """RISC-V RV32I VM used as the organism's genome/brains.

    Memory map:
        0x0000-0x03FF: General RAM (local mem slots 0-15 at 0x0000+slot*4)
        0x0400-0x04FF: Sensor inputs (64 sensors x4 bytes, read-only to host)
        0x0500-0x053F: Action output buffer (16 x4 bytes)
        0x0540:       Action count (byte)
        0x0544:       Remaining budget (scaled int)
        0x0548:       Current tick
        0x0600-0x06FF: Shared memory (64 x4 bytes, if enabled)
    """

    def __init__(self, genome):
        self.genome = list(genome)
        self.r = [0] * 32
        self.pc = 0
        self.running = True
        self.instr_count = 0
        self.mem = bytearray(4096)
        self._senses = {}
        self._budget = 0.0
        self._shared_mem = None

    def clone_mutated(self, rate: float = 0.06) -> 'RVVMBot':
        ng = list(self.genome)
        for i in range(len(ng)):
            if random.random() < rate:
                for _ in range(random.randint(1, 3)):
                    ng[i] ^= (1 << random.randint(0, 31))
        if random.random() < rate * 0.4 and len(ng) < 200:
            ng.insert(random.randrange(0, len(ng) + 1), rand_inst())
        if random.random() < rate * 0.2 and len(ng) > 3:
            ng.pop(random.randrange(len(ng)))
        return RVVMBot(ng)

    def crossover(self, other: 'RVVMBot') -> 'RVVMBot':
        pt = random.randrange(0, min(len(self.genome), len(other.genome)))
        return RVVMBot(self.genome[:pt] + other.genome[pt:])

    def execute(self, budget: float, senses: Dict[int, float], tick: int = 0,
                shared_mem: Optional[bytearray] = None) -> List[Tuple[int, int]]:
        self.r = [0] * 32
        self.pc = 0
        self.running = True
        self.instr_count = 0
        self.mem = bytearray(4096)
        self._senses = senses
        self._budget = budget
        self._shared_mem = shared_mem
        self._action_words: List[Tuple[int, int]] = []

        for sid, val in senses.items():
            addr = SENSE_BASE + sid * 4
            if addr + 4 <= len(self.mem):
                ival = int(val * SENSE_SCALE)
                ival = max(-2**31, min(2**31 - 1, ival))
                self.mem[addr:addr+4] = ival.to_bytes(4, 'little', signed=True)

        self.mem[TICK_ADDR:TICK_ADDR+4] = tick.to_bytes(4, 'little', signed=True)
        budget_int = max(0, min(2**31-1, int(budget * 100)))
        self.mem[BUDGET_ADDR:BUDGET_ADDR+4] = budget_int.to_bytes(4, 'little', signed=True)

        max_instr = max(10, min(200, int(budget * 50)))
        for _ in range(max_instr):
            if not self.running:
                break
            self._step()

        actions = []
        used = set()
        for rv in self.r:
            if rv == 0:
                continue
            act_id = rv & 0xF
            if act_id >= Action.TOTAL:
                continue
            key = (act_id, (rv >> 4) & 0xF)
            if key in used:
                continue
            used.add(key)
            actions.append((act_id, (rv >> 4) & 0xF, 0.0))
            if len(actions) >= 8:
                break

        for i in range(16):
            word = struct.unpack_from('<i', self.mem, ACTION_BASE + i * 4)[0]
            if word == 0:
                continue
            act_id = word & 0xFF
            if act_id >= Action.TOTAL:
                continue
            key = (act_id, (word >> 8) & 0xFF)
            if key in used:
                continue
            used.add(key)
            actions.append((act_id, (word >> 8) & 0xFF, 0.0))

        return actions

    def _step(self):
        if not self.running or self.pc < 0 or self.pc >= len(self.genome) * 4:
            self.running = False
            return

        self.instr_count += 1
        idx = self.pc // 4
        inst = u32(self.genome[idx])
        pc_next = u32(self.pc + 4)

        opcode = inst & 0x7F
        rd = (inst >> 7) & 0x1F
        f3 = (inst >> 12) & 0x7
        rs1 = (inst >> 15) & 0x1F
        rs2 = (inst >> 20) & 0x1F
        f7 = (inst >> 25) & 0x7F
        vrs1 = u32(self.r[rs1])
        vrs2 = u32(self.r[rs2])

        if opcode == 0x73:       # SYSTEM
            if inst == 0x00000073:
                self._ecall()
        elif opcode == 0x6F:     # JAL
            if rd:
                self.r[rd] = u32(pc_next)
            pc_next = u32(self.pc + j_extract(inst))
        elif opcode == 0x67:     # JALR
            if rd:
                self.r[rd] = u32(pc_next)
            pc_next = (vrs1 + i_imm(inst)) & ~1
        elif opcode == 0x63:     # BRANCH
            taken = {0: vrs1 == vrs2, 1: vrs1 != vrs2, 4: s32(vrs1) < s32(vrs2),
                     5: s32(vrs1) >= s32(vrs2),
                     6: vrs1 < vrs2, 7: vrs1 >= vrs2}.get(f3)
            if taken:
                pc_next = u32(self.pc + b_extract(inst))
        elif opcode == 0x03:     # LOAD
            addr = u32(vrs1 + i_imm(inst))
            val = 0
            if addr + 4 <= len(self.mem):
                if f3 == 2:    # LW
                    val = struct.unpack_from('<i', self.mem, addr)[0]
                elif f3 == 1:  # LH
                    val = sext(struct.unpack_from('<h', self.mem, addr)[0], 16)
                elif f3 == 5:  # LHU
                    val = struct.unpack_from('<H', self.mem, addr)[0]
                elif f3 == 4:  # LBU
                    val = self.mem[addr]
                else:          # LB
                    val = sext(self.mem[addr], 8)
            if rd:
                self.r[rd] = u32(val)
        elif opcode == 0x23:     # STORE
            addr = u32(vrs1 + s_imm(inst))
            if addr + 4 <= len(self.mem):
                v = u32(self.r[rs2])
                if f3 == 2:    # SW
                    struct.pack_into('<i', self.mem, addr, s32(v))
                    act_id = v & 0xF
                    if act_id < Action.TOTAL and len(self._action_words) < 16:
                        arg = (v >> 4) & 0xF
                        self._action_words.append((act_id, arg, 0.0))
                elif f3 == 0:  # SB
                    self.mem[addr] = v & 0xFF
                elif f3 == 1:  # SH
                    v16 = v & 0xFFFF
                    struct.pack_into('<h', self.mem, addr, v16 if v16 < 0x8000 else v16 - 0x10000)
        elif opcode == 0x13:     # OP-IMM
            imm = i_imm(inst)
            if rd:
                if f3 == 0:
                    self.r[rd] = u32(vrs1 + imm)
                elif f3 == 1:
                    self.r[rd] = u32(vrs1 << (imm & 0x1F))
                elif f3 == 2:
                    self.r[rd] = 1 if s32(vrs1) < imm else 0
                elif f3 == 3:
                    self.r[rd] = 1 if vrs1 < u32(imm) else 0
                elif f3 == 4:
                    self.r[rd] = u32(vrs1 ^ imm)
                elif f3 == 5:  # SRLI / SRAI
                    sh = imm & 0x1F
                    self.r[rd] = sra32(vrs1, sh) if (imm >> 10) & 1 else u32(vrs1 >> sh)
                elif f3 == 6:
                    self.r[rd] = u32(vrs1 | imm)
                elif f3 == 7:
                    self.r[rd] = u32(vrs1 & imm)
        elif opcode == 0x33:     # OP
            f7_30 = (f7 >> 5) & 1
            if rd:
                if f3 == 0:
                    self.r[rd] = u32(vrs1 - vrs2) if f7_30 else u32(vrs1 + vrs2)
                elif f3 == 1:
                    self.r[rd] = u32(vrs1 << (vrs2 & 0x1F))
                elif f3 == 2:
                    self.r[rd] = 1 if s32(vrs1) < s32(vrs2) else 0
                elif f3 == 3:
                    self.r[rd] = 1 if vrs1 < vrs2 else 0
                elif f3 == 4:
                    self.r[rd] = u32(vrs1 ^ vrs2)
                elif f3 == 5:
                    self.r[rd] = sra32(vrs1, vrs2 & 0x1F) if f7_30 else u32(vrs1 >> (vrs2 & 0x1F))
                elif f3 == 6:
                    self.r[rd] = u32(vrs1 | vrs2)
                elif f3 == 7:
                    self.r[rd] = u32(vrs1 & vrs2)
        elif opcode == 0x37:     # LUI
            if rd:
                self.r[rd] = u32(inst >> 12 << 12)
        elif opcode == 0x17:     # AUIPC
            if rd:
                self.r[rd] = u32(self.pc + (inst >> 12 << 12))

        self.r[0] = 0
        self.pc = pc_next

    def _ecall(self):
        a7 = self.r[17] & 0xFF
        a0 = u32(self.r[10])
        a1 = u32(self.r[11])

        if a7 == 0:  # SENSE
            sid = a0 % NUM_SENSORS
            val = self._senses.get(sid, 0.0)
            self.r[10] = int(val * SENSE_SCALE)
        elif a7 == 1:  # ACT
            act_id = a0 % Action.TOTAL
            arg = int(a1) & 0xFF
            word = (act_id & 0xFF) | ((arg & 0xFF) << 8)
            for i in range(16):
                addr = ACTION_BASE + i * 4
                existing = struct.unpack_from('<i', self.mem, addr)[0]
                if existing == 0:
                    struct.pack_into('<i', self.mem, addr, word)
                    break
        elif a7 == 2:  # ENERGY
            remaining = max(0, self._budget - self.instr_count * 0.01)
            self.r[10] = int(remaining * 1000)
        elif a7 == 3:  # HALT
            self.running = False
        elif a7 == 4:  # RAND
            self.r[10] = random.randint(0, 10000)
        elif a7 == 5:  # TICK
            val = struct.unpack_from('<i', self.mem, TICK_ADDR)[0]
            self.r[10] = val
        elif a7 == 6:  # MLOAD
            slot = a0 % VM_MEM_SIZE
            addr = slot * 4
            val = struct.unpack_from('<i', self.mem, addr)[0]
            self.r[10] = val
        elif a7 == 7:  # MSTORE
            slot = a0 % VM_MEM_SIZE
            addr = slot * 4
            struct.pack_into('<i', self.mem, addr, a1)
        elif a7 == 8:  # GLOAD
            if self._shared_mem is not None:
                slot = a0 % len(self._shared_mem)
                self.r[10] = self._shared_mem[slot]
        elif a7 == 9:  # GSTORE
            if self._shared_mem is not None:
                slot = a0 % len(self._shared_mem)
                self._shared_mem[slot] = max(0, min(255, a1))


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
    vm: RVVMBot
    energy: float
    age: int
    generation: int
    id: int
    weight: float = 1.0
    genome_a: List[int] = field(default_factory=list)
    genome_b: List[int] = field(default_factory=list)
    active_bank: int = 0
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
    parent_id: int = -1
    action_counts: Dict[int, float] = field(default_factory=dict)
    last_regs: List[int] = field(default_factory=lambda: [0] * 32)
    facing: int = 0  # 0=N, 1=E, 2=S, 3=W; last direction acted in

    @property
    def genome(self) -> list:
        return self.genome_a


def _tdist(a: int, b: int, size: int) -> int:
    return abs(a - b)


def _wx(x: int) -> int:
    return max(0, min(WIDTH - 1, x)) if WIDTH else 0


def _wy(y: int) -> int:
    return max(0, min(HEIGHT - 1, y)) if HEIGHT else 0


def _dir_to(dx: int, dy: int) -> int:
    if abs(dx) >= abs(dy):
        return 1 if dx > 0 else 3  # E, W
    else:
        return 2 if dy > 0 else 0  # S, N


_next_id = 0


def _next_oid() -> int:
    global _next_id
    _next_id += 1
    return _next_id


def _mutate_genome(genome: List[int], rate: float = MUTATION_RATE) -> List[int]:
    ng = list(genome)
    for i in range(len(ng)):
        if random.random() < rate:
            for _ in range(random.randint(1, 3)):
                ng[i] ^= (1 << random.randint(0, 31))
    if random.random() < rate * 0.4 and len(ng) < 200:
        ng.insert(random.randrange(0, len(ng) + 1), rand_inst())
    if random.random() < rate * 0.2 and len(ng) > 3:
        ng.pop(random.randrange(len(ng)))
    return ng


def random_genome(length: int = 10) -> List[int]:
    return [rand_inst() for _ in range(length)]


class World:
    def __init__(self):
        self.organisms: List[Organism] = []
        self.resources: Dict[Tuple[int, int], str] = {}
        self.tick = 0
        self.next_id = 0
        self.shift_timer = random.randint(*ENV_SHIFT_INTERVAL)
        self.events: List[str] = []
        self.action_log: List[str] = []
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
        self.shared_mem: Optional[bytearray] = bytearray(SHARED_MEM_SIZE) if SHARED_MEM_ENABLED else None
        self.symbol_buffers: Dict[Tuple[int, int], Dict[int, float]] = {}  # typed symbol channel
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
        if not SOUND_ENABLED or not ENVIRONMENTAL_SOUNDS_ENABLED:
            return
        tone = self.SOUND_TONES.get(event_type)
        if tone:
            freq, dur = tone
            self.mixer.set_stinger(event_type, freq, SOUND_VOLUME, dur)

    def _log_action(self, msg: str):
        self.action_log.append(msg)
        self.action_log = self.action_log[-12:]

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
        self, x: int, y: int, genome: list, energy: float = 3.0, generation: int = 0,
        genome_b: Optional[List[int]] = None, active_bank: int = 0,
        parent_id: int = -1,
        parent_mem: Optional[List[float]] = None,
    ) -> Organism:
        ga = list(genome)
        gb = list(genome_b) if genome_b is not None else list(genome)
        vm = RVVMBot(genome=ga)
        org = Organism(
            x=_wx(x), y=_wy(y),
            vm=vm,
            genome_a=ga,
            genome_b=gb,
            active_bank=active_bank,
            energy=energy,
            age=0,
            generation=generation,
            id=_next_oid(),
            parent_id=parent_id,
            freq_bass=random.uniform(30, 100),
            freq_mid=random.uniform(150, 600),
            freq_treble=random.uniform(700, 4000),
            vol_bass=0.0,
            vol_mid=0.0,
            vol_treble=0.0,
        )
        org.vm.genome = ga
        if parent_mem is not None:
            size = min(len(parent_mem), VM_MEM_SIZE * 4)
            for i in range(size):
                v = parent_mem[i] + random.randint(-2, 2)
                org.vm.mem[i] = max(0, min(255, v))
        self.organisms.append(org)
        self.all_genomes_seen.add(tuple(ga))
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
            senses[Sensor.FOOD_DIR] = float(_dir_to(best_food[0] - org.x, best_food[1] - org.y))
        else:
            senses[Sensor.FOOD_DIST] = 99.0
            senses[Sensor.FOOD_DIR] = 0.0

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
            senses[Sensor.ORG_DIR] = float(_dir_to(best_org.x - org.x, best_org.y - org.y))
        else:
            senses[Sensor.ORG_DIST] = 99.0
            senses[Sensor.ORG_DIR] = 0.0

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
        # Symbol sensors — typed symbol channel (SYMEMIT)
        sym_dict = self.symbol_buffers.get((org.x, org.y), {}) if SHARED_MEM_ENABLED else {}
        if sym_dict:
            best_sym = max(sym_dict, key=lambda s: abs(sym_dict[s]))
            senses[Sensor.SYMBOL_ID] = float(best_sym) / 63.0
            senses[Sensor.SYMBOL_VAL] = max(-1.0, min(1.0, sym_dict[best_sym] / 10.0))
        else:
            senses[Sensor.SYMBOL_ID] = 0.0
            senses[Sensor.SYMBOL_VAL] = 0.0
        senses[Sensor.FACING] = float(org.facing)
        return senses

    def apply_action(self, org: Organism, action_id: int, _arg: int, speed: int = 1):
        dissipation = 1.0 + max(0, org.energy - 5.0) * 0.15
        move_cost = 0.02 * dissipation * speed * org.weight
        weight_burn = 0.01 * speed

        if action_id == Action.MOVE:
            dx, dy = DIR_VECS[_arg % 4]
            org.x = _wx(org.x + dx)
            org.y = _wy(org.y + dy)
            org.energy -= move_cost
            org.weight = max(0.0, org.weight - weight_burn)
            org.facing = _arg % 4
            self._log_action(f"O{org.id} {DIR_GLYPHS[org.facing]}")

        elif action_id == Action.EAT:
            pos = (org.x, org.y)
            if pos in self.resources:
                rtype = self.resources.pop(pos)
                val = RESOURCE_TYPES[rtype]["value"]
                org.energy += val
                org.weight += val * 0.3
                self._log_action(f"O{org.id} +{rtype}")
            else:
                for other in self.organisms:
                    if other is org or other.energy <= 0:
                        continue
                    if other.parent_id == org.id or org.parent_id == other.id:
                        continue
                    if (other.x == org.x and other.y == org.y
                            and other.energy < org.energy * 0.6
                            and org.weight > other.weight):
                        org.energy += other.energy * 0.4
                        org.weight += other.weight * 0.6
                        other.cause_of_death = "predation"
                        other.energy = 0
                        self._log_action(f"O{org.id} ⊗ O{other.id}")
                        break

        elif action_id == Action.ATTACK:
            dx, dy = DIR_VECS[_arg % 4]
            tx, ty = _wx(org.x + dx), _wy(org.y + dy)
            hit = None
            for other in self.organisms:
                if other is org:
                    continue
                if other.parent_id == org.id or org.parent_id == other.id:
                    continue
                if other.x == tx and other.y == ty and org.weight > other.weight:
                    power = max(0.1, org.energy) * 0.3
                    other.energy -= power
                    if other.energy <= 0:
                        other.cause_of_death = "predation"
                        org.energy += other.energy * 0.5
                        org.weight += other.weight * 0.5
                    hit = other
                    break
            org.facing = _arg % 4
            if hit:
                self._log_action(f"O{org.id} ✗ O{hit.id}")

        elif action_id == Action.REPRODUCE:
            if org.energy < ENERGY_COST_PER_CHILD * 0.6:
                return
            neighbors = []
            for dx, dy in [(0, 1), (0, -1), (1, 0), (-1, 0), (1, 1), (1, -1), (-1, 1), (-1, -1)]:
                nx, ny = _wx(org.x + dx), _wy(org.y + dy)
                if not any(o.x == nx and o.y == ny for o in self.organisms):
                    neighbors.append((nx, ny))
            if not neighbors:
                return
            nx, ny = random.choice(neighbors)
            child_g = org.generation + 1

            # Look for a mate on adjacent cells
            mate = None
            for dx, dy in [(0, 1), (0, -1), (1, 0), (-1, 0), (1, 1), (1, -1), (-1, 1), (-1, -1)]:
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
                cross = org.vm.crossover(mate.vm)
                child_ga = _mutate_genome(cross.genome)
                child_gb = _mutate_genome(cross.genome)
                child = self._spawn(nx, ny, child_ga,
                                    genome_b=child_gb,
                                    active_bank=random.randint(0, 1),
                                    energy=2.5, generation=child_g,
                                    parent_id=org.id,
                                    parent_mem=org.vm.mem)
                self._log_action(f"O{org.id} ♥ O{child.id}")
            else:
                cost = ENERGY_COST_PER_CHILD * 0.6
                org.energy -= cost
                child_ga = _mutate_genome(org.genome_a)
                child_gb = _mutate_genome(org.genome_b)
                child = self._spawn(nx, ny, child_ga,
                                    genome_b=child_gb,
                                    active_bank=random.randint(0, 1),
                                    energy=2.0, generation=child_g,
                                    parent_id=org.id,
                                    parent_mem=org.vm.mem)
                if child_g > self.max_gen_ever:
                    self.max_gen_ever = child_g
                    self.events.append(f"Gen {child_g} reached!")
                    self._sound("new_gen")

        elif action_id == Action.REST:
            org.energy += 0.3 / dissipation

        elif action_id == Action.SOUND:
            vol = min(1.0, max(0.0, org.energy / 10.0))
            regs = org.last_regs
            abs_val = lambda v, lo, hi: lo + (abs(v) % (hi - lo))
            org.freq_bass = abs_val(regs[10], 20, 500)
            org.freq_mid = abs_val(regs[11], 100, 2000)
            org.freq_treble = abs_val(regs[12], 500, 8000)
            org.vol_bass = vol * (0.1 + 0.9 * (abs(regs[13]) / 10000.0 if regs[13] else 0.4))
            org.vol_mid = vol * (0.1 + 0.9 * (abs(regs[14]) / 10000.0 if regs[14] else 0.3))
            org.vol_treble = vol * (0.1 + 0.9 * (abs(regs[15]) / 10000.0 if regs[15] else 0.2))
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

        elif action_id == Action.SYMEMIT and SHARED_MEM_ENABLED:
            # upper 2 bits of _arg index the symbol-id register; lower 2 bits index the value register
            sym_reg = (_arg >> 2) % NUM_REGS
            val_reg = _arg % NUM_REGS
            sym_id = int(abs(org.last_regs[sym_reg])) % 64
            val = org.last_regs[val_reg]
            for dx in range(-2, 3):
                for dy in range(-2, 3):
                    tx, ty = _wx(org.x + dx), _wy(org.y + dy)
                    pos = (tx, ty)
                    if pos not in self.symbol_buffers:
                        self.symbol_buffers[pos] = {}
                    self.symbol_buffers[pos][sym_id] = (
                        self.symbol_buffers[pos].get(sym_id, 0.0) + val * 0.5
                    )

        elif action_id == Action.HGT:
            for other in self.organisms:
                if other is org:
                    continue
                if _tdist(other.x, org.x, WIDTH) <= 1 and _tdist(other.y, org.y, HEIGHT) <= 1:
                    idx = _arg % min(len(org.genome), len(other.genome))
                    org.vm.genome[idx], other.vm.genome[idx] = other.vm.genome[idx], org.vm.genome[idx]
                    break

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
        temp_warm = max(0.0, self.temp_diurnal)
        temp_cold = max(0.0, -self.temp_diurnal)
        dl_treb = 1000 + self.daylight * 2000
        season_bass = 35 if self.season == "summer" else 50
        if ENVIRONMENTAL_SOUNDS_ENABLED:
            self.mixer.set_ambient("pressure", 40 + p_dev * 40, p_dev * 0.12, 0, 0, 0, 0)
            self.mixer.set_ambient("moisture", 90 + self.moisture * 80, self.moisture * 0.08,
                                   0, 0, 0, 0)
            self.mixer.set_ambient("temperature", 50, temp_cold * 0.06,
                                   350 + temp_warm * 250, temp_warm * 0.07,
                                   0, 0)
            self.mixer.set_ambient("daylight", 0, 0, 0, 0,
                                   dl_treb, self.daylight * 0.06)
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
            org.vm.genome = org.genome_a if org.active_bank == 0 else org.genome_b
            senses = self.compute_senses(org)
            budget = min(org.energy * 0.4, 8.0)
            actions = org.vm.execute(budget, senses, tick=self.tick, shared_mem=self.shared_mem)
            org.last_regs = org.vm.r.copy()
            org.vm.genome = org.genome_a
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
                    self._log_action(f"O{org.id} wake")
                else:
                    torpid = True
            elif org.energy <= 0.5:
                org.torpor = True
                torpid = True
                self._log_action(f"O{org.id} torpor")
            else:
                torpid = False

            # Sleep cycles
            asleep = False
            if not torpid:
                if org.awake:
                    if org.energy < 0.8 and org.age > 5:
                        org.awake = False
                        org.sleep_timer = random.randint(3, 8)
                        self._log_action(f"O{org.id} sleep")
                else:
                    org.sleep_timer -= 1
                    org.energy += 0.08
                    if org.sleep_timer <= 0 or org.energy > 3.0:
                        org.awake = True
                        self._log_action(f"O{org.id} wake")
            asleep = not org.awake and not torpid

            # Apply VM actions
            if not torpid and not asleep:
                moved_n = 0
                for act_id, arg, _intensity in actions:
                    is_move = act_id == Action.MOVE
                    if is_move and MAX_MOVEMENT_SPEED > 0 and moved_n >= MAX_MOVEMENT_SPEED:
                        continue
                    self.apply_action(org, act_id, arg, speed=moved_n + 1)
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

            # Gene conversion: copy byte from inactive to active bank
            if random.random() < 0.02:
                if org.active_bank == 0:
                    src, dst = org.genome_b, org.genome_a
                else:
                    src, dst = org.genome_a, org.genome_b
                if src and dst:
                    i = random.randrange(min(len(src), len(dst)))
                    dst[i] = src[i]

            # Genome-determined drift when VM produces no movement actions
            if not torpid and not asleep:
                moved = any(
                    a[0] == Action.MOVE
                    for a in actions
                )
                if not moved:
                    dx = (org.genome[0] % 3) - 1 if org.genome else 0
                    dy = (org.genome[min(1, len(org.genome)-1)] % 3) - 1 if len(org.genome) > 1 else 0
                    org.x = _wx(org.x + dx)
                    org.y = _wy(org.y + dy)
                    if dx or dy:
                        org.weight = max(0.0, org.weight - 0.005 * org.weight)

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
            if FIGHT_OVERLAP_ENABLED and not torpid:
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

            # Metabolic cost
            base_cost = SUMMER_BASE_COST if self.season == "summer" else WINTER_BASE_COST
            mult = 0.1 if torpid else 1.0
            if asleep:
                mult = 0.0
            org.energy -= (base_cost + 0.02) * mult
            if not asleep:
                org.weight = max(0.0, org.weight - 0.002 * mult)

            # Starvation or weight loss
            if org.energy <= 0 or org.weight <= 0:
                cause = "disease" if org.id in self.diseased else "starvation" if org.energy <= 0 else "wasting"
                org.cause_of_death = cause
                dead.add(org.id)
                continue

            # Random spontaneous mutation
            if random.random() < MUTATION_RATE * 0.0667:
                i = random.randrange(len(org.genome))
                org.vm.genome[i] ^= (1 << random.randint(0, 31))

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
            if MAX_AGE == 0:
                max_age = 120 + (org.vm.instr_count % 100)
            else:
                max_age = MAX_AGE
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
            if org.energy >= repro_thresh * 1.5 and random.random() < MUTATION_RATE * 0.5:
                target = org.genome_a if random.random() < 0.5 else org.genome_b
                if len(target) < 200 and random.random() < 0.5:
                    target.insert(random.randrange(0, len(target) + 1), rand_inst())

        # Remove dead
        pop_before = len(self.organisms)
        dead_list = []
        kept = []
        for o in self.organisms:
            if o.id in dead:
                dead_list.append(o)
                cause = o.cause_of_death or "unknown"
                self.death_stats[cause] = self.death_stats.get(cause, 0) + 1
                self._log_action(f"O{o.id} ✝ {cause}")
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

        if SHARED_MEM_ENABLED:
            # Symbol buffer decay
            dec_sym: Dict[Tuple[int, int], Dict[int, float]] = {}
            for pos, sym_dict in self.symbol_buffers.items():
                new_d = {s: v * 0.85 for s, v in sym_dict.items() if abs(v * 0.85) > 0.01}
                if new_d:
                    dec_sym[pos] = new_d
            self.symbol_buffers = dec_sym

            # Shared memory slow decay (values persist unless overwritten or allowed to fade)
            for i in range(len(self.shared_mem)):
                if self.shared_mem[i] > 0:
                    self.shared_mem[i] = max(0, self.shared_mem[i] - 1)

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

        # Migration — disabled; no spontaneous generation

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
            color = COLORS[(org.id + org.generation) % len(COLORS)]
            dl = self._daylight_at(org.x, phase)

            glyph = DIR_GLYPHS[org.facing % 4]

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

        # ── sidebar ──
        import shutil
        avail = shutil.get_terminal_size().columns - (WIDTH + 2) - 2

        sb_lines: list[str] = []
        if sentinel and sentinel.generation > 0:
            g = sentinel.genome
            decoded = []
            for i in range(min(len(g), 18)):
                decoded.append(disasm_rv32(g[i]))
            prog = " ; ".join(decoded[:18])
            if avail < len(prog) + 12:
                prog = prog[:max(1, avail - 15)] + "..."
            sb_lines.append(
                f"gen={sentinel.generation} age={sentinel.age} "
                f"⚡={sentinel.energy:.1f} wt={sentinel.weight:.2f} "
                f"len={len(sentinel.genome)} "
                f"bank={'B' if sentinel.active_bank else 'A'}"
            )
            sb_lines.append(f"└vm: {prog}")
            max_py = min(HEIGHT + 2 - len(sb_lines) - 1, 18)
            for i in range(max_py):
                py_line = rv32_to_py(g[i])
                if avail < len(py_line) + 10:
                    py_line = py_line[:max(1, avail - 12)] + "..."
                sb_lines.append(f"└py[{i}]: {py_line}")
            if len(decoded) > max_py:
                sb_lines.append(f"└...({len(decoded)-max_py} more)")

        # ── grid with sidebar on right ──
        lines: list[str] = []
        for y in range(HEIGHT + 2):
            if y == 0:
                row_str = top_bar
            elif y == HEIGHT + 1:
                row_str = bot_bar
            else:
                row_str = f"{BOLD}║{RESET}{''.join(grid[y-1])}{BOLD}║{RESET}"
            if y < len(sb_lines):
                row_str += f"  {sb_lines[y]}"
            lines.append(row_str)

        # ── status lines ──
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
                f"  Pop:{n:4d}  ⚡:{avg_e:.1f}  W:{sum(o.weight for o in self.organisms)/n:.2f}  "
                f"Gen:{max_g:3d}  Age:{avg_age:.1f}  "
                f"Sp:{sp:2d}  H\u2019:{shannon:.2f}  Fos:{self.fossil_count:4d}  "
                f"Res:{len(self.resources):3d}  "
                + (f"Shm:{sum(1 for b in self.shared_mem if b != 0):2d}  " if SHARED_MEM_ENABLED else "")
                + f"{'☀ sum' if self.season == 'summer' else '\u2744 win'}  "
                f"T:{self.tick}  tot:{sum(o.energy+o.fat for o in self.organisms):.0f}/{MAX_SYSTEM_ENERGY:.0f}"
            )

            # Day-night bar
            bw = min(WIDTH, 60)
            step = max(1, WIDTH // bw)
            dn_bar = ""
            for x in range(0, WIDTH, step):
                dl = self._daylight_at(x, phase)
                dn_bar += "░" if dl > 0.6 else ("▒" if dl > 0.3 else "█")
            lines.append(f"  [dn] {dn_bar}")

            # Population sparkline
            if self.pop_history:
                max_pop = max(self.pop_history)
                min_pop = min(self.pop_history)
                span = max_pop - min_pop if max_pop > min_pop else 1
                window = self.pop_history[-min(60, len(self.pop_history)):]
                sparkline = "".join("▁▂▃▄▅▆▇█"[int((p - min_pop) / span * 7)] for p in window)
                lines.append(f"  └{'─' * min(50, len(window))}  {sparkline}")

            # Fitness sparkline
            if len(self.fitness_history) > 5:
                feats = self.fitness_history[-40:]
                mn, mx = min(feats), max(feats)
                rng = mx - mn if mx > mn else 1
                fitline = "".join("▁▂▃▄▅▆▇█"[int((f - mn) / rng * 7)] for f in feats)
                lines.append(f"  └fit              {fitline}")

            # Dominant genome fingerprint
            gc = {}
            for o in self.organisms:
                k = str(o.genome[:6])
                gc[k] = gc.get(k, 0) + 1
            dom = max(gc, key=gc.get) if gc else "?"
            pct = gc.get(dom, 0) / n * 100 if n else 0
            lines.append(f"  dom VM[:6]: {dom} ({pct:.0f}%)")



        # Events
        self.events = self.events[-3:]
        for ev in self.events:
            lines.append(f"  {ev}")

        return "\n".join(lines)


async def main():
    global SOUND_ENABLED, ENVIRONMENTAL_SOUNDS_ENABLED, SOUND_VOLUME, TICK_RATE, MUTATION_RATE, VM_MEM_SIZE, EXTINCTION_LOG_FILE, SEED, WIDTH, HEIGHT, MAX_SYSTEM_ENERGY, MAX_MOVEMENT_SPEED, MAX_AGE, SHARED_MEM_ENABLED, SHARED_MEM_SIZE, INITIAL_ORGANISMS, DAY_LENGTH
    parser = argparse.ArgumentParser(description="VM-genome evolutionary ecosystem")
    parser.add_argument("--volume", type=float, default=SOUND_VOLUME)
    parser.add_argument("--no-sound", action="store_true")
    parser.add_argument("--no-environmental-sounds", action="store_true")
    parser.add_argument("--initial-population", type=int, default=INITIAL_ORGANISMS,
                    help="number of organisms to start with (default: %(default)s)")
    parser.add_argument("--day-length", type=int, default=DAY_LENGTH,
                    help="ticks per full day/night cycle (default: %(default)s)")
    parser.add_argument("--tick-rate", type=float, default=TICK_RATE)
    parser.add_argument("--log", default=EXTINCTION_LOG_FILE)
    parser.add_argument("--seed", type=int, default=SEED)
    parser.add_argument("--width", type=int, default=WIDTH)
    parser.add_argument("--height", type=int, default=HEIGHT)
    parser.add_argument("--max-energy", type=float, default=None)
    parser.add_argument("--max-movement-speed", type=int, default=0,
                    help="max movement actions per tick per organism (0=unlimited)")
    parser.add_argument("--max-age", type=float, default=None,
                    help="max organism age in ticks (0 = unlimited, default = per-organism VM-based limit)")
    parser.add_argument("--continuous", action="store_true")
    parser.add_argument("--shared-memory", action="store_true",
                    help="enable experimental shared memory and symbolic signal features (GLOAD/GSTORE/SYMEMIT)")
    parser.add_argument("--shared-mem-size", type=int, default=SHARED_MEM_SIZE,
                    help="number of shared memory slots (default: %(default)s)")
    parser.add_argument("--mutation-rate", type=float, default=MUTATION_RATE,
                    help="per-byte mutation rate during reproduction (default: %(default)s)")
    parser.add_argument("--vm-memory", type=int, default=VM_MEM_SIZE,
                    help="per-organism VM local memory slots (default: %(default)s)")
    args = parser.parse_args()
    if args.no_sound:
        SOUND_ENABLED = False
    if args.no_environmental_sounds:
        ENVIRONMENTAL_SOUNDS_ENABLED = False
    if args.shared_memory:
        SHARED_MEM_ENABLED = True
    SHARED_MEM_SIZE = max(1, args.shared_mem_size)
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
    if args.max_age is not None:
        MAX_AGE = args.max_age if args.max_age > 0 else float('inf')
    MUTATION_RATE = max(0.0, min(1.0, args.mutation_rate))
    VM_MEM_SIZE = max(1, min(256, args.vm_memory))
    INITIAL_ORGANISMS = max(0, args.initial_population)
    DAY_LENGTH = max(1, args.day_length)
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
                g = best.genome
                decoded = []
                for i in range(min(len(g), 18)):
                    decoded.append(disasm_rv32(g[i]))
                prog = " ; ".join(decoded[:18])
                print(f"  Best VM: gen={best.generation} age={best.age:.0f} "
                      f"⚡={best.energy:.1f} wt={best.weight:.2f} len={len(best.genome)} "
                      f"bank={'B' if best.active_bank else 'A'} ({len(best.genome_b)}b)")
                print(f"  └vm: {prog}")
                if len(decoded) > 18:
                    print(f"  └...({len(decoded)-18} more vm instructions)")

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
