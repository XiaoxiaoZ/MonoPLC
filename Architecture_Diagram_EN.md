# MonoPLC Architecture Diagram and Program Structure

This document outlines the core data flow and event loop architecture based on the actual codebase structure of the `SideEffectPLC` project (including `Control_LOOP`, `SideEffect_LOOP`, and `FB_SimpleLogic`).

## 1. Core Data Flow and Onion Architecture Diagram

The Mermaid diagram below illustrates MonoPLC's single-thread event loop mechanism and how external side-effects are aggressively decoupled from the main logic flow:

```mermaid
flowchart TD
    %% External Environment and Physical Inputs
    subgraph Physical_And_Environment_Layer
        HMI_Input["HMI Soft Button Input"]
        Web_Input["HTTP / REST API Commands"]
        DB_Input["Database Polling / Push"]
        Sensor_Temp["Analog Sensors (Temperature/Pressure)"]
        Sensor_Enc["High-Speed Counters (Encoders/Axis)"]
        Sensor_Vis["Machine Vision Data (X/Y/Angle)"]
        HardwareOut[Physical Hardware Outputs]
        Network[External Networks / IoT]
    end

    %% Side-Effect Processing Loop (Non-blocking / Background Tasks)
    subgraph SideEffect_LOOP
        InputBuilder[Input Action Builder]
        AsyncInputQ[("Async Input Queue (DUT_Effect_Monoid)")]
        AsyncEffectQ[("Async Output Queue (DUT_Effect_Monoid)")]
        IoTExecutor["IoT / Network Async Executor"]
        
        HMI_Input -.->|"Unreliable Soft Buttons"| InputBuilder
        Web_Input -.->|"External REST Clients"| InputBuilder
        DB_Input -.->|"Database Polling / WebHooks"| InputBuilder
        InputBuilder -->|"Assemble into Effect"| AsyncInputQ
        
        AsyncEffectQ --> IoTExecutor
        IoTExecutor -->|"Send Network Requests"| Network
    end

    %% Real-Time Control Loop (Ultra-Fast)
    subgraph Control_LOOP
        EventBus[("Local Event Bus (DUT_Effect_Monoid)")]
        TickGen["System Tick Generator"]
        
        AsyncInputQ -->|"1. Drain to Local Bus"| EventBus
        TickGen -->|"1. Push Environmental Tick"| EventBus
        
        Router{"Event Routing Dispatcher"}
        EventBus -->|"2. Sequentially Pop Current Event"| Router
        
        Router -->|"3a. Hardware Mapping Execution"| HardwareOut
        Router -->|"3b. Dispatch to Async Network Layer"| AsyncEffectQ
        
        %% Core Control Code (Absolutely Safe / Crash-Free)
        subgraph FB_SimpleLogic
            SafeCore((Safe Core Control Logic Never directly triggers external blocking))
            MonoidMerge["Side-Effect Collector (FC_CombineEffects)"]
        end
        
        Sensor_Temp -->|"High-Frequency Real-Time Snapshot (Temp)"| SafeCore
        Sensor_Enc -->|"High-Frequency Real-Time Snapshot (Speed/Pos)"| SafeCore
        Sensor_Vis -->|"High-Frequency Real-Time Snapshot (Vision)"| SafeCore
        Router -->|"3c. Single Business Excitation Event"| SafeCore
        SafeCore -->|"Isolate Error-Prone External Actions"| MonoidMerge
        SafeCore -->|"Express Lane: Direct Physical Output Mapping"| HardwareOut
        MonoidMerge -->|"4. Collect Instructions & Dispatch to Queue"| EventBus
    end

classDef safeCore fill:#e1f5fe,stroke:#03a9f4,stroke-width:2px;
classDef sideEffect fill:#fff3e0,stroke:#ff9800,stroke-width:2px;
classDef hardware fill:#f1f8e9,stroke:#8bc34a,stroke-width:2px;
classDef queues fill:#f3e5f5,stroke:#9c27b0,stroke-width:1px,stroke-dasharray: 5 5;
classDef monoidFeature fill:#fff59d,stroke:#fbc02d,stroke-width:3px,color:#d84315,stroke-dasharray: 5 5;

class SafeCore safeCore;
class InputBuilder,IoTExecutor sideEffect;
class HMI_Input,Web_Input,DB_Input,Sensor_Temp,Sensor_Enc,Sensor_Vis,HardwareOut,Network hardware;

%% Highlight all major pathways involving Monoid data sets
class AsyncInputQ,AsyncEffectQ,EventBus,MonoidMerge monoidFeature;
```

## 2. Architecture Key Concepts

Based on the code structure, the project is divided into three strictly isolated tiers:

1. **Side-Effect Layer (`SideEffect_LOOP`)**:
   - Responsible for dealing with the "dirty" external environment. It converts unpredictable inputs (like HMI Button clicks or HTTP payloads) into standardized `DUT_Effect` structures (via functions like `FC_StartInputEffect`) and pushes them into the **`Async_Input_Queue`**.
   - It also extracts time-consuming network operations (e.g., MQTT publishing, Database writes) from the **`Async_Effect_Queue`** and transmits them calmly in the background. This design **completely eliminates the fatal threat of network latency blocking the PLC's millisecond-level main scan cycle**.

2. **Real-Time Control Loop (`Control_LOOP`)**:
   - This is the ultra-high-frequency loop (e.g., 1ms cycle time). As its first step, it ingests all incoming external events and the locally generated System Tick, consolidating them into the **`Event_Bus_Queue`**.
   - Next, it initiates a robust **`WHILE` Loop Event Dispatcher**. If the popped event demands hardware manipulation, it immediately toggles the physical pins. If it's a network request, it's routed sequentially to the `SideEffect_LOOP` via the async queue. If it represents a business logic trigger (e.g., a button press or the mere passage of time), only then is it fed into the core brain for computation.
   - 💡 **Flexibility Note: Event routing is NOT the only dispatch mechanism!**
     - This example employs a single-threaded event loop architecture (much like the Node.js engine) utilizing an `Event_Bus_Queue`. This is an extremely pure but highly geeky implementation.
     - **Developers are completely free to dismantle this loop.** As long as you catch the `DUT_Effect_Monoid` yielded by your business logic, you can dispatch it using traditional cascading `IF` statements or assign it across different PLC Tasks for relay execution. The Onion Architecture's isolation boundaries remain perfectly intact.

3. **Core Control Code Layer (`FB_SimpleLogic`)**:
   - This serves as the brain for business logic computation. In this project's exemplar, it is abstracted to the highest degree as a **"Pure Function"**.
   - It possesses no timers, maintains no network connections, and is barred from manipulating physical pins directly. It strictly receives the **frozen real-time data** (e.g., temperature, frozen timestamp strings) alongside a **single input event** that triggers its decision-making.
   - The output of its contemplation is a multitude of **"Desired Side-Effect" instruction sets** (collected and bundled using `FC_CombineEffects`).
   - ⚠️ **CRITICAL ARCHITECTURE DECLARATION: Pure Functions are merely an option, NOT a restriction!**
     - The example code employs an aggressively strict Pure Function approach simply to demonstrate **how remarkably high the ceiling for state decoupling can reach** under the MonoPLC framework.
     - **Implementing a Pure Function is absolutely not a prerequisite for this architecture to run.**
     - As long as you physically isolate your "computational logic" from the "error-prone side-effect actions" (like sending network requests or reading/writing files), you are perfectly safe. Even if you maintain traditional nested calls, State Machines (SFC), or retain a few controllable local state variables inside `FB_SimpleLogic`—as long as the final computed outcome is purely a `DUT_Effect_Monoid` data set, this Onion framework will flawlessly shield your main program from being compromised by external environments.
