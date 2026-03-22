# CLAUDE.md

## Project Overview

MonoPLC is a **research demo** exploring how **Monoid algebra** provides a mathematically rigorous isolation boundary between real-time PLC logic and external systems (IoT, LLM, network services). Built natively in TwinCAT 3 (IEC 61131-3 Structured Text) with a Python FastAPI supervisory server, it demonstrates that deterministic control logic can remain pure and side-effect-free while composing with arbitrary external effects through Monoid structures.

**Core thesis:** By treating all effects as elements of a Monoid, PLC logic never directly touches hardware I/O, network calls, or external APIs. Instead, it produces declarative effect descriptors that are combined algebraically — enabling parallel folding, time-travel replay, and cross-layer correctness guarantees via homomorphisms.

## Repository Structure

```
MonoPLC/
├── POUs/                    # Programmable Organization Units (TwinCAT ST)
│   ├── Control_LOOP.TcPOU   #   Main event loop dispatcher (200-element ring buffer)
│   ├── SideEffect_LOOP.TcPOU#   Side effect executor
│   ├── PLCLogic/             #   Deterministic logic blocks
│   │   ├── FB_SimpleLogic.TcPOU    # Temperature control logic
│   │   └── FB_HumidityLogic.TcPOU  # Humidity control logic
│   ├── Monoid/               #   Monoid combination operations
│   │   ├── FC_CombineEffects.TcPOU  # Effect list concatenation
│   │   └── FC_CombineHumidity.TcPOU # Humidity monoid combination
│   ├── Constructors/         #   Effect factory functions (valve, alarm, IoT, etc.)
│   └── Simulation/           #   Plant simulator for testing (FB_PlantSimulator)
├── DUTs/                    # Data Unit Types (structs: DUT_Effect, DUT_Input_Event, etc.)
├── GVLs/                    # Global Variable Lists (event queues, system config)
├── VISUs/                   # Visualization / HMI definitions
├── _Libraries/              # Beckhoff Automation TwinCAT libraries
├── monoplc-server/          # Python FastAPI supervisory server
│   ├── main.py              #   Entry point (FastAPI app with lifespan)
│   ├── config.py            #   Pydantic Settings (env prefix: MONOPLC_)
│   ├── models.py            #   Pydantic models mirroring PLC DUTs
│   ├── monoid.py            #   Monoid protocol + implementations (PLCEffectMonoid, etc.)
│   ├── fold_engine.py       #   Time-slice replay and parallel fold
│   ├── homomorphism.py      #   Structure-preserving map φ: M_PLC → M_MW
│   ├── state_store.py       #   Server-side state aggregator via monoid algebra
│   ├── plc_bridge.py        #   ADS/pyads communication layer
│   ├── routers/             #   API route modules
│   │   ├── effects.py       #     Effect CRUD endpoints
│   │   ├── status.py        #     System status queries
│   │   ├── buttons.py       #     Button/command endpoints
│   │   ├── llm.py           #     LLM decision-making endpoints (Ollama)
│   │   └── algebra.py       #     Algebraic verification endpoints
│   └── tests/               #   Test suite
│       ├── test_monoid_properties.py  # Property-based tests (Hypothesis)
│       ├── test_fold_engine.py        # Fold engine tests
│       ├── conftest.py                # Shared fixtures
│       └── strategies.py             # Hypothesis strategies
├── *.md                     # Documentation (EN + CN bilingual)
└── SideEffectPLC.plcproj    # TwinCAT project file (MSBuild)
```

## Tech Stack

- **PLC Runtime:** TwinCAT 3 XAE (Beckhoff), IEC 61131-3 Structured Text
- **Supervisory Server:** Python 3, FastAPI, Pydantic, uvicorn
- **PLC Communication:** pyads (TwinCAT ADS protocol)
- **LLM Integration:** Ollama (local LLM, default model: llama3)
- **Testing:** pytest, Hypothesis (property-based testing), pytest-asyncio

## Architecture

The system is layered to enforce side-effect isolation:

1. **Pure Logic Layer** — `FB_SimpleLogic`, `FB_HumidityLogic` receive `DUT_Input_Event`, return `DUT_Effect`. No I/O, no blocking calls, fully deterministic.
2. **Monoid Algebra Layer** — `FC_CombineEffects` concatenates effect lists associatively. Effects are declarative data, not imperative actions.
3. **Event Loop** — `Control_LOOP` is a single-threaded ring-buffer dispatcher (200 elements). Effects re-enter the queue for recursive processing.
4. **Side Effect Executor** — `SideEffect_LOOP` is the only place where effects materialize into real I/O.
5. **Python Supervisory Server** — Bridges PLC↔external world via ADS. Implements Monoid homomorphism (φ: M_PLC → M_MW) for cross-layer correctness. Integrates LLM for operator-level decisions.

**Why Monoids matter for PLC isolation:**
- **Associativity** enables parallel folding and time-travel replay (fold any sub-sequence independently)
- **Identity element** provides safe default/empty states
- **Homomorphism** (φ: M_PLC → M_MW) guarantees structural correctness across PLC↔Server boundary
- **External integrations** (IoT, LLM, MQTT) become just another Monoid element — they compose without contaminating PLC logic

## PLC Code Conventions

- **File formats:** `.TcPOU` (program units), `.TcDUT` (data types), `.TcGVL` (global vars), `.TcVMO` (visualization)
- **Naming prefixes:**
  - `FB_` — Function Blocks (stateful, e.g. `FB_SimpleLogic`)
  - `FC_` — Functions (stateless, e.g. `FC_CombineEffects`)
  - `DUT_` — Data Unit Types (structs, e.g. `DUT_Effect`)
  - `GVL` — Global Variable Lists
- **Event queues:** `Async_Effect_Queue` (PLC→Server, 100 elements), `Async_Input_Queue` (Server→PLC, 100 elements)
- **Design principle:** Logic blocks NEVER directly read sensors or write actuators. They only compute state transitions from input events and return effect descriptors.

## Python Server

**Entry point:** `monoplc-server/main.py`

**Configuration:** Environment variables with `MONOPLC_` prefix (see `config.py`):
- `MONOPLC_AMS_NET_ID` — TwinCAT AMS Net ID (default: `199.4.42.250.1.1`)
- `MONOPLC_ADS_PORT` — ADS port (default: `851`)
- `MONOPLC_OLLAMA_URL` — Ollama endpoint (default: `http://localhost:11434`)
- `MONOPLC_OLLAMA_MODEL` — LLM model (default: `llama3`)

**Core modules:**
- `monoid.py` — Abstract Monoid protocol + implementations: `PLCEffectMonoid`, `MWStateMonoid`, `SumMonoid`, `MaxMonoid`, `DictProductMonoid`
- `fold_engine.py` — Time-slice replay, parallel fold with checkpoints
- `homomorphism.py` — Structure-preserving map φ: M_PLC → M_MW
- `state_store.py` — State reconstruction via monoid fold

## Development Commands

```bash
# Run the Python supervisory server
cd monoplc-server && python main.py

# Run all tests
cd monoplc-server && pytest

# Run property-based algebraic verification tests
cd monoplc-server && pytest tests/test_monoid_properties.py -v

# Run fold engine tests
cd monoplc-server && pytest tests/test_fold_engine.py -v
```

**Dependencies:** Install via `pip install -r monoplc-server/requirements.txt`

## Testing Approach

Tests use **Hypothesis** for property-based testing to verify algebraic invariants:
- **Associativity:** `combine(combine(a, b), c) == combine(a, combine(b, c))`
- **Identity:** `combine(a, identity) == a == combine(identity, a)`
- **Homomorphism:** `φ(combine(a, b)) == combine(φ(a), φ(b))`
- **Time-travel correctness:** Fold over any sub-sequence reconstructs valid state

## Conventions

- **Commit messages:** Conventional-style with prefixes: `feat:`, `docs:`, `refactor:`, `chore:`
- **Documentation:** Bilingual (English + Chinese), with `_EN.md` / no suffix for Chinese variants
- **No CI/CD pipeline** currently — testing is manual via pytest
- **`.gitignore`:** Excludes TwinCAT build artifacts (`_Boot/`, `_CompileInfo/`, `.tclrs`), IDE files (`.vs/`), and `.claude/`

## Key Documentation

- `MonoPLC_Architecture_EN.md` — Deep dive into the Monoid-based architecture
- `MonoPLC_LLM_Monoid_Architecture_EN.md` — LLM integration design via Monoids
- `PLC_vs_Modern_Languages_Challenges_EN.md` — Comparison with Rust implementations
- `Architecture_Diagram_EN.md` — Visual architecture diagrams
- `TODO_Server_Architecture.md` — Future roadmap
