# MonoPLC Bridge Server 架构（已实现）

> **注意**：本文档最初为设计草稿，现已更新以反映实际实现。MQTT 集成部分尚未实现，保留为未来 TODO。

---

## 总体目标

将 MonoPLC 的 HMI 软按钮和消息展示从 PLC 内部模拟替换为 **真实的外部服务通信**，并引入 **LLM 智能决策层**，让 Ollama 读取 Monoid 数据后自动选择 `ENUM_Effect_Type` 来调用对应的 server 端点。

**核心架构约束**：Bridge Server 与 PLC 之间**仅通过 Effect 环形队列通信**（Monoid-Only），禁止直接读写 GVL 变量（如 `Net_Temperature`, `Net_ValveCmd`）。

```
┌─────────────┐    REST API     ┌─────────────────┐     ADS 环形队列
│  Ollama LLM │ ◄─────────────►│  Bridge Server  │ ◄──────────────► PLC
│  (决策大脑)  │                │  (Python FastAPI)│     (TwinCAT)
└─────────────┘                └───────┬────────┘
                                        │ REST + Static
                                        ▼
                               ┌─────────────────┐
                               │  Web Dashboard   │
                               │  (HTTP 轮询)     │
                               └─────────────────┘
```

---

## 第 1 部分：Bridge Server（核心中间件）

### [x] 1.1 技术选型与项目初始化

- **语言**: Python (FastAPI)，async 原生支持
- 项目结构：
  ```
  monoplc-server/
  ├── main.py              # FastAPI 入口 + lifespan 管理
  ├── config.py            # pydantic-settings 配置管理
  ├── models.py            # Pydantic 数据模型（镜像 PLC DUT）
  ├── plc_bridge.py        # ADS 通信层 (Monoid-Only: 仅队列读写)
  ├── state_store.py       # 泛型 KV 状态重建（零领域耦合）
  ├── check_logs.py        # 日志查询工具脚本
  ├── routers/
  │   ├── __init__.py
  │   ├── effects.py       # Effect CRUD 端点
  │   ├── status.py        # 系统状态查询
  │   ├── buttons.py       # 软按钮端点 (Start/Stop/Reset)
  │   └── llm.py           # LLM 决策/聊天/解读端点
  └── static/
      ├── app.js           # 前端 Dashboard JavaScript
      └── system_doc.md    # LLM 操作手册
  ```

### [x] 1.2 数据模型定义 (对应 PLC 侧的 DUT)

```python
# models.py
from enum import IntEnum
from pydantic import BaseModel, Field

class EffectType(IntEnum):
    """镜像 PLC 的 ENUM_Effect_Type"""
    EFF_NONE            = 0
    EFF_IOT_PUB         = 1
    EFF_FILE_LOG        = 2
    EFF_VALVE_CTRL      = 3   # ⚠ PLC 控制层专用，监控层禁止
    EFF_ALARM           = 4   # ⚠ PLC 控制层专用，监控层禁止
    EFF_IOT_CMD_STOP    = 5
    EFF_IOT_CMD_START   = 6
    EFF_IOT_CMD_RESET   = 7
    EFF_SYSTEM_TICK     = 8   # ⚠ PLC 内部时钟，外部禁止
    EFF_SETPOINT_CHANGE = 9   # 参数调整（LLM 最重要的控制接口）
    EFF_LLM_DECISION    = 10  # 未来扩展

# LLM 白名单 — 仅允许监控层使用以下类型
LLM_ALLOWED_EFFECTS = frozenset({0, 1, 2, 5, 6, 7, 9})

class Effect(BaseModel):
    """镜像 PLC 的 DUT_Effect"""
    e_type: EffectType = Field(default=EffectType.EFF_NONE)
    target: str = Field(default="", max_length=32)
    payload: str = Field(default="", max_length=64)
    value: float = Field(default=0.0)

class EffectMonoid(BaseModel):
    """镜像 PLC 的 DUT_Effect_Monoid"""
    count: int = 0
    effects: list[Effect] = Field(default_factory=list)
```

### [x] 1.3 RESTful API 端点设计

| Method | Endpoint | 描述 | 实现方式 |
|--------|----------|------|---------|
| `POST` | `/api/effects` | 提交一个 Effect 到 PLC 输入队列 | → `plc_bridge.push_input_effect()` |
| `GET`  | `/api/effects/stream?n=20` | 获取最近 N 条 Effect 日志 | ← `state_store.get_logs()` |
| `GET`  | `/api/status` | 读取泛型重建的系统状态 | ← `state_store.get_state()` |
| `GET`  | `/api/logs?n=20` | 获取带时间戳的 Effect 日志 | ← `state_store.get_logs()` |
| `POST` | `/api/buttons/stop` | 构造并发送 `EFF_IOT_CMD_STOP` | → `plc_bridge.push_input_effect()` |
| `POST` | `/api/buttons/start` | 构造并发送 `EFF_IOT_CMD_START` | → `plc_bridge.push_input_effect()` |
| `POST` | `/api/buttons/reset` | 构造并发送 `EFF_IOT_CMD_RESET` | → `plc_bridge.push_input_effect()` |
| `POST` | `/api/llm/decide` | LLM 纯状态驱动自动决策 | Ollama → 白名单校验 → PLC |
| `POST` | `/api/llm/chat` | 用户文本输入驱动 LLM 决策 | 同上，附加 user_input |
| `GET`  | `/api/llm/interpret` | LLM 实时解读系统状态 | 返回 summary + status_cards |

### [x] 1.4 PLC ADS 通信层 (Monoid-Only)

**架构约束**：仅通过 Effect 环形队列与 PLC 通信，禁止直接读写 GVL 变量。

```python
# plc_bridge.py — Monoid-Only ADS 通信层
class PLCBridge:
    """仅暴露两个数据通道：
      - push_input_effect()  : Server → PLC (Async_Input_Queue)
      - pop_output_effects() : PLC → Server (Async_Effect_Queue)
    """
    QUEUE_SIZE = 100  # PLC 侧环形缓冲区大小

    def push_input_effect(self, effect: Effect) -> bool:
        """写入一个 Effect 到 PLC 的 Async_Input_Queue 环形缓冲区"""
        # 读 Head/Tail → 检查队列满 → 写入数据 → 更新 Head 指针
        ...

    def pop_output_effects(self) -> list[Effect]:
        """从 PLC 的 Async_Effect_Queue 弹出所有待处理 Effect
        包含环形缓冲区溢出保护：检测生产者越过消费者时自动跳转 Tail"""
        ...
```

**注意**：按钮操作（Start/Stop/Reset）不再直接写 `Inbox_IoT_SoftStop := TRUE`，而是构造对应的 `Effect`（如 `EFF_IOT_CMD_STOP`）通过 `push_input_effect()` 发送。

---

## 第 2 部分：MQTT 按钮订阅

> **状态：未实现 (TODO)**
> 当前前端通过 HTTP REST API 直接与 Bridge Server 通信，MQTT 集成留待未来实现。

### [ ] 2.1 MQTT Broker 搭建
### [ ] 2.2 MQTT 客户端集成到 Bridge Server
### [ ] 2.3 按钮前端 MQTT 接入

---

## 第 3 部分：Ollama LLM Server

### [x] 3.1 Ollama 安装与模型部署

- 安装 Ollama: https://ollama.ai
- 拉取合适模型:
  ```bash
  ollama pull llama3      # 或 mistral、qwen 等
  ```
- 确认本地 API 可用: `http://localhost:11434/api/generate`
- 模型名称通过 `config.py` 的 `OLLAMA_MODEL` 配置

### [x] 3.2 LLM 集成到 Bridge Server

实际实现采用**动态 System Prompt 生成** + **白名单安全机制**：

```python
# routers/llm.py — 实际实现要点
async def get_system_prompt() -> str:
    """动态生成 System Prompt：
    1. 从 SYS_DOC_URL 拉取操作手册 (system_doc.md)
    2. 动态生成白名单 / 禁止列表（从 EffectType 枚举自动导出）
    """

def _build_state_prompt(state: dict, user_input: str | None = None) -> str:
    """泛型状态序列化 — 遍历 state 字典所有键值对，无硬编码领域变量"""

def _validate_and_respond(llm_result: dict) -> LLMDecisionResponse:
    """白名单校验：仅允许 LLM_ALLOWED_EFFECTS 中的 e_type 通过"""
```

**关键安全约束**：
- LLM 被明确告知它是**监控层**，不是控制层
- 禁止使用 `EFF_VALVE_CTRL(3)`, `EFF_ALARM(4)`, `EFF_SYSTEM_TICK(8)`
- 允许使用 `{0, 1, 2, 5, 6, 7, 9}` 类型的 Effect
- 如果 LLM 尝试使用禁止类型，`_validate_and_respond()` 会拦截并返回错误

### [x] 3.3 LLM 决策流程

```
用户输入 / API 调用
        │
        ▼
┌─────────────────────────┐
│  Bridge Server 收集状态  │
│  - 从 StateStore 获取    │
│    泛型 KV 字典          │
│  - 无硬编码变量名        │
└───────────┬─────────────┘
            │
            ▼
┌─────────────────────────┐
│  Ollama LLM 分析        │
│  - 输入: 动态 Prompt     │
│    (系统状态 + 操作手册)  │
│  - 输出: Effect 决策 JSON│
└───────────┬─────────────┘
            │
            ▼
┌─────────────────────────┐
│  Bridge Server 执行      │
│  - 白名单校验 e_type     │
│  - 通过 ADS 队列写入 PLC │
└─────────────────────────┘
```

### [x] 3.4 LLM → ENUM_Effect_Type 映射规则

LLM 根据白名单约束进行决策：

| 场景 | LLM 输入特征 | 预期选择的 EffectType |
|------|-------------|---------------------|
| 温度过高 | 温度超过阈值 | `EFF_SETPOINT_CHANGE (9)` 调整参数 |
| 用户说"停机" | `user_input = "停止"` | `EFF_IOT_CMD_STOP (5)` |
| 用户说"启动" | `user_input = "启动"` | `EFF_IOT_CMD_START (6)` |
| 用户说"复位" | `user_input = "复位"` | `EFF_IOT_CMD_RESET (7)` |
| 温度正常、需要汇报 | 系统运行正常 | `EFF_IOT_PUB (1)` |
| 异常记录 | 任何异常状态 | `EFF_FILE_LOG (2)` |
| 无需操作 | 一切正常 | `EFF_NONE (0)` |

> **注意**：LLM **严禁**直接使用 `EFF_VALVE_CTRL(3)` 或 `EFF_ALARM(4)`。阀门控制和报警由 PLC 的 `FB_SimpleLogic` 实时控制层独立处理。

---

## 第 4 部分：API 文档生成

### [x] 4.1 自动文档

- FastAPI 自带 Swagger UI: `http://localhost:8000/docs`
- 自带 ReDoc: `http://localhost:8000/redoc`
- 所有 Pydantic 模型均有 docstring 和 `Field(description=...)`

### [ ] 4.2 手写使用文档 (API_GUIDE.md)

需包含：
- [ ] 快速启动指南 (安装依赖、配置 AMS Net ID、启动命令)
- [ ] 每个端点的 curl 示例
- [ ] Ollama 模型配置说明
- [ ] Effect 类型与白名单说明

---

## 第 5 部分：ENUM_Effect_Type 扩展（已完成部分）

已在 PLC 和 Python 侧实现的扩展：

```
EFF_SETPOINT_CHANGE := 9,   // 参数调整（LLM 监控层最重要的控制接口）
EFF_LLM_DECISION    := 10,  // LLM 决策事件（未来扩展）
```

---

## 实施顺序

1. [x] **搭 Bridge Server 骨架** — FastAPI + pyads + 基本 REST 端点
2. [x] **实现 PLC ADS 通信** — Monoid-Only 环形队列读写
3. [x] **实现 Web Dashboard 前端** — HTTP 轮询 + 软按钮 + 日志展示
4. [x] **集成 LLM 决策** — Bridge Server 调用 Ollama，解析 JSON 回复
5. [x] **LLM → Effect 路由** — 白名单校验 + 自动写入 PLC
6. [x] **LLM 系统解读** — `/api/llm/interpret` 端点 + 状态卡片
7. [ ] **（可选）接入 MQTT** — paho-mqtt 订阅按钮 topic
8. [ ] **生成 API 使用文档** — 手写 API_GUIDE.md

---

## 依赖概览

```
# requirements.txt
fastapi>=0.100.0
uvicorn>=0.23.0
pyads>=3.3.0
httpx>=0.24.0
pydantic>=2.0.0
pydantic-settings>=2.0.0
```

```
# Ollama (单独安装)
# https://ollama.ai
ollama pull llama3
```
