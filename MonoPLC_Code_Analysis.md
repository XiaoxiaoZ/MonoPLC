# MonoPLC 幺半群代数代码分析 / MonoPLC Monoid Algebra Code Analysis

---

## 1. 引言 / Introduction

### 1.1 问题背景 / Problem Background

传统 PLC（Programmable Logic Controller）编程深度依赖过程式范式：逻辑判断与物理 I/O 操作紧密耦合，典型代码模式为：

```
IF Temp > 80 THEN
    TurnOnValve();   // 直接操纵硬件
    SendMQTT(...);   // 阻塞式网络调用
END_IF
```

这种范式在系统规模扩大时暴露出严重问题：

1. **副作用纠缠（Side-Effect Entanglement）**：逻辑条件、物理 I/O 映射、网络通信三者在同一代码块中交织，难以独立测试与复用。
2. **僵尸状态（Zombie States）**：阻塞式网络调用（MQTT、REST）占用毫秒级 PLC 扫描周期，导致看门狗超时和不确定状态。
3. **竞态条件（Race Conditions）**：多任务间共享变量缺乏形式化保证，状态一致性依赖人工审查而非数学证明。

### 1.2 核心命题 / Core Proposition

MonoPLC 提出的核心命题是：**将所有副作用建模为幺半群（Monoid）元素，通过代数组合律实现副作用与确定性逻辑的完全分离**。

这一命题带来以下根本性变化：

- 核心逻辑模块成为**纯函数**：接收事件，返回效果数据结构，不直接操纵任何硬件或网络。
- 副作用的**组合、分发、执行**由统一的代数框架管理，具有可证明的正确性。
- PLC 端与监督层（Python）通过**幺半群同态（Monoid Homomorphism）**桥接，两侧可独立演化。

---

## 2. 幺半群的形式化定义 / Formal Definition of Monoid

### 2.1 数学定义 / Mathematical Definition

一个**幺半群（Monoid）**是一个三元组 $(S, \oplus, \varepsilon)$，其中：

- $S$ 为**载体集合（Carrier Set）**
- $\oplus: S \times S \to S$ 为**二元运算（Binary Operation）**
- $\varepsilon \in S$ 为**单位元（Identity Element）**

且满足以下公理：

1. **结合律（Associativity）**：$\forall a, b, c \in S: (a \oplus b) \oplus c = a \oplus (b \oplus c)$
2. **单位元律（Identity）**：$\forall a \in S: \varepsilon \oplus a = a = a \oplus \varepsilon$

### 2.2 在本项目中的实例化 / Instantiation in This Project

MonoPLC 中存在多个幺半群实例，形成层次化的代数结构：

| 幺半群 / Monoid | 载体 $S$ | 运算 $\oplus$ | 单位元 $\varepsilon$ | 所在层 / Layer |
|---|---|---|---|---|
| $M_{\text{Effect}}$ | `DUT_Effect_Monoid` | 数组拼接 | 空数组 (Count=0) | PLC |
| $M_{\text{Humidity}}$ | `DUT_Humidity_Monoid` | 实数加法 | 0.0 | PLC |
| $M_{\text{Climate}}$ | `DUT_Climate_Monoid` | 分量独立组合 | $(ε_{\text{Effect}}, ε_{\text{Humidity}})$ | PLC |
| $M_{\text{PLC}}$ | `list[Effect]` | 列表连接 | `[]` | Python |
| $M_{\text{MW}}$ | `dict[str, dict]` | 右偏字典合并 | `{}` | Python |
| $M_{\text{Sum}}$ | `int` | 整数加法 | `0` | Python |
| $M_{\text{Max}}$ | `float` | 取最大值 | $-\infty$ | Python |
| $M_{\text{Product}}$ | `dict[str, Any]` | 分量独立组合 | $(ε_1, ε_2, ..., ε_n)$ | Python |

---

## 3. PLC 端幺半群实现 / PLC-Side Monoid Implementation

### 3.1 载体集合 — 效果数据结构 / Carrier Set — Effect Data Structures

#### 3.1.1 最小效果单元 DUT_Effect

`DUT_Effect` 是整个代数体系的**原子元素（Atom）**，表示一个离散的副作用意图：

```iecst
TYPE DUT_Effect :
STRUCT
    EType   : ENUM_Effect_Type := EFF_NONE;
    Target  : STRING[32] := '';   // 目标: 引脚名、文件名或 Topic
    Payload : STRING[64] := '';   // 字符串载荷
    Value   : REAL := 0.0;       // 数值载荷
END_STRUCT
END_TYPE
```

设计要点分析：

- **EType** 决定效果的语义类别，是路由和白名单校验的核心依据。
- **Target + Value + Payload** 构成效果的通用载荷，足以表达阀门控制（`Target="CoolingValve", Value=1.0`）、IoT 发布（`Target="system/status", Payload="..."`）、参数变更（`Target="TempHighLimit", Value=85.0`）等各类副作用。
- 这种最小化设计使得效果结构与具体领域**完全解耦**：增加新的传感器或执行器只需定义新的 `EType` 枚举值，无需修改数据结构。

#### 3.1.2 效果类型枚举 ENUM_Effect_Type

```iecst
TYPE ENUM_Effect_Type :
(
    EFF_NONE           := 0,    // 空操作 / No-op
    EFF_IOT_PUB        := 1,    // IoT 发布 / IoT publish
    EFF_FILE_LOG       := 2,    // 文件日志 / File logging
    EFF_VALVE_CTRL     := 3,    // 阀门控制 / Valve control (PLC-only)
    EFF_ALARM          := 4,    // 报警触发 / Alarm trigger (PLC-only)
    EFF_IOT_CMD_STOP   := 5,    // 停机命令 / System stop
    EFF_IOT_CMD_START  := 6,    // 启动命令 / System start
    EFF_IOT_CMD_RESET  := 7,    // 复位命令 / System reset
    EFF_SYSTEM_TICK    := 8,    // 系统心跳 / System heartbeat
    EFF_SETPOINT_CHANGE := 9,   // 参数变更 / Setpoint change
    EFF_LLM_DECISION   := 10    // LLM 决策 / LLM decision
);
END_TYPE
```

该枚举体现了**层级安全边界**的设计：

- **PLC 控制层专属** (3, 4)：`EFF_VALVE_CTRL` 和 `EFF_ALARM` 仅由 PLC 实时控制逻辑生成，LLM 和外部系统**禁止**直接发出此类效果。
- **跨层可用** (0,1,2,5,6,7,9,10)：可由 PLC、Python 监督层或 LLM 生成。
- **内部信号** (8)：`EFF_SYSTEM_TICK` 是事件循环的心跳驱动，在同态映射 $\varphi$ 中被过滤。

#### 3.1.3 幺半群载体容器 DUT_Effect_Monoid

```iecst
TYPE DUT_Effect_Monoid :
STRUCT
    Count   : INT := 0;
    Effects : ARRAY[1..20] OF DUT_Effect;
END_STRUCT
END_TYPE
```

这是 PLC 端效果幺半群 $M_{\text{Effect}}$ 的**载体集合** $S$。`Count` 字段标记有效元素数量，`Effects[1..20]` 为有界数组。

从代数视角看，`DUT_Effect_Monoid` 等价于**有界列表（Bounded List）**：$S = \{(e_1, e_2, ..., e_k) \mid 0 \le k \le 20, e_i \in \text{DUT\_Effect}\}$。

> **工程妥协**：IEC 61131-3 不支持动态内存分配，因此使用固定大小数组模拟列表。容量上限 20 是实时性与表达力的折中——单个扫描周期内产生超过 20 个效果在实际工业场景中极为罕见。

### 3.2 二元运算 ⊕ — FC_CombineEffects

`FC_CombineEffects` 实现幺半群的二元运算，其语义为**数组拼接（Array Concatenation）**：

```iecst
FUNCTION FC_CombineEffects : DUT_Effect_Monoid
VAR_INPUT
    A : DUT_Effect_Monoid;
    B : DUT_Effect_Monoid;
END_VAR
VAR
    i : INT;
    Result : DUT_Effect_Monoid;
END_VAR
---
Result := A;
FOR i := 1 TO B.Count DO
    IF Result.Count < 20 THEN
        Result.Count := Result.Count + 1;
        Result.Effects[Result.Count] := B.Effects[i];
    ELSE
        EXIT;  // 达到上限，放弃后续追加
    END_IF;
END_FOR;
FC_CombineEffects := Result;
```

**结合律证明**：

设 $A = (a_1, ..., a_m)$, $B = (b_1, ..., b_n)$, $C = (c_1, ..., c_p)$，其中 $m + n + p \le 20$（在容量范围内）。

$$
(A \oplus B) \oplus C = (a_1, ..., a_m, b_1, ..., b_n) \oplus (c_1, ..., c_p) = (a_1, ..., a_m, b_1, ..., b_n, c_1, ..., c_p)
$$

$$
A \oplus (B \oplus C) = (a_1, ..., a_m) \oplus (b_1, ..., b_n, c_1, ..., c_p) = (a_1, ..., a_m, b_1, ..., b_n, c_1, ..., c_p)
$$

两者相等，结合律成立。$\square$

> **注意**：当总元素数超过 20 时，拼接操作会截断（丢弃溢出元素）。严格来说，这使得在溢出情况下结合律可能不成立。然而在实际运行中，单周期内效果数量远低于 20，此约束在工程实践中等价于数学上的无界列表幺半群。

### 3.3 单位元 ε — FC_EmptyEffect

```iecst
FUNCTION FC_EmptyEffect : DUT_Effect_Monoid
VAR
    Empty : DUT_Effect_Monoid;  // ST 默认初始化: Count=0, 所有 Effects 为零值
END_VAR
---
FC_EmptyEffect := Empty;
```

由于 Structured Text 的结构体默认初始化所有字段为零值，`Empty` 的 `Count = 0` 即表示**空列表**。

**单位元验证**：

- **左单位元**：$\varepsilon \oplus A$：由于 $\varepsilon.\text{Count} = 0$，循环体不执行，`Result := A` 直接返回 $A$。
- **右单位元**：$A \oplus \varepsilon$：`Result := A`，循环从 1 到 $B.\text{Count} = 0$ 不执行，返回 $A$。

因此 $\varepsilon \oplus A = A = A \oplus \varepsilon$。$\square$

### 3.4 积幺半群 / Product Monoid — DUT_Climate_Monoid

`DUT_Climate_Monoid` 是 $M_{\text{Effect}}$ 与 $M_{\text{Humidity}}$ 的**积幺半群（Product Monoid）**：

```iecst
// 典型的 Product Monoid, 将自由幺半群 (Effect) 和加法幺半群 (Humidity) 结合
TYPE DUT_Climate_Monoid :
STRUCT
    Temp : DUT_Effect_Monoid;       // 温度效果幺半群分量
    Hum  : DUT_Humidity_Monoid;     // 湿度加法幺半群分量
END_STRUCT
END_TYPE
```

其中湿度幺半群 $M_{\text{Humidity}} = (\mathbb{R}, +, 0)$ 是经典的**加法幺半群**：

```iecst
TYPE DUT_Humidity_Monoid :
STRUCT
    SprayAmount : REAL := 0.0;  // 累计喷洒量 (ml)
END_STRUCT
END_TYPE
```

积幺半群的组合运算 `FC_CombineClimate` 对每个分量**独立**地应用各自的二元运算：

```iecst
// Product Monoid 核心: 对子 Monoid 独立且同步地结合
Result.Temp := FC_CombineEffects(A.Temp, B.Temp);
Result.Hum  := FC_CombineHumidity(A.Hum, B.Hum);
FC_CombineClimate := Result;
```

湿度分量的组合运算为简单加法：

```iecst
Result.SprayAmount := A.SprayAmount + B.SprayAmount;
FC_CombineHumidity := Result;
```

**积幺半群定理**：若 $(S_1, \oplus_1, \varepsilon_1)$ 和 $(S_2, \oplus_2, \varepsilon_2)$ 均为幺半群，则 $(S_1 \times S_2, \oplus, (\varepsilon_1, \varepsilon_2))$ 也是幺半群，其中 $(a_1, a_2) \oplus (b_1, b_2) = (a_1 \oplus_1 b_1, a_2 \oplus_2 b_2)$。

该定理保证了 `DUT_Climate_Monoid` 自动继承结合律和单位元性质，无需额外证明。

---

## 4. 事件循环中的幺半群折叠 / Monoidal Fold in Event Loop

### 4.1 Control_LOOP 的代数视角 / Algebraic View of Control_LOOP

`Control_LOOP` 是整个系统的**事件驱动调度器**，其执行流程可以用幺半群折叠（Fold）来描述。

**阶段 1: 外部事件采集**

从异步输入队列（Python 监督层、HMI 按钮）中将效果注入本地事件总线，并生成系统心跳事件：

```iecst
// 将异步队列里的按键/外部网络命令，抽入本地事件总线
WHILE gvl.Async_Input_Head <> gvl.Async_Input_Tail DO
    Next_Head := (Q_Head + 1) MOD 200;
    IF Next_Head <> Q_Tail THEN
        Event_Bus_Queue[Q_Head] := gvl.Async_Input_Queue[gvl.Async_Input_Tail];
        Q_Head := Next_Head;
    END_IF
    gvl.Async_Input_Tail := (gvl.Async_Input_Tail + 1) MOD 100;
END_WHILE;

// 生成系统心跳事件 (The System Tick)
Temp_Monoid := FC_TickInputEffect('System_Timer');
Event_Bus_Queue[Q_Head] := Temp_Monoid.Effects[1];
```

**阶段 2: 事件循环与幺半群折叠**

事件总线中的每个事件被逐一弹出，经过路由后送入纯函数处理，输出的效果通过幺半群组合聚合：

```iecst
WHILE Q_Tail <> Q_Head DO
    Current_Event := Event_Bus_Queue[Q_Tail];
    Q_Tail := (Q_Tail + 1) MOD 200;

    // 路由：物理映射与通讯抛出
    IF Current_Event.EType = EFF_VALVE_CTRL OR ... THEN
        // 推送到异步输出队列 (供 Python 监督层消费)
        gvl.Async_Effect_Queue[gvl.Async_Queue_Head] := Current_Event;
    END_IF

    // 路由：业务逻辑计算
    IF Current_Event.EType = EFF_SYSTEM_TICK OR ... THEN
        Inst_Logic(Current_Temp := GVL.Net_Temperature, ...);
        Inst_HumLogic(Input_Event := Current_Event);

        // 积幺半群组合
        Local_Climate.Temp := Inst_Logic.Effects_Out;
        Local_Climate.Hum  := Inst_HumLogic.Effects_Out;
        Global_Climate := FC_CombineClimate(Global_Climate, Local_Climate);

        // ★ 事件递归：生成的效果回填总线
        FOR i := 1 TO Total_Monoid.Count DO
            Event_Bus_Queue[Q_Head] := Total_Monoid.Effects[i];
            Q_Head := Next_Head;
        END_FOR;
    END_IF
END_WHILE;
```

从代数视角，每个扫描周期执行的计算等价于：

$$
\text{State}_{n+1} = \text{State}_n \oplus \bigoplus_{i=1}^{k} f(e_i)
$$

其中 $f: \text{DUT\_Effect} \to M_{\text{Climate}}$ 是纯函数（`FB_SimpleLogic` + `FB_HumidityLogic`）的组合，$\bigoplus$ 表示通过 `FC_CombineClimate` 的多次折叠。

### 4.2 事件递归的代数闭包 / Event Recursion as Algebraic Closure

`Control_LOOP` 最具创新性的设计是**事件递归（Event Recursion）**：纯函数输出的效果不直接执行，而是重新注入事件总线等待下一轮调度。

```
Event_Bus_Queue ← [e₁, e₂, ..., eₙ]
                     ↓ pop e₁
              f(e₁) = [e'₁, e'₂]    (纯函数生成新效果)
                     ↓ push back
Event_Bus_Queue ← [e₂, ..., eₙ, e'₁, e'₂]
```

这形成了一个**代数闭包**：所有效果（无论来自外部输入还是内部逻辑生成）都在同一个幺半群框架内被统一处理。这保证了：

1. **处理顺序的确定性**：FIFO 队列保证因果顺序。
2. **单线程安全性**：整个循环在一个 PLC 扫描周期内完成，无需锁或互斥。
3. **可扩展性**：新增效果类型只需在路由表中添加条目，核心循环逻辑不变。

### 4.3 纯函数 FB_SimpleLogic 的幺半群语义 / Monoidal Semantics of FB_SimpleLogic

`FB_SimpleLogic` 是温度控制的核心逻辑块，其接口体现了纯函数的设计原则：

- **输入**：当前温度（冻结快照）、时间戳、单一事件
- **输出**：`DUT_Effect_Monoid`（效果幺半群元素）
- **无直接 I/O**：不读取硬件传感器，不操纵任何物理设备

其内部采用**模式匹配（Pattern Matching）**对事件类型进行分派，每个分支通过效果构造函数生成效果，最终通过 `FC_CombineEffects` 组合：

```iecst
// 初始化空 Monoid（单位元）
Effects_Out := FC_EmptyEffect();

// 模式匹配
IF Input_Event.EType = EFF_IOT_CMD_STOP THEN
    Eff_Valve   := FC_ValveEffect('CoolingValve', FALSE);
    Eff_Network := FC_IoTEffect('system/status', Final_Payload);
    Effects_Out := FC_CombineEffects(Eff_Valve, Eff_Network);

ELSIF Input_Event.EType = EFF_SYSTEM_TICK THEN
    IF System_En THEN
        // 迟滞控制逻辑 (Hysteresis)
        IF Current_Temp < TempLowLimit AND Is_Cooling THEN
            Eff_Valve   := FC_ValveEffect('CoolingValve', FALSE);
            Eff_Network := FC_IoTEffect('system/status', ...);
            Effects_Out := FC_CombineEffects(Eff_Valve, Eff_Network);
        ELSIF Current_Temp > TempHighLimit AND NOT Is_Cooling THEN
            Eff_Valve   := FC_ValveEffect('CoolingValve', TRUE);
            ...
        END_IF;

        // 心跳：周期性状态快照
        IF Tick_Counter >= HEARTBEAT_INTERVAL THEN
            Eff_Network := FC_IoTEffect('system/status', ...);
            Effects_Out := FC_CombineEffects(Effects_Out, Eff_Network);
        END_IF;
    END_IF;
END_IF;
```

从代数语义看，`FB_SimpleLogic` 是一个**参数化的幺半群态射（Parameterized Monoid Morphism）**：

$$
f_{\text{logic}}: \text{DUT\_Effect} \times \text{State} \to M_{\text{Effect}}
$$

每次调用产生一个幺半群元素，由调用者负责折叠。这种设计使得逻辑模块**可独立测试**：给定输入事件和状态快照，验证输出的幺半群元素是否正确，完全不依赖物理硬件。

---

## 5. Python 端幺半群体系 / Python-Side Monoid System

Python 监督层定义了一组**独立设计（Independently Designed）**的幺半群，通过同态映射与 PLC 端桥接。

### 5.1 M_PLC — PLCEffectMonoid

```python
class PLCEffectMonoid:
    """M_PLC = (list[Effect], concat, [])"""
    def empty(self) -> list[Effect]:
        return []
    def combine(self, a: list[Effect], b: list[Effect]) -> list[Effect]:
        return a + b
```

$M_{\text{PLC}} = (\text{list}[\text{Effect}], +\!+, [\ ])$

这是 PLC 端 `DUT_Effect_Monoid` 的 Python 端**同构映射**：载体集合为 Python 列表（无容量限制），运算为列表连接，单位元为空列表。

### 5.2 M_MW — MWStateMonoid

```python
class MWStateMonoid:
    """M_MW = (dict[str, dict[str, Any]], right-biased merge, {})"""
    def empty(self) -> dict:
        return {}
    def combine(self, a: dict, b: dict) -> dict:
        return {**a, **b}
```

$M_{\text{MW}} = (\text{dict}, \text{merge}_R, \{\})$

这是专为中间件层设计的幺半群，其语义为**右偏字典合并（Right-Biased Dict Merge）**：键冲突时后者覆盖前者（last-writer-wins）。

**结合律证明**：

对于字典合并运算 $\{**a, **b\}$：

$$
\{**(\{**a, **b\}), **c\} = \{**a, **b, **c\}
$$

$$
\{**a, **(\{**b, **c\})\} = \{**a, **b, **c\}
$$

Python 字典展开的语义保证两者等价（对于相同键，最终取最右侧的值）。$\square$

> **设计独立性**：$M_{\text{MW}}$ 的语义（字典合并）与 $M_{\text{PLC}}$ 的语义（列表连接）完全不同。两个幺半群各自针对其运行时约束优化——PLC 端优化为固定大小数组操作，Python 端优化为快速键值查找。它们之间的一致性由同态 $\varphi$ 保证。

### 5.3 辅助幺半群 / Auxiliary Monoids

```python
class SumMonoid:
    """M_Sum = (int, +, 0) — 用于计数聚合"""
    def empty(self) -> int: return 0
    def combine(self, a: int, b: int) -> int: return a + b

class MaxMonoid:
    """M_Max = (float, max, -∞) — 用于追踪峰值"""
    def empty(self) -> float: return float('-inf')
    def combine(self, a: float, b: float) -> float: return max(a, b)
```

这些基础幺半群作为积幺半群的**分量（Components）**，提供不同维度的聚合能力。

### 5.4 DictProductMonoid — 通用积幺半群 / Generic Product Monoid

```python
class DictProductMonoid:
    """
    Given Monoids M₁, M₂, ..., Mₙ, the Product Monoid is
    component-wise composition.
    """
    def __init__(self, components: list[MonoidComponent]):
        self.components = components

    def empty(self) -> dict[str, Any]:
        return {c.name: c.monoid.empty() for c in self.components}

    def combine(self, a: dict, b: dict) -> dict:
        return {c.name: c.monoid.combine(a[c.name], b[c.name])
                for c in self.components}

    def map_effect(self, effect: Effect) -> dict[str, Any]:
        return {c.name: c.map_fn(effect) for c in self.components}
```

实际注册的分量：

```python
PRODUCT_COMPONENTS = [
    MonoidComponent(name="state",          monoid=MWStateMonoid(), map_fn=map_state),
    MonoidComponent(name="effect_count",   monoid=SumMonoid(),     map_fn=map_count),
    MonoidComponent(name="alarm_count",    monoid=SumMonoid(),     map_fn=map_alarm),
    MonoidComponent(name="total_spray_ml", monoid=SumMonoid(),     map_fn=map_spray),
]
```

**零修改扩展性**：新增一个监控维度（如"最高温度"）只需在 `PRODUCT_COMPONENTS` 中添加一行：

```python
MonoidComponent(name="peak_temp", monoid=MaxMonoid(), map_fn=map_peak_temp)
```

由于积幺半群定理，所有下游算法（并行折叠、时间旅行、检查点、API 端点）**自动继承**新维度，无需任何代码修改。这是幺半群代数结构带来的核心工程优势之一。

---

## 6. 幺半群同态 / Monoid Homomorphism φ

### 6.1 形式化定义 / Formal Definition

**幺半群同态（Monoid Homomorphism）** $\varphi: M_1 \to M_2$ 是载体集合之间的映射，满足：

1. **运算保持（Operation Preservation）**：$\varphi(a \oplus_1 b) = \varphi(a) \oplus_2 \varphi(b)$
2. **单位元保持（Identity Preservation）**：$\varphi(\varepsilon_1) = \varepsilon_2$

在 MonoPLC 中，同态 $\varphi$ 桥接 PLC 端和 Python 端：

$$
\varphi: M_{\text{PLC}} \to M_{\text{MW}}
$$

$$
\varphi: (\text{list}[\text{Effect}], +\!+, [\ ]) \to (\text{dict}, \text{merge}_R, \{\})
$$

### 6.2 具体实现 — phi() 函数 / Concrete Implementation

```python
def phi(effects: list[Effect]) -> dict[str, dict[str, Any]]:
    """
    Monoid homomorphism: maps a PLC-side effect list (M_PLC carrier)
    to a middleware-side state dict (M_MW carrier).

    Homomorphism law:
        phi(plc.combine(a, b)) == mw.combine(phi(a), phi(b))
    """
    result: dict = {}
    for effect in effects:
        # 过滤噪声信号
        if effect.e_type in (EffectType.EFF_SYSTEM_TICK, EffectType.EFF_NONE):
            continue
        # 复合键生成: "EFF_VALVE_CTRL.CoolingValve"
        composite_key = (
            f"{effect.e_type.name}.{effect.target}" if effect.target
            else effect.e_type.name
        )
        # Last-writer-wins: 后出现的效果覆盖先前的
        result[composite_key] = {
            "value": effect.value,
            "payload": effect.payload,
        }
    return result
```

$\varphi$ 的核心逻辑：

1. **遍历**效果列表中的每个效果
2. **过滤**噪声信号（`TICK`, `NONE`）
3. **生成复合键** `"类型.目标"` 作为字典键
4. **Right-biased 覆盖**：同一键的后续效果覆盖先前值

**同态保持性证明草案**：

设 $A = [a_1, ..., a_m]$，$B = [b_1, ..., b_n]$。

- **左侧**：$\varphi(A \oplus_{\text{PLC}} B) = \varphi([a_1, ..., a_m, b_1, ..., b_n])$。遍历拼接后的列表，对每个效果生成键值对，后者覆盖前者。
- **右侧**：$\varphi(A) \oplus_{\text{MW}} \varphi(B) = \{**\varphi(A), **\varphi(B)\}$。先独立映射两个列表，再执行字典合并（$B$ 的键覆盖 $A$ 的键）。

关键观察：在拼接列表 $A +\!+ B$ 中，$B$ 的元素出现在 $A$ 之后。由于 $\varphi$ 的 last-writer-wins 语义，对于任何键 $k$：

- 若 $k$ 仅出现在 $A$ 中：两侧结果相同。
- 若 $k$ 仅出现在 $B$ 中：两侧结果相同。
- 若 $k$ 同时出现在 $A$ 和 $B$ 中：左侧取 $B$ 中最后出现的值（因为 $B$ 在拼接列表末尾）；右侧取 $\varphi(B)$ 中的值（字典合并时 $B$ 覆盖 $A$）。两者相等。

因此 $\varphi(A \oplus_1 B) = \varphi(A) \oplus_2 \varphi(B)$ 成立。$\square$

### 6.3 MonoidHomomorphism 泛型框架 / Generic Framework

```python
class MonoidHomomorphism(Generic[A, B]):
    """
    Generic Monoid Homomorphism: given two independently designed Monoids
    and a mapping φ, automatically provides a verified cross-layer pipeline.
    """
    def __init__(self, source: Monoid[A], target: Monoid[B], phi: Callable[[A], B]):
        self.source = source
        self.target = target
        self.phi = phi

    def transport(self, value: A) -> B:
        """φ(value)"""
        return self.phi(value)

    def transport_combine(self, values: list[A]) -> B:
        """Combine in source, then map via φ."""
        source_combined = reduce(self.source.combine, values, self.source.empty())
        return self.phi(source_combined)

    def verify(self, a: A, b: A) -> HomomorphismVerification:
        """Verify: φ(a ⊕₁ b) == φ(a) ⊕₂ φ(b)"""
        lhs = self.phi(self.source.combine(a, b))
        rhs = self.target.combine(self.phi(a), self.phi(b))
        return HomomorphismVerification(lhs=lhs, rhs=rhs, holds=(lhs == rhs))
```

三种核心操作：

| 操作 / Operation | 含义 / Semantics | 用途 / Use Case |
|---|---|---|
| `transport(value)` | $\varphi(\text{value})$ | 将 PLC 效果列表映射为 MW 状态字典 |
| `transport_combine(values)` | $\varphi(\bigoplus_1 \text{values})$ | 先在源端组合，再映射 |
| `verify(a, b)` | 检验 $\varphi(a \oplus_1 b) = \varphi(a) \oplus_2 \varphi(b)$ | 跨层正确性验证 |

### 6.4 同态的工程意义 / Engineering Significance of Homomorphism

同态 $\varphi$ 带来的核心工程价值：

1. **独立演化（Independent Evolution）**：PLC 端可以修改 `FC_CombineEffects` 的实现细节（如优化数组操作），Python 端可以修改状态字典的聚合策略——只要 $\varphi$ 的保持性质不变，跨层一致性自动保证。

2. **验证简化（Verification Simplification）**：传统系统需要端到端集成测试来验证 PLC 和上位机的数据一致性。而在 MonoPLC 中，只需验证 $\varphi$ 的保持性质——这是一个**有限的代数检查**，可通过 Property-Based Testing 自动化。

3. **路径等价（Path Equivalence）**：对于任何效果序列，以下两条路径产生相同结果：
   - **路径 1**：在 PLC 端组合所有效果，然后映射到 Python 端
   - **路径 2**：逐个将效果映射到 Python 端，然后在 Python 端组合

   这意味着即使网络传输导致效果到达顺序与 PLC 端不同（只要最终序列相同），状态重建结果也是一致的。

---

## 7. 代数折叠与时间旅行 / Algebraic Fold & Time-Travel

### 7.1 状态重建的代数基础 / Algebraic Foundation of State Reconstruction

`StateStore` 通过持续消费 PLC 输出的效果流来重建系统状态。其核心操作是**幺半群折叠（Monoidal Fold）**：

$$
\text{state}(t) = \bigoplus_{\text{MW}} \left[ \varphi_{\text{product}}(e_i) \mid e_i \in \text{effects}[0..t] \right]
$$

其中 $\varphi_{\text{product}}$ 是积幺半群的映射函数，将每个效果同时映射到所有分量：

```python
def _consume_effect(self, effect: Effect) -> None:
    """state = state ⊕_mw φ([effect])"""
    mapped = self._product_monoid.map_effect(effect)
    self._state_data = self._product_monoid.combine(self._state_data, mapped)
```

每消费一个效果，执行一次积幺半群的组合运算。由于结合律，这等价于对整个效果历史的完整折叠。

### 7.2 并行折叠的正确性 / Correctness of Parallel Fold

`FoldEngine` 利用结合律实现**并行折叠**：

```python
def fold_parallel(self, effects: list[Effect], n_workers: int = 4) -> Any:
    """
    Parallel fold exploiting Monoid associativity.
    Produces **identical** results to sequential fold — guaranteed by:
        (a ⊕ b) ⊕ c = a ⊕ (b ⊕ c)
    """
    chunks = self._split(effects, n_workers)
    with ThreadPoolExecutor(max_workers=n_workers) as executor:
        partial_results = list(executor.map(self.fold, chunks))
    return reduce(self._monoid.combine, partial_results, self._monoid.empty())
```

**正确性保证**：设效果序列 $E = [e_1, ..., e_n]$，分为 $k$ 块 $C_1, ..., C_k$。

$$
\text{fold}(E) = \bigoplus_{i=1}^{n} \varphi(e_i) = \underbrace{\bigoplus_{e \in C_1} \varphi(e)}_{\text{fold}(C_1)} \oplus \underbrace{\bigoplus_{e \in C_2} \varphi(e)}_{\text{fold}(C_2)} \oplus \cdots \oplus \underbrace{\bigoplus_{e \in C_k} \varphi(e)}_{\text{fold}(C_k)}
$$

结合律保证此等式成立，因此各分块可在不同线程上独立计算后合并。

> **与传统回调队列的对比**：一个使用任意回调函数的事件队列**无法**安全地进行并行处理，因为回调可能依赖隐藏的状态。幺半群的结合律是一个**数学定律**，而非需要测试验证的假设。

### 7.3 时间旅行 — state_at(t) / Time-Travel

检查点（Checkpoint）是效果历史中某个时刻的状态快照。结合律允许从检查点开始增量重建，而非从头折叠：

$$
\text{state\_at}(t) = \text{checkpoint}@t_0 \oplus \bigoplus_{e_i: t_0 < \text{time}(e_i) \le t} \varphi(e_i)
$$

```python
def state_at(self, t: datetime, log: list[EffectLogEntry],
             checkpoints: list[Checkpoint]) -> FoldResult:
    # 查找 t 之前最近的检查点
    best_checkpoint = None
    for cp in reversed(checkpoints):
        if cp.timestamp <= t:
            best_checkpoint = cp
            break

    if best_checkpoint is not None:
        # 仅折叠检查点之后的增量
        delta_effects = [entry.effect for entry in log[best_checkpoint.effect_index:]
                        if entry.timestamp <= t]
        delta_state = self.fold(delta_effects)
        return self._monoid.combine(best_checkpoint.state, delta_state)
    else:
        # 无检查点——从头完整折叠
        return self.fold([e.effect for e in log if e.timestamp <= t])
```

**正确性**基于结合律的分裂性质：

$$
\text{fold}(e_1, ..., e_n) = \text{fold}(e_1, ..., e_k) \oplus \text{fold}(e_{k+1}, ..., e_n)
$$

检查点即为 $\text{fold}(e_1, ..., e_k)$ 的缓存，增量折叠计算 $\text{fold}(e_{k+1}, ..., e_n)$。

### 7.4 增量计算 / Incremental Computation

每次新效果到达时的更新复杂度为 $O(1)$（单次组合运算），而完整重折叠为 $O(n)$：

$$
\text{state}_{n+1} = \text{state}_n \oplus \varphi(e_{n+1})
$$

`FoldEngine` 提供了对比验证：

```python
def fold_incremental_compare(self, effects: list[Effect]) -> IncrementalComparison:
    """
    Demonstrate O(1) incremental update vs O(n) full re-fold.
    Both produce identical results — guaranteed by Monoid associativity.
    """
    state_old = self.fold(effects[:-1])
    incremental = self._monoid.combine(state_old, self._map_fn(effects[-1]))
    full = self.fold(effects)
    # incremental == full (guaranteed)
```

---

## 8. 幺半群性质的验证策略 / Verification of Monoid Properties

### 8.1 Property-Based Testing 框架 / Framework

MonoPLC 使用 **Hypothesis** 框架进行基于性质的测试（Property-Based Testing），通过随机生成大量效果流来验证代数性质。

与传统单元测试（固定输入 → 检查输出）不同，基于性质的测试验证的是**普遍量化的数学命题**：

$$
\forall a, b, c \in S: (a \oplus b) \oplus c = a \oplus (b \oplus c)
$$

### 8.2 关键测试用例分析 / Key Test Case Analysis

**结合律验证**（以 $M_{\text{PLC}}$ 为例）：

```python
class TestPLCEffectMonoid:
    m = PLCEffectMonoid()

    @given(a=effect_list_strategy(), b=effect_list_strategy(), c=effect_list_strategy())
    @hsettings(max_examples=100)
    def test_associativity(self, a, b, c):
        """(a ⊕ b) ⊕ c == a ⊕ (b ⊕ c)"""
        lhs = self.m.combine(self.m.combine(a, b), c)
        rhs = self.m.combine(a, self.m.combine(b, c))
        assert lhs == rhs
```

Hypothesis 自动生成 100 组随机效果列表，验证结合律在所有输入上成立。

**同态保持性验证**（$\varphi$ 的核心性质）：

```python
class TestHomomorphismPhi:
    plc = PLCEffectMonoid()
    mw = MWStateMonoid()

    @given(a=effect_list_strategy(), b=effect_list_strategy())
    @hsettings(max_examples=200)
    def test_homomorphism_law(self, a, b):
        """φ(a ⊕_plc b) == φ(a) ⊕_mw φ(b)"""
        lhs = phi(self.plc.combine(a, b))      # 先组合，再映射
        rhs = self.mw.combine(phi(a), phi(b))   # 先映射，再组合
        assert lhs == rhs
```

这 200 组随机测试验证了同态的核心性质。特别注意的测试场景包括：

- **键冲突（Overlapping Keys）**：两个效果列表包含相同 `(e_type, target)` 时，last-writer-wins 语义在两条路径上是否一致。
- **噪声过滤**：`TICK` 和 `NONE` 效果被过滤后，同态性质是否仍然成立。

**积幺半群验证**：

```python
class TestProductMonoid:
    m = ProductMonoid(MWStateMonoid(), SumMonoid(), SumMonoid())

    def test_identity(self):
        """ε_product == (ε_state, ε_sum, ε_sum)"""
        empty = self.m.empty()
        assert empty == ({}, 0, 0)
        a = ({"key": {"value": 1.0}}, 5, 2)
        assert self.m.combine(self.m.empty(), a) == a  # 左单位元
        assert self.m.combine(a, self.m.empty()) == a  # 右单位元
```

---

## 9. 代数性质带来的架构优势 / Architectural Benefits from Algebraic Properties

### 9.1 结合律 → 并行化与增量计算 / Associativity → Parallelism & Incremental Computation

结合律 $(a \oplus b) \oplus c = a \oplus (b \oplus c)$ 使得折叠操作可以在**任意位置分裂**，带来两项关键能力：

- **并行折叠**：将效果历史分为 $N$ 块，分别在不同线程上折叠，最后合并。正确性由数学定律保证而非测试覆盖。
- **增量更新**：每个新效果到达时只需执行一次 $\oplus$ 运算（$O(1)$），无需重新折叠整个历史（$O(n)$）。
- **检查点复用**：从任意历史检查点开始增量折叠即可得到目标时刻的状态，实现**时间旅行**。

### 9.2 单位元 → 安全的默认状态与空操作 / Identity → Safe Defaults & No-ops

单位元 $\varepsilon$ 的存在保证了：

- **安全初始化**：系统启动时从 $\varepsilon$ 开始折叠，无需特殊的初始化逻辑。
- **空操作不影响状态**：纯函数在不需要产生效果时返回 $\varepsilon$（通过 `FC_EmptyEffect()`），与前序结果组合后状态不变。
- **边界条件简化**：空效果列表、空检查点等边界情况自然处理，无需特判。

### 9.3 同态 → 跨层解耦与独立演化 / Homomorphism → Cross-Layer Decoupling

同态 $\varphi$ 的保持性质意味着：

- **两端可独立设计和优化**：PLC 端优化为固定大小数组的实时操作，Python 端优化为字典的快速查找——两者的内部实现完全解耦。
- **正确性验证降维**：传统系统需要 $O(n^2)$ 的端到端集成测试；MonoPLC 只需验证 $\varphi$ 的保持性质——这是一个可自动化的**有限代数检查**。
- **容错性**：即使网络传输导致效果分批到达，只要最终序列完整，状态重建结果一致。

### 9.4 积幺半群 → 关注点分离与零修改扩展 / Product Monoid → Separation of Concerns & Zero-Modification Extension

积幺半群的分量独立性意味着：

- **关注点分离**：温度控制、湿度控制、报警计数等维度各自在独立的子幺半群中运算，互不干扰。
- **声明式扩展**：新增监控维度只需在 `PRODUCT_COMPONENTS` 注册一个 `MonoidComponent`，无需修改折叠引擎、时间旅行、API 端点等任何下游代码。
- **自动传播**：积幺半群定理保证新分量自动满足幺半群公理。

### 9.5 幂等状态推导 → 故障恢复 / Idempotent State Derivation → Fault Recovery

由于系统状态是效果流的**确定性函数**：

$$
\text{state} = \text{fold}(\varphi, \text{effect\_history})
$$

系统可以在任何故障后通过**重放效果历史**来恢复状态，无需外部持久化或快照同步机制。效果日志本身就是系统的**单一事实来源（Single Source of Truth）**。

---

## 10. LLM 层的代数约束 / Algebraic Constraints on LLM Layer

### 10.1 效果白名单 = 幺半群子集限制 / Effect Whitelist as Monoid Subset Restriction

LLM 层被限制只能生成特定类型的效果，这在代数上等价于将 LLM 的输出限制在 $M_{\text{Effect}}$ 的一个**子幺半群**中：

```python
LLM_ALLOWED_EFFECTS = {0, 1, 2, 5, 6, 7, 9}  # 白名单
# 禁止: 3 (VALVE_CTRL), 4 (ALARM), 8 (SYSTEM_TICK)
```

白名单验证发生在效果进入代数流之前：

```python
def _validate_and_respond(llm_result: dict) -> LLMDecisionResponse:
    if int(e_type) not in LLM_ALLOWED_EFFECTS:
        return LLMDecisionResponse(
            allowed=False,
            message=f"❌ {e_type.name} rejected by whitelist. "
                    f"Valves and alarms are managed by PLC real-time control layer.",
        )
```

这种设计确保 LLM 的决策**永远不能直接控制物理执行器或触发报警**——这些操作只能由 PLC 实时控制层的确定性逻辑生成。LLM 的输出在通过白名单校验后，以标准的 `DUT_Effect` 形式注入 `Async_Input_Queue`，进入统一的代数流。

### 10.2 LLM 决策的代数路径 / Algebraic Path of LLM Decisions

LLM 决策在系统中的完整代数路径：

$$
\text{LLM prompt} \xrightarrow{\text{StateStore.get\_state()}} \text{State} = \text{fold}(\varphi, \text{history})
$$

$$
\text{LLM output} \xrightarrow{\text{whitelist}} e_{\text{llm}} \in M_{\text{Effect}}^{\text{allowed}} \xrightarrow{\text{push}} \text{Async\_Input\_Queue}
$$

$$
\text{Control\_LOOP}: \text{State}' = \text{State} \oplus f(e_{\text{llm}})
$$

LLM 读取的状态本身就是幺半群折叠的结果，其决策经由白名单过滤后重新进入同一个代数循环。整个过程形成了一个**代数闭环**。

---

## 11. 总结 / Conclusion

MonoPLC 展示了幺半群代数在工业控制系统中的实用性。通过将副作用建模为幺半群元素，项目实现了：

1. **形式化正确性保证**：结合律、单位元律、同态保持性均可通过 Property-Based Testing 自动化验证，将系统正确性从"测试覆盖率"提升为"数学证明"。

2. **架构解耦**：PLC 实时控制层与 Python 监督层通过同态 $\varphi$ 桥接，两端可独立设计、优化、演化，跨层一致性由有限的代数检查保证。

3. **计算能力释放**：结合律直接带来并行折叠、增量更新、检查点复用、时间旅行等能力，这些能力在传统过程式 PLC 架构中难以实现。

4. **安全的 LLM 集成**：通过效果白名单将 LLM 输出限制在幺半群子集中，确保智能决策层无法绕过实时控制层的安全边界。

5. **零修改扩展性**：积幺半群的分量独立性使得新增监控维度、传感器、执行器只需声明式注册，所有下游算法自动继承。

这一实践表明，即便在 IEC 61131-3 结构化文本这类受限的编程环境中，形式化代数方法也能有效降低系统复杂度，提供传统方法难以达到的正确性保证和架构灵活性。
