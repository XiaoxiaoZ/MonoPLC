# MonoPLC: LLM Integration Architecture & Interface Specification Based on Monoids

In the prior [MonoPLC Architecture](MonoPLC_Architecture_EN.md) document, we explored how to use the mathematical concept of Monoids to decouple "Side Effects" from "Pure Business Logic." 
This document builds upon that foundation, focusing specifically on **how a Large Language Model (LLM) automatically perceives system states**, and **how the MonoPLC architecture leverages Monoid principles to ensure system robustness and high extensibility** upon LLM integration. It also details the communication interfaces and exact code locations for all connecting components.

---

## 1. Overall Collaborative Architecture Diagram

To intuitively grasp the exact relationships between the physical control layer, the intermediary pipeline layer, and the intelligent supervisory layer, please refer to the following architectural data flow diagram:

```mermaid
flowchart TD
    %% Physical Control Layer
    subgraph Layer_PLC ["1. Programmable Logic Controller (TwinCAT PLC) - Real-time Control Layer"]
        direction TB
        SafeCore(("FB_SimpleLogic\n(POUs/PLCLogic/FB_SimpleLogic.TcPOU)"))
        MonoidMerge{"FC_CombineEffects\n(Monoid Folding)"}
        AsyncOut[("Async_Effect_Queue\n(Output Effect Monoid)")]
        AsyncIn[("Async_Input_Queue\n(Legal Effect Input)")]
        Hardware[(Physical Actuators/Sensors)]
        
        Hardware -.->|High-frequency physical mapping| SafeCore
        SafeCore -->|Direct hardware control| Hardware
        SafeCore -->|Yields isolated side-effects| MonoidMerge
        MonoidMerge -->|Associative packaging to Monoid| AsyncOut
        AsyncIn -->|Extract Effect for core logic| SafeCore
    end

    %% Middleware Bus Layer
    subgraph Layer_Server ["2. Bridge Server (Python FastAPI) - Asynchronous Data Bus Layer"]
        direction TB
        PLC_Bridge["plc_bridge.py\n(monoplc-server/plc_bridge.py)"]
        State_Store["state_store.py\n(monoplc-server/state_store.py)"]
        Router_LLM["LLM Router\n(monoplc-server/routers/llm.py)"]
        Router_UI["UI Button Router\n(monoplc-server/routers/buttons.py)"]
        
        PLC_Bridge -->|1. Consume Effect stream| State_Store
        State_Store -.->|2. KV dictionary state slice| Router_LLM
        Router_LLM -->|3. Commands converted to Effects| PLC_Bridge
        Router_UI -->|UI Commands converted to Effects| PLC_Bridge
    end

    %% Intelligent Supervisory Layer
    subgraph Layer_AI ["3. Supervisory & Decision Layer"]
        Ollama["Ollama Inference Engine\n(config.py OLLAMA_URL)"]
        WebUI["Human Operator Frontend\n(static/app.js)"]
        
        Router_LLM <==>|Prompt IN / Effect Intent OUT| Ollama
        Router_UI <==>|HTTP API (Polling)| WebUI
    end

    classDef plcCore fill:#e1f5fe,stroke:#03a9f4,stroke-width:2px;
    classDef serverBus fill:#f3e5f5,stroke:#9c27b0,stroke-width:2px;
    classDef aiBrain fill:#fff3e0,stroke:#ff9800,stroke-width:2px;
    classDef queue fill:#e8f5e9,stroke:#4caf50,stroke-width:2px,stroke-dasharray: 5 5;

    class SafeCore plcCore;
    class State_Store,Router_LLM,Router_UI serverBus;
    class Ollama aiBrain;
    class AsyncOut,AsyncIn queue;

    %% Cross-layer boundary links
    AsyncOut -->|1. AdsSyncRead| PLC_Bridge
    PLC_Bridge -->|2. AdsSyncWrite| AsyncIn
```

---

## 2. How Does the LLM Automatically Retrieve System Information?

In traditional system designs, providing state data to an LLM often requires hardcoding a plethora of application-specific variables (e.g., explicitly reading `PLC.Temperature`). In MonoPLC, this entire process is **generic and automated**, enormously enhancing extensibility.

### 2.1 State Capture Pipeline (StateStore Reconstructor)
The LLM never directly queries the internal state variables of the PLC. Instead, it relies on the `StateStore` within the Bridge Server middleware to reconstruct the state.
1. **Pristine Queue Export**:
   The PLC unifies all behavioral intents and state updates by writing them as `DUT_Effect_Monoid` structures into the `Async_Effect_Queue`.
2. **Generic Key-Value Folding**:
   In `monoplc-server/state_store.py`, the `_poll_loop` function polls to extract these Effects. It overwrites the latest `Payload` and `Value` utilizing the composite primary key `(EType, Target)`, forming a dynamic dictionary. **There is absolutely zero domain coupling here.** Whether handling temperatures, pressures, or newly added machine vision results, as long as they conform to the unified Effect struct, they are automatically logged.

### 2.2 Dynamic Prompt Generation
When an LLM inference request is made, the information is dynamically injected (see `monoplc-server/routers/llm.py`):
```python
def _build_state_prompt(state: dict, user_input: str | None = None) -> str:
    lines = ["Current system state (JSON mapping):"]
    for key, value in state.items():
        val_str = json.dumps(value, ensure_ascii=False) if isinstance(...) else str(value)
        lines.append(f"- {key}: {val_str}")
    # ...
```
Via this mechanism, whenever an `/api/llm/decide` or `/api/llm/chat` request occurs, the LLM beholds a comprehensive, flattened snapshot of system state and recent logs entirely decoupled from the actual business logic, realizing fully automated telemetry capture.

---

## 3. Leveraging Monoids: Ensuring Robustness & Extensibility

Since LLM generation inherently possesses uncertainty (hallucinations), **the LLM must absolutely never act as the control layer**.
MonoPLC, combined with perfect Monoidal algebraic properties, establishes an impenetrable security threshold at the system's core.

### 3.1 Robustness Guaranteed: Whitelists and Effect Interception
The guiding principle of the system is: **The LLM acts strictly as a Supervisory Layer; the PLC's `FB_SimpleLogic` acts as the exclusive real-time control layer.**

By defining strict enums and whitelist interception mechanisms in the middleware (see `monoplc-server/models.py` and `monoplc-server/routers/llm.py`):
1. **Whitelist Mode (`LLM_ALLOWED_EFFECTS`)**:
   The LLM is permitted to issue "Intent" commands such as `EFF_SETPOINT_CHANGE` (modifying preset parameters) or `EFF_IOT_CMD_START`.
2. **Hardware Isolation Interception**:
   The LLM is **strictly prohibited** from outputting operation types that directly manipulate the physical layer, such as `EFF_VALVE_CTRL` (direct actuator control) or `EFF_ALARM`. If the LLM attempts to do so, the `_validate_and_respond()` function blocks it immediately and raises an error.
3. **The Control Safety Net (`FB_SimpleLogic.TcPOU`)**:
   Even if the LLM legally sends an `EFF_SETPOINT_CHANGE` to alter the maximum temperature threshold, the actual determination of whether to shut a cooling valve remains entirely under the jurisdiction of `FB_SimpleLogic`—the pristine deterministic logic module within the PLC. It evaluates actions based on its physical rules and system heartbeat. No matter how many erratic commands external sources attempt to inject, they are simply received as standardized `Input_Event` items without ever threatening the core PLC with memory crashes or deadlocks.

### 3.2 Extensibility Guaranteed: Associativity and Identity
Leveraging the unique **associativity** trait of Monoids, integrating new functionalities **no longer necessitates altering the existing main control loop**.
For example, when introducing a novel LLM analytical capability or adding new soft-buttons:
* Regardless of whether the directive originates from an HMI, IoT subscriptions, or LLM decisions (`routers/llm.py`), it is ultimately wrapped into the exact same `Effect` struct format.
* At the PLC border, these inputs are seamlessly dumped into the cyclic input queue. Finally, utilizing `FC_CombineEffects`, they are flawlessly folded and processed exactly like basic addition. The lines of code in the business module are spared from exploding into a tangled "spiderweb" of conditions as integration dimensions increase.

---

## 4. Component Interfaces and Core Code Locations

To manifest these mechanics, the system defines a set of strictly aligned data structures and communication seams (Interfaces):

### 4.1 Cross-Platform Unified Data Models (Interface Contract)
The sole defining standard for communication is the perfect mirroring of data structures between the PLC and the Python Server.
* **Python Code** in `monoplc-server/models.py` (Utilizing Pydantic models):
  * `class EffectType(IntEnum)` maps exactly to the PLC enum `ENUM_Effect_Type`
  * `class Effect(BaseModel)` maps exactly to `DUT_Effect`
  * `class EffectMonoid(BaseModel)` maps exactly to `DUT_Effect_Monoid`

### 4.2 Component Interaction Matrix

| Interaction Component A | Interaction Component B | Communication Channel & Protocol | Specific Implementation File Path |
| :--- | :--- | :--- | :--- |
| **PLC Business Core** | **Local Event Bus** | Monoid `DUT_Effect` | `POUs/PLCLogic/FB_SimpleLogic.TcPOU` <br/> *(Input `Input_Event: DUT_Effect` / Output `Effects_Out: DUT_Effect_Monoid`)* |
| **Bridge Server** | **TwinCAT PLC** | Ads Read/Write on Cyclic Queue (`Async_Queue`) | `monoplc-server/plc_bridge.py` <br/> *(Exclusive interface: `push_input_effect` & `pop_output_effects`)* |
| **Bridge Server** | **Status Cache** | In-memory structural aggregation: `SystemState` | `monoplc-server/state_store.py` <br/> *(Parses `pop_output_effects` and overwrites RAM)* |
| **User / Frontend** | **Bridge Server** | FastAPI REST Interface (HTTP) | `monoplc-server/routers/buttons.py`<br/>`monoplc-server/routers/llm.py` |
| **Bridge Server** | **Ollama LLM Service**| HTTP Request | `monoplc-server/routers/llm.py` <br/> *(`_call_ollama()` pointing to `/api/generate`)* |

### 4.3 Lifecycle of a Request (Using an LLM Decision as an Example)
1. **State Acquisition**: The user triggers `/api/llm/decide`.
2. **Context Compilation**: Calls `app_state.state_store.get_state()` to retrieve the generic dictionary. Utilizes `_build_state_prompt()` to construct JSON-formatted prompt words fed into the LLM.
3. **Inference & Whitelist Validation**: The LLM provides a response (deserialized into `LLMActionResponse`). The `_validate_and_respond()` function authenticates whether this operation constitutes a non-invasive directive under `LLM_ALLOWED_EFFECTS`.
4. **Command Dispatch**: Calls `plc_bridge.push_input_effect(response.effect)` to encapsulate the side effect intent as a `DUT_Effect`, pushing it into the ADS `GVL.Async_Input_Queue`, making it available for the PLC to safely collect on its subsequent cycle.

Through this elegant loop, Large Language Models are effortlessly fused into a highly reliable standalone industrial control architectures—equipped with **exceptional environmental perception** while governed by **impenetrable system access boundaries**.
