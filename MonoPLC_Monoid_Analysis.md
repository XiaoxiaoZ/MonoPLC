# MonoPLC Monoid 完整代数分析

## 1. Monoid 全景图

MonoPLC 当前实现了 **7 种具名 Monoid**，分布于 PLC 和 Python 两层，并通过 **Monoid 同态（Homomorphism）** 和 **Product Monoid** 实现跨层桥接与多维度组合。

### 1.1 原子 Monoid 一览

| 名称 | 载体 (Carrier) | 运算 ⊕ | 单位元 ε | 所在层 | 代码位置 |
|---|---|---|---|---|---|
| **PLCEffectMonoid** | `list[Effect]` | 列表拼接 (concat) | `[]` | 双端 | PLC: `FC_CombineEffects` / Py: `monoid.py` |
| **MWStateMonoid** | `dict[str, dict]` | 右偏字典合并 (`{**a, **b}`) | `{}` | Python | `monoid.py` |
| **SumMonoid** | `int` | 加法 (`a + b`) | `0` | Python | `monoid.py` |
| **MaxMonoid** | `float` | 取最大 (`max(a, b)`) | `-∞` | Python | `monoid.py` |
| **DUT_Humidity_Monoid** | `REAL` (SprayAmount) | 加法 (`A + B`) | `0.0` | PLC | `FC_CombineHumidity` |

### 1.2 复合 Monoid（Product Monoid）

| 名称 | 组成 | 运算 | 所在层 | 代码位置 |
|---|---|---|---|---|
| **DUT_Climate_Monoid** | `(DUT_Effect_Monoid, DUT_Humidity_Monoid)` | 逐分量 combine | PLC | `FC_CombineClimate` |
| **DictProductMonoid** | `{state: M_MW, effect_count: M_Sum, alarm_count: M_Sum, total_spray_ml: M_Sum}` | 逐分量 combine | Python | `monoid.py` `DictProductMonoid` |

> [!IMPORTANT]
> `DictProductMonoid` 使用**字典**存储各分量，而非元组。这意味着添加新维度只需在 `PRODUCT_COMPONENTS` 注册表追加一行，**所有下游算法（Fold、Time-Travel、Checkpoint、REST API）自动继承新维度，零代码改动**。

---

## 2. 跨层桥梁：Monoid 同态 (Homomorphism)

两端的 Monoid 通过同态函数 `φ` 实现代数等价桥接：

```
M_PLC ──φ──→ M_MW
(list concat)    (right-biased dict merge)
```

**同态律**：`φ(a ⊕_PLC b) = φ(a) ⊕_MW φ(b)`

```python
# homomorphism.py — φ 函数
def phi(effects: list[Effect]) -> dict:
    result = {}
    for e in effects:
        key = f"{e.e_type.name}.{e.target}"
        result[key] = {"value": e.value, "payload": e.payload, "target": e.target}
    return result
```

φ 对 EffectType 完全透明——新增传感器类型 → 自动产生新 key → 无需修改 φ。

> [!TIP]
> 同态律已通过 **Hypothesis property-based testing** 验证（`test_monoid_properties.py`），覆盖任意随机生成的 Effect 组合。

---

## 3. 生产端

### 3.1 PLC 生产端 — Free Monoid 组装意图

`FB_SimpleLogic` 在纯函数内用 combine 组装多个副作用意图：

```pascal
Effects_Out := FC_EmptyEffect();                          // ε (空列表)
Eff_Valve   := FC_ValveEffect('CoolingValve', TRUE);      // [a]
Eff_Network := FC_IoTEffect('system/status', msg);        // [b]
Effects_Out := FC_CombineEffects(Eff_Valve, Eff_Network); // [a] ⊕ [b] = [a, b]
```

`FB_HumidityLogic` 独立产出 Sum Monoid：

```pascal
Effects_Out := FC_EmptyHumidity();                                    // ε = 0.0
IF Input_Event.EType = EFF_VALVE_CTRL AND Target = 'Humidifier' THEN
    Effects_Out.SprayAmount := LREAL_TO_REAL(Input_Event.Value);      // 5.0
END_IF
```

两者通过 **Product Monoid** 无痛合体：

```pascal
Local_Climate.Temp := Inst_Logic.Effects_Out;      // Free Monoid
Local_Climate.Hum  := Inst_HumLogic.Effects_Out;   // Sum Monoid
Global_Climate := FC_CombineClimate(Global_Climate, Local_Climate);
// FC_CombineClimate 只做逐分量委托，零领域知识
```

### 3.2 Python 生产端 — 退化使用 `ε ⊕ [e]`

Python 端每次只构造单个 Effect 推入队列：

```python
effect = Effect(e_type=EFF_VALVE_CTRL, target="Humidifier", value=5.0)
bridge.push_input_effect(effect)  # ε ⊕ [effect]
```

> [!NOTE]
> 如果 LLM 需要一次性下发多个指令（"同时改温度上限并启动"），则需真正的 combine。当前 whitelist 机制（`LLM_ALLOWED_EFFECTS`）限制了 LLM 只能操作安全的 Effect 子集。

---

## 4. 消费端

### 4.1 PLC 消费端 — 事件总线路由

Monoid 被拆为逐个 Effect 进入事件总线，通过三层路由系统分发：

```
┌─────────────────────────────────────────────────────┐
│                 Event_Bus_Queue                      │
│              (循环队列, 容量 200)                     │
└────────────────────┬────────────────────────────────┘
                     │ Pop One Event
                     ▼
    ┌──── IF EFF_VALVE_CTRL / IOT_PUB / ALARM ────┐
    │  路由 1&2: 写入 Async_Effect_Queue            │ → Python 可见
    │           (跨层日志)                           │
    └───────────────────────────────────────────────┘
    ┌──── IF EFF_VALVE_CTRL ───────────────────────┐
    │  硬件映射: CoolingValve → GVL.Net_ValveCmd   │ → 物理输出
    └───────────────────────────────────────────────┘
    ┌──── IF TICK/CMD/SETPOINT/VALVE(Humidifier) ──┐
    │  路由 3: → Inst_Logic (温度纯函数)            │
    │          → Inst_HumLogic (湿度纯函数)         │
    │          → FC_CombineClimate (Product 组合)   │
    │          → 事件递归 (新副作用回填总线)          │ → 递归闭环
    └───────────────────────────────────────────────┘
```

> [!IMPORTANT]
> 路由使用**非互斥 IF**（而非 IF-ELSIF），允许一个 Event 同时触发多条路由。例如 `EFF_VALVE_CTRL` 既被记录到 Python 日志队列，又被送入物理映射和业务逻辑。

### 4.2 Python 消费端 — DictProductMonoid Fold

Python 端将每条 Effect 通过 `DictProductMonoid.map_effect` 并行映射到 N 个子维度，再逐维度 `combine`：

```python
PRODUCT_COMPONENTS = [
    MonoidComponent(name="state",          monoid=MWStateMonoid(), map_fn=map_state),
    MonoidComponent(name="effect_count",   monoid=SumMonoid(),     map_fn=map_count),
    MonoidComponent(name="alarm_count",    monoid=SumMonoid(),     map_fn=map_alarm),
    MonoidComponent(name="total_spray_ml", monoid=SumMonoid(),     map_fn=map_spray),
]
# 添加第 5 个维度？只需这里多写一行，全系统自动适应。
```

每个 `map_fn` 是一个独立的 **Monoid 同态**，将单条 `Effect` 投射到对应子 Monoid 的载体上。

---

## 5. Monoid 的生命周期（更新版）

```
FB_SimpleLogic / FB_HumidityLogic 内部: Monoid (combine 组装意图)
        ↓ Product Monoid: FC_CombineClimate (合体)
Global_Climate 变量: DUT_Climate_Monoid (PLC-Native 状态，仅存在于 PLC 运存)
        ↓
FOR i := 1 TO Count → 拆成单个 Effect
        ↓
Event_Bus_Queue: 单个 DUT_Effect (路由分发)
        ↓ 路由 1&2: 写入 Async_Effect_Queue
        ↓ ADS 逐字段传输 → Python
Python: Effect 对象
        ↓ DictProductMonoid.map_effect(effect)
        ↓ DictProductMonoid.combine(accumulated, mapped)
Python StateStore: N-维 dict {state: {...}, effect_count: N, alarm_count: M, total_spray_ml: X}
        ↓ Checkpoint 持久化 → checkpoint.json
        ↓ REST API → 前端 Dashboard / Time-Travel UI
```

---

## 6. 已实现的代数能力

### 6.1 时间旅行 (Time Travel) ✅

```python
def state_at(t: int) -> dict:
    """重建第 t 条 Effect 时刻的完整系统状态"""
    effects = persistent_log[:t]
    return fold_engine.fold(effects)  # 使用 checkpoint 加速
```

结合律保证：从任意 checkpoint 断点恢复 + fold 剩余部分 = 从头 fold 全量。

### 6.2 并行 Fold (Parallel Fold) ✅

```python
# 将日志分片到 N 个 worker
chunk_results = parallel_map(fold, chunks)
# 按顺序合并（仅需结合律，不需交换律）
final = reduce(monoid.combine, chunk_results, monoid.empty())
```

### 6.3 增量计算 (Incremental Fold) ✅

```python
state_new = monoid.combine(state_old, map_effect(e_new))  # O(1)
```

### 6.4 Checkpoint 加速 ✅

```python
# 不从 Effect #0 开始，而是从最近的 checkpoint 恢复
checkpoint_state = load_checkpoint()  # 直接跳到 Effect #N
remaining = effects[N:]
state = fold(remaining, initial=checkpoint_state)
```

### 6.5 代数测试 (Property-Based Testing) ✅

```python
@given(a=effect_list_strategy(), b=effect_list_strategy())
def test_homomorphism_law(self, a, b):
    assert phi(plc.combine(a, b)) == mw.combine(phi(a), phi(b))
```

一条法则覆盖所有输入空间（Hypothesis 随机生成），新增 EffectType 时零新测试代码。

### 6.6 LLM 安全隔离 ✅

```python
LLM_ALLOWED_EFFECTS = frozenset({EFF_NONE, EFF_IOT_PUB, EFF_FILE_LOG, ...})
# EFF_VALVE_CTRL, EFF_ALARM → 被 blacklist 拦截，永远无法从 LLM 到达 PLC
```

Free Monoid 的延迟执行特性使得 Effect 是**纯数据**，可在执行前被审查和过滤。

---

## 7. 跨层一致性证明

当 PLC 和 Python 接收**同一事件流**时，两端的 Product Monoid 的 fold 结果**数学严格一致**：

```
PLC:    Global_Climate.Hum.SprayAmount = Σ map_spray(e_i)  (PLC 内部)
Python: total_spray_ml                 = Σ map_spray(e_i)  (从 Async_Effect_Queue 重建)
```

> [!CAUTION]
> 一致性的**必要前提**是事件流的完整性：PLC 必须将所有参与 fold 的 Event 都写入 `Async_Effect_Queue`。如果 PLC 本地消化了某些 Event（如 `EFF_SYSTEM_TICK`）而不上报，Python 端的重建将与 PLC 原生状态产生偏差。这是一个**有意的架构决策**（过滤高频 TICK 以避免网络洪泛），而非 Bug。

---

## 8. Product Monoid 的可扩展性证明

添加新的业务维度（例如"喷水总量"）的完整步骤：

### 唯一需要修改的逻辑代码

```python
# state_store.py — 组件注册表
def map_spray(effect: Effect) -> float:
    return float(effect.value) if ... else 0.0

PRODUCT_COMPONENTS = [
    ...existing components...,
    MonoidComponent(name="total_spray_ml", monoid=SumMonoid(), map_fn=map_spray),  # ← 新增
]
```

### 以下模块无需任何修改

| 模块 | 为什么不用改 |
|---|---|
| `FoldEngine` | 操作抽象 Monoid 接口，不感知具体维度 |
| `StateStore._consume_effect` | 调用 `DictProductMonoid.map_effect` + `combine`，维度透明 |
| Time-Travel API (`state_at`) | 返回 `fold()` 结果的 dict，自动包含新 key |
| Checkpoint 系统 | 序列化/反序列化整个 dict，自动包含新 key |
| `routers/algebra.py` | 动态遍历 dict keys，无硬编码 |

> [!TIP]
> 这正是 **Product Monoid 的代数隔离定理**：`(M₁ × M₂ × ... × Mₙ)` 的 combine 是逐分量独立的。添加 `Mₙ₊₁` 不影响已有的 `M₁...Mₙ` 的正确性，因为新维度的运算与旧维度在代数上**正交**。
