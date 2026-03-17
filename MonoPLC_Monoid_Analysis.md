# MonoPLC 两个 Monoid 分析

## Monoid 定义

**Monoid 是 Effect 的列表**，不是单个 Effect。

| 组成 | PLC 端 | Python 端 |
|---|---|---|
| **载体** | `DUT_Effect_Monoid` = `(Count, Effects[1..20])` | `EffectMonoid` = `(count, list[Effect])` |
| **运算 ⊕** | `FC_CombineEffects(A, B)` — 列表拼接 | `list` 的 `+` 运算符 — 列表拼接 |
| **单位元 ε** | `FC_EmptyEffect()` — 空列表 | `EffectMonoid()` |
| **生成元** | `DUT_Effect` — 单个元素，lift 后为 `ε ⊕ [e]` | `Effect` — 单个元素，lift 后为 `ε ⊕ [e]`（Python 端实际使用方式） |

---

## 生产端

### PLC 生产端 — 完整 Monoid combine

`FB_SimpleLogic` 在纯函数内用 combine 组装多个意图：

```pascal
Effects_Out := FC_EmptyEffect();                         // ε
Eff_Valve   := FC_ValveEffect('CoolingValve', TRUE);     // [a]
Eff_Network := FC_IoTEffect('system/status', msg);       // [b]
Effects_Out := FC_CombineEffects(Eff_Valve, Eff_Network); // [a] ⊕ [b] = [a, b]
```

### Python 生产端 — 退化的 ε ⊕ [e]

每次只构造单个 Effect 推入队列，等价于 `ε ⊕ [e] = [e]`：

```python
effect = Effect(e_type=EFF_IOT_CMD_STOP, target="HMI_Button", ...)
bridge.push_input_effect(effect)  # 概念上 = ε ⊕ [effect]
```

Python 端是**退化（degenerate）的 Monoid 使用**——永远只做 `ε ⊕ [e]`，从不做 `[a, b] ⊕ [c, d]`。combine 能力可用但未被需要，因为目前一次只推一个命令。

> [!NOTE]
> 如果将来 LLM 需要一次性下发多个指令（比如"同时改温度上限并启动系统"），那就会需要真正的 combine。

---

## 消费端

### PLC 消费端 — Mealy Machine（拆解 + 转换）

Monoid 被拆为逐个 Effect，通过事件总线路由：

```pascal
FOR i := 1 TO Total_Monoid.Count DO           // 拆解 Monoid
    Event_Bus_Queue[Q_Head] := Total_Monoid.Effects[i];
END_FOR;
// 然后逐个 pattern match → 路由到硬件/异步队列/纯函数
```

消费的目的是**产生新的 Effect 流**（吃了吐），不是 combine。

### Python 消费端 — Fold（拆解 + 折叠到状态）

逐个 Effect 折叠到 key-value 状态快照：

```python
for effect in effects:                        # 拆解
    composite_key = f"{effect.e_type.name}.{effect.target}"
    self._state.data[composite_key] = {"value": ..., "payload": ...}
```

消费的目的是**重建可查询的状态**（吃了存），不是 combine。

---

## Monoid 的生命周期

Monoid 只存活在 `FB_SimpleLogic` 的纯函数计算中，出了函数就被逐层拆解：

```
FB_SimpleLogic 内部: Monoid (combine 组装)
        ↓ FOR i := 1 TO Count — 拆成单个 Effect
Event_Bus_Queue: 单个 DUT_Effect
        ↓ 路由器分发
Async_Effect_Queue: 单个 DUT_Effect
        ↓ ADS 逐字段传输 — 连结构体都拆了
Python: 裸标量 (INT, STRING, STRING, REAL)
        ↓ 重组为 Effect 对象
StateStore: 折叠到 Dict
```

---

## 研究方向：持久化 Effect 历史 → Event Sourcing

当前 `StateStore` 使用 `deque(maxlen=N)` 只保留最近 N 条 Effect 日志。如果将 Effect 历史**完整持久化**，则可以解锁 fold 的全部能力：

### 任意时间切片的状态重建

```python
def state_at(timestamp) -> Dict:
    """重建任意时刻的系统状态"""
    return foldl(_consume_effect, {}, effects_before(timestamp))

def state_between(t1, t2) -> Dict:
    """某个时间段内的状态增量"""
    return foldl(_consume_effect, {}, effects_in_range(t1, t2))
```

### 升级为 foldMap 后可并行化

如果 fold 函数满足 Monoid homomorphism（`h(a ⊕ b) = h(a) ⊕' h(b)`），则可以分片并行 fold 再 combine：

```
foldMap f [e₁...e₅₀₀₀] ⊕' foldMap f [e₅₀₀₁...e₁₀₀₀₀]
= foldMap f [e₁...e₁₀₀₀₀]
```

### 与现有架构的距离

| 概念 | 现有实现 | 需要的改动 |
|---|---|---|
| Event Log | `deque(maxlen=N)` 有限缓冲 | 持久化到数据库/文件 |
| State Projection | `_consume_effect` (foldl) | 已具备 |
| 时间旅行 | ⚠️ 理论已支持，缺持久化 | 持久化后即可用 |
| 并行 fold | ⚠️ homomorphism 已满足 | 持久化后即可用 |

> [!TIP]
> 当前 `_consume_effect` 的 last-writer-wins 覆写本质是 **right-biased Dict merge**（`{**left, **right}`），这构成一个合法的 Monoid，且 fold 函数是一个 **Monoid homomorphism**。因此 foldMap 并行化是可行的——只需保证分片结果**按顺序从左到右合并**（不要求交换律，只要求结合律）。
