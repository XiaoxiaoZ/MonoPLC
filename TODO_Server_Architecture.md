# TODO: RESTful API + MQTT + Ollama LLM Server Architecture for MonoPLC

> 下次实现时按照此文档逐步完成。

---

## 总体目标

将 MonoPLC 的 HMI 软按钮 (`Inbox_IoT_SoftStop/Start/Reset`) 和消息展示 (`HMI_Logs`) 从 PLC 内部模拟替换为 **真实的外部服务通信**，并引入 **LLM 智能决策层**，让 Ollama 读取 Monoid 数据后自动选择 `ENUM_Effect_Type` 来调用对应的 server 端点。

```
┌─────────────┐    MQTT sub     ┌─────────────────┐
│  前端/按钮   │ ──────────────►│   MQTT Broker    │
│  (Web/CLI)  │                │  (Mosquitto)     │
└─────────────┘                └────────┬────────┘
                                        │ publish to PLC topic
┌─────────────┐    REST API     ┌───────▼────────┐     ADS/共享内存
│  Ollama LLM │ ◄─────────────►│  Bridge Server  │ ◄──────────────► PLC
│  (决策大脑)  │                │  (Python/Node)  │     (TwinCAT)
└─────────────┘                └───────┬────────┘
                                        │ REST
                                        ▼
                               ┌─────────────────┐
                               │  消息展示前端     │
                               │  (Web Dashboard) │
                               └─────────────────┘
```

---

## 第 1 部分：Bridge Server（核心中间件）

### [ ] 1.1 技术选型与项目初始化

- **语言**: Python (FastAPI) 或 Node.js (Express)
  - 推荐 Python FastAPI — 与 Ollama Python SDK 直接集成，async 原生支持
- 初始化项目结构：
  ```
  monoplc-server/
  ├── main.py              # FastAPI 入口
  ├── routers/
  │   ├── effects.py       # RESTful 端点: Effect CRUD
  │   ├── status.py        # 系统状态查询
  │   └── llm.py           # LLM 决策端点
  ├── mqtt_handler.py      # MQTT 客户端 (paho-mqtt)
  ├── plc_bridge.py        # ADS 通信层 (pyads)
  ├── models.py            # Pydantic 数据模型
  ├── config.py            # 配置管理
  └── requirements.txt
  ```

### [ ] 1.2 数据模型定义 (对应 PLC 侧的 DUT)

```python
# models.py
from enum import IntEnum
from pydantic import BaseModel

class EffectType(IntEnum):
    """镜像 PLC 的 ENUM_Effect_Type"""
    EFF_NONE           = 0
    EFF_IOT_PUB        = 1
    EFF_FILE_LOG       = 2
    EFF_VALVE_CTRL     = 3
    EFF_ALARM          = 4
    EFF_IOT_CMD_STOP   = 5
    EFF_IOT_CMD_START  = 6
    EFF_IOT_CMD_RESET  = 7
    EFF_SYSTEM_TICK    = 8

class Effect(BaseModel):
    """镜像 PLC 的 DUT_Effect"""
    e_type: EffectType = EffectType.EFF_NONE
    target: str = ""       # max 32 chars
    payload: str = ""      # max 64 chars
    value: float = 0.0

class EffectMonoid(BaseModel):
    """镜像 PLC 的 DUT_Effect_Monoid"""
    count: int = 0
    effects: list[Effect] = []
```

### [ ] 1.3 RESTful API 端点设计

| Method | Endpoint | 描述 | 对应 PLC 变量 / 行为 |
|--------|----------|------|---------------------|
| `POST` | `/api/effects` | 提交一个 Effect 到 PLC 输入队列 | → `Async_Input_Queue` |
| `GET`  | `/api/effects/queue` | 读取当前输出队列状态 | ← `Async_Effect_Queue` |
| `GET`  | `/api/status` | 读取系统状态 (温度、阀门等) | ← `Net_Temperature`, `Net_ValveCmd` |
| `GET`  | `/api/logs` | 获取 HMI_Logs (消息展示) | ← `HMI_Logs[1..5]` |
| `POST` | `/api/buttons/stop` | 按下 Stop 按钮 | → `Inbox_IoT_SoftStop := TRUE` |
| `POST` | `/api/buttons/start` | 按下 Start 按钮 | → `Inbox_IoT_SoftStart := TRUE` |
| `POST` | `/api/buttons/reset` | 按下 Reset 按钮 | → `Inbox_IoT_SoftReset := TRUE` |
| `POST` | `/api/llm/decide` | LLM 根据当前状态决策 | 见第 3 部分 |

### [ ] 1.4 PLC ADS 通信层

```python
# plc_bridge.py — 使用 pyads 库
import pyads

class PLCBridge:
    def __init__(self, ams_net_id: str, port: int = 851):
        self.plc = pyads.Connection(ams_net_id, port)

    def read_temperature(self) -> float:
        return self.plc.read_by_name("GVL.Net_Temperature", pyads.PLCTYPE_REAL)

    def read_valve_cmd(self) -> bool:
        return self.plc.read_by_name("GVL.Net_ValveCmd", pyads.PLCTYPE_BOOL)

    def read_hmi_logs(self) -> list[str]:
        return [self.plc.read_by_name(f"GVL.HMI_Logs[{i}]", pyads.PLCTYPE_STRING)
                for i in range(1, 6)]

    def write_soft_button(self, button: str):
        """button: 'Stop' | 'Start' | 'Reset'"""
        self.plc.write_by_name(f"GVL.Inbox_IoT_Soft{button}", True, pyads.PLCTYPE_BOOL)

    def push_input_effect(self, effect: dict):
        """将 Effect 写入 Async_Input_Queue"""
        head = self.plc.read_by_name("GVL.Async_Input_Head", pyads.PLCTYPE_INT)
        next_head = (head + 1) % 100
        tail = self.plc.read_by_name("GVL.Async_Input_Tail", pyads.PLCTYPE_INT)
        if next_head != tail:
            base = f"GVL.Async_Input_Queue[{head}]"
            self.plc.write_by_name(f"{base}.EType", effect["e_type"], pyads.PLCTYPE_INT)
            self.plc.write_by_name(f"{base}.Target", effect["target"], pyads.PLCTYPE_STRING)
            self.plc.write_by_name(f"{base}.Payload", effect["payload"], pyads.PLCTYPE_STRING)
            self.plc.write_by_name(f"{base}.Value", effect["value"], pyads.PLCTYPE_REAL)
            self.plc.write_by_name("GVL.Async_Input_Head", next_head, pyads.PLCTYPE_INT)
```

---

## 第 2 部分：MQTT 按钮订阅

### [ ] 2.1 MQTT Broker 搭建

- 安装 Mosquitto 或使用公共测试 broker (如 `test.mosquitto.org`)
- Topic 设计:
  ```
  monoplc/buttons/stop     ← 前端发 "1" 触发停止
  monoplc/buttons/start    ← 前端发 "1" 触发启动
  monoplc/buttons/reset    ← 前端发 "1" 触发复位
  monoplc/status           → Bridge Server 发布状态更新
  monoplc/logs             → Bridge Server 发布日志更新
  monoplc/llm/command      → LLM 决策结果发布
  ```

### [ ] 2.2 MQTT 客户端集成到 Bridge Server

```python
# mqtt_handler.py
import paho.mqtt.client as mqtt

class MQTTHandler:
    def __init__(self, broker: str, port: int, plc_bridge):
        self.client = mqtt.Client()
        self.plc = plc_bridge
        self.client.on_message = self.on_message
        self.client.connect(broker, port)

    def start(self):
        self.client.subscribe("monoplc/buttons/#")
        self.client.loop_start()

    def on_message(self, client, userdata, msg):
        topic = msg.topic
        if topic == "monoplc/buttons/stop":
            self.plc.write_soft_button("Stop")
        elif topic == "monoplc/buttons/start":
            self.plc.write_soft_button("Start")
        elif topic == "monoplc/buttons/reset":
            self.plc.write_soft_button("Reset")

    def publish_status(self, data: dict):
        self.client.publish("monoplc/status", str(data))

    def publish_logs(self, logs: list[str]):
        self.client.publish("monoplc/logs", "\n".join(logs))
```

### [ ] 2.3 按钮前端 (可选 — Web Dashboard 或 CLI)

- 简单 Web 页面用 MQTT over WebSocket 直连 broker
- 三个按钮: Stop / Start / Reset → 发布到对应 MQTT topic
- 日志展示区: 订阅 `monoplc/logs`，实时滚动更新

---

## 第 3 部分：Ollama LLM Server

### [ ] 3.1 Ollama 安装与模型部署

- 安装 Ollama: https://ollama.ai
- 拉取合适模型:
  ```bash
  ollama pull llama3      # 或 mistral、qwen 等
  ```
- 确认本地 API 可用: `http://localhost:11434/api/generate`

### [ ] 3.2 LLM 集成到 Bridge Server

```python
# routers/llm.py
import httpx
from models import EffectType, Effect

OLLAMA_URL = "http://localhost:11434/api/generate"

SYSTEM_PROMPT = """你是 MonoPLC 的智能决策引擎。你根据当前系统状态（温度、阀门状态、日志）
来决定应该执行哪种 Effect 操作。

可用的 ENUM_Effect_Type:
- EFF_NONE (0): 不做任何操作
- EFF_IOT_PUB (1): 发布 IoT 消息到指定 Topic
- EFF_FILE_LOG (2): 写入文件日志
- EFF_VALVE_CTRL (3): 阀门控制 (Target=阀门名, Value>0.5 打开)
- EFF_ALARM (4): 触发报警
- EFF_IOT_CMD_STOP (5): 紧急停机命令
- EFF_IOT_CMD_START (6): 启动系统
- EFF_IOT_CMD_RESET (7): 复位系统
- EFF_SYSTEM_TICK (8): 系统心跳 (通常不由 LLM 触发)

你必须返回一个 JSON 对象:
{
  "e_type": <int>,
  "target": "<string max 32>",
  "payload": "<string max 64>",
  "value": <float>,
  "reasoning": "<简要解释你的决策>"
}
"""

async def llm_decide(current_state: dict) -> dict:
    """读取 Monoid 数据，让 LLM 决定执行哪种 Effect"""
    user_prompt = f"""当前系统状态:
- 温度: {current_state['temperature']}°C
- 阀门: {'打开' if current_state['valve_cmd'] else '关闭'}
- 最近日志: {current_state['logs']}
- 用户指令: {current_state.get('user_input', '无')}

请根据以上信息，决定应该执行什么操作。返回 JSON。"""

    async with httpx.AsyncClient() as client:
        response = await client.post(OLLAMA_URL, json={
            "model": "llama3",
            "prompt": user_prompt,
            "system": SYSTEM_PROMPT,
            "format": "json",
            "stream": False
        })
    return response.json()
```

### [ ] 3.3 LLM 决策流程

```
用户输入 / 定时触发
        │
        ▼
┌─────────────────────────┐
│  Bridge Server 收集状态  │
│  - 读 PLC 温度、阀门     │
│  - 读 HMI_Logs          │
│  - 读 Effect 队列       │
└───────────┬─────────────┘
            │
            ▼
┌─────────────────────────┐
│  Ollama LLM 分析        │
│  - 输入: 当前状态 JSON   │
│  - 输出: Effect 决策 JSON│
│    { e_type, target,    │
│      payload, value }    │
└───────────┬─────────────┘
            │
            ▼
┌─────────────────────────┐
│  Bridge Server 执行      │
│  - 验证 e_type 合法性    │
│  - 通过 ADS 写入 PLC    │
│  - 通过 MQTT 广播结果    │
└─────────────────────────┘
```

### [ ] 3.4 LLM → ENUM_Effect_Type 映射规则

LLM 根据输入/输出语义自动选择：

| 场景 | LLM 输入特征 | 预期选择的 EffectType |
|------|-------------|---------------------|
| 温度过高、阀门未开 | `temp > 80, valve = false` | `EFF_VALVE_CTRL (3)` + `EFF_ALARM (4)` |
| 用户说"停机" | `user_input = "停止"` | `EFF_IOT_CMD_STOP (5)` |
| 用户说"启动" | `user_input = "启动"` | `EFF_IOT_CMD_START (6)` |
| 用户说"复位" | `user_input = "复位"` | `EFF_IOT_CMD_RESET (7)` |
| 温度正常、需要汇报 | `temp < 70, running` | `EFF_IOT_PUB (1)` |
| 异常记录 | 任何异常状态 | `EFF_FILE_LOG (2)` |
| 无需操作 | 一切正常 | `EFF_NONE (0)` |

---

## 第 4 部分：API 文档生成

### [ ] 4.1 自动文档

- FastAPI 自带 Swagger UI: `http://localhost:8000/docs`
- 自带 ReDoc: `http://localhost:8000/redoc`
- 确保所有 Pydantic 模型都有 docstring 和 `Field(description=...)`

### [ ] 4.2 手写使用文档 (API_GUIDE.md)

需包含：
- [ ] 快速启动指南 (安装依赖、配置 AMS Net ID、启动命令)
- [ ] 每个端点的 curl 示例
- [ ] MQTT topic 订阅说明
- [ ] Ollama 模型配置说明
- [ ] 与 PLC GVL 变量的对应关系表

---

## 第 5 部分：可能的 ENUM_Effect_Type 扩展

根据新架构，建议在 PLC 侧扩展以下类型:

```
EFF_REST_RESPONSE := 9,    // REST API 回复确认
EFF_LLM_DECISION  := 10,   // LLM 决策事件 (携带推理结果)
EFF_MQTT_SUB      := 11    // MQTT 外部订阅输入
```

---

## 实施顺序

1. [ ] **搭 Bridge Server 骨架** — FastAPI + pyads + 基本 REST 端点
2. [ ] **实现 PLC ADS 通信** — 读写 GVL 变量
3. [ ] **接入 MQTT** — paho-mqtt 订阅按钮 topic，替代 HMI 软按钮
4. [ ] **实现消息展示** — 通过 REST/MQTT 推送 HMI_Logs 到前端
5. [ ] **安装 Ollama** — 拉取模型，测试本地 API
6. [ ] **集成 LLM 决策** — Bridge Server 调用 Ollama，解析 JSON 回复
7. [ ] **LLM → Effect 路由** — 将 LLM 决策转为 `DUT_Effect` 写入 PLC
8. [ ] **生成 API 文档** — Swagger + 手写 API_GUIDE.md
9. [ ] **端到端测试** — 按钮 → MQTT → Server → PLC → 日志 → 前端
10. [ ] **（可选）PLC 侧扩展 ENUM_Effect_Type** — 增加 REST/LLM/MQTT 类型

---

## 依赖概览

```
# requirements.txt
fastapi>=0.100.0
uvicorn>=0.23.0
pyads>=3.3.0
paho-mqtt>=1.6.0
httpx>=0.24.0
pydantic>=2.0.0
```

```
# Ollama (单独安装)
# https://ollama.ai
ollama pull llama3
```
