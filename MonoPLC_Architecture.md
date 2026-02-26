# MonoPLC: 基于 Monoid 的纯函数式 PLC 控制架构的数学模型与工业实践

## 1. 核心痛点：为什么传统的 PLC 架构会走向失控？

在传统的工业自动化编程（如 IEC 61131-3，特别是 ST/SCL 和梯形图）中，我们往往直面底层硬件，代码中充斥着大量的**状态读写**与**瞬时动作**：

```iecst
// 传统代码示例：灾难的温床
IF Temp > 80.0 THEN
    CoolingValve := TRUE;    
    StartTimer(IN:=TRUE);    
    MQTT_Send('Warning');    // 副作用：调用外部网络库发送字符串，可能导致 PLC 扫描周期拉长
END_IF
```

这种直接操作带来致命的工程问题：
**纯真逻辑与外部副作用的混淆（网络、IO、延迟互相纠缠）**
纯粹的商业逻辑（“什么时候该开水阀”）与底层通信（“怎么发 MQTT”、“怎么等待网络延迟而不阻塞 Task”）被捏合在一起。如果网络堵塞，直接调用 `MQTT_Send` 会拉爆整个毫秒级的实时 Task；一旦异步拉出了别的 Task，又会面临跨任务数据抢占互斥的噩梦。

---

## 2. 破局之道：引入 Monoid 代数结构

为了解决上述乱象，我们从抽象代数中借用了一个简单但极其强大的概念：**Monoid（幺半群）**。

### 什么是 Monoid？
在数学上，一个集合 $M$ 和其上的一个二元运算 $\circ$ 如果满足以下三个条件，就构成了一个 Monoid：
1. **封闭性 (Closure)**：对任意 $a, b \in M$，有 $a \circ b \in M$。
2. **结合律 (Associativity)**：对任意 $a, b, c \in M$，有 $(a \circ b) \circ c = a \circ (b \circ c)$。
3. **单位元 (Identity)**：存在一个元素 $e \in M$，使得对任意 $a \in M$，有 $e \circ a = a \circ e = a$。

### 为什么 Monoid 能拯救业务代码？
在 MonoPLC 架构中，我们将**所有的外部影响（无论输入信号还是输出动作）**抽象为**“Effect（副作用）”**的数据。而 `Effect` 集合通过一个合并运算，构成了 `Effect_Monoid`。

这意味着：
- **封闭性 (Closure)**：一个 Effect（如关水阀）和另一个 Effect（如发日志），合起来依然也是一个完全相同的 `DUT_Effect_Monoid` 类型。不仅逻辑接口极度统一，还能实现无穷尽的堆叠。处理 1 个操作和处理 100 个操作，在下游函数的入参定义上毫无区别：
  ```iecst
  Eff_Valve   := FC_ValveEffect('CoolingValve', FALSE); // 产生 1 个关阀副作用
  Eff_Network := FC_IoTEffect('mqtt/status', 'Stop');   // 产生 1 个网络副作用
  // 两个不同维度的操作合并，产生的仍是完全一样格式的 DUT_Effect_Monoid
  Effects_Out := FC_CombineEffects(Eff_Valve, Eff_Network); 
  ```

- **单位元 (Identity)**：如果这一毫秒的扫描周期内风平浪静，我们返回的并不是传统的“直接 RETURN 不做什么（Void/No-Op）”，或者是“返回一个 NULL 指针”，而是坚持返回一个“空的 Monoid”：
  ```iecst
  // 没有触发任何事件，返回一个 Count = 0, 空数组的 Monoid
  Effects_Out := FC_EmptyEffect(); 
  ```
  这彻底消除了下游函数处理空数据时那些丑陋的 `IF pData <> 0 THEN` 等边界空数组判断。任何事物加上了 `FC_EmptyEffect()` 都依然等于它最初的自己。

- **结合律 (Associativity)**：这是应对多重并发和异步通信的关键。无论是 Task A 本地从 HMI 收到的直接电信号，还是 Task B 从网络 IoT 协议解析出的间接命令，只要它们都遵循这个 Monoid 规则，开发者就可以随意将它们像数学加法一样折叠：
  ```iecst
  // 无论先加哪个，或者在哪一步加，结果毫无区别： (网 + 屏) + 物理管脚 = 网 + (屏 + 物理管脚)
  Total_Input_Monoid := FC_CombineEffects(IoT_Input_Monoid, HMI_Input_Monoid);
  Total_Input_Monoid := FC_CombineEffects(Total_Input_Monoid, Physical_Input_Monoid);
  ```
  无论是在什么核心的哪个 Task 里、以何种顺序合并，最终传递给纯逻辑处理函数的，永远都是一个清洗干净、合并规整的抽象“输入数据集合”。

---

## 3. MonoPLC 详细架构实现

### 3.1 数据定义与单位元 （The Types & Identity）

我们首先定义单一的 `DUT_Effect` 结构体，它是纯粹的数据声明（Declarative），而不是可执行的动作动作（Imperative）。
```iecst
TYPE DUT_Effect :
STRUCT
	EType   : ENUM_Effect_Type; // 例如 EFF_VALVE_CTRL, EFF_IOT_PUB
	Target  : STRING[32];       // 对象，例如 'CoolingValve'
	Value   : REAL;             // 例如 1.0 (TRUE/ON)
END_STRUCT
END_TYPE
```

接着，利用它组合出代表整个状态集或行为集的 `DUT_Effect_Monoid`：
```iecst
TYPE DUT_Effect_Monoid :
STRUCT
	Count   : INT := 0; 
    Effects : ARRAY[1..20] OF DUT_Effect;
END_STRUCT
END_TYPE
```
其中，`FC_EmptyEffect()` 函数返回一个 `Count = 0` 且清零初始化的结构体，充当了代数中的**单位元**。

### 3.2 组合函数 (The Combinator)

`FC_CombineEffects` 函数充当了 Monoid 的那个二元运算符号 $\circ$：
```iecst
FUNCTION FC_CombineEffects : DUT_Effect_Monoid
VAR_INPUT
    M1 : DUT_Effect_Monoid;
    M2 : DUT_Effect_Monoid;
END_VAR
// ... 将两个数组合并，返回一个新的结构体
```
有了它，任何复杂的复合动作，最终都可以规约为**纯天然的数据打包**。

> [!NOTE]
> 在本项目的示例代码中，我们采用的是最直观的 **FIFO 定长数组追加** 方式来实现 Effect 的结合律（即 `M1 + M2` 会把 `M2` 里的事件依次排在 `M1` 的末尾）。
> 
> 但请注意：**这并不是唯一的合并方式。** 只要满足数学上的封闭性和结合律，在不同的应用场景中，你可以采用：
> - **环形缓冲区 (Ring Buffer)** 拼接，用于极高频场景以避免大量数组循环拷贝开销。
> - **链表 (Linked List)** 拼接，用于支持动态内存分配的现代控制系统 (如 TwinCAT 3 的 `__NEW`)。
> - **带优先级的去重合并**（例如 `M1` 里有一个关阀门，`M2` 里也有一个，合并时丢掉一个重复的），只要逻辑自圆其说。
> 
> Monoid 架构设计的重点在于**接口的抽象和约束**，它从根本上屏蔽了底层数据结构是如何堆叠的物理细节。

### 3.3 架构三阶段：洋葱圈模型

我们将 PLC 扫描周期强行一分为三，物理隔离。

#### 阶段 A：收集与折叠输入 (Collect & Fold)
负责与底层物理接口与外部异步环境解耦。它从不同任务（如 HMI Task, 网络通信 Task）里无锁地消费异步队列里的输入事件队列。
在这一步，利用结合律：`Total_Input_Monoid := Input1 + Input2 + ... `
不管来源有多复杂，最终都被折叠成了一个统一的 `DUT_Effect_Monoid` 类型传递给核心。

#### 阶段 B：业务核心 (The Pure Core)
**只计算，不动作！**
这被固化在预设的纯函数块 `FB_SimpleLogic` 中。这是整套系统的中央控制器（纯业务逻辑运算器）。
```iecst
Inst_Logic(
	Current_Temp := GVL.Net_Temperature, 
	Current_Time := TimeStr,
	Inputs_In    := Total_Input_Monoid
);
Total_Out_Monoid := Inst_Logic.Effects_Out;
```
在这个纯粹的环境中：
- 开发者不需要考虑网络阻塞等外部环境因素；
- 开发者不需要对全局 IO 地址直接取数写数；
- 这个功能块是可以离开 PLC 环境直接剥离出去进行**单元测试（Unit Testing）**的！因为它是严格的纯数据映射 `y = f(x)`。
  - **怎么做到？** 在传统的 PLC 测试中，你必须连上真实的 PLC 硬件，或者开启繁重的外围仿真环境去“拨动”映射的输入变量（这通常被称为“在回路测试 HIL”）。但在 MonoPLC 中，你只需要在测试框架（比如 TcUnit）里声明一个 `Test_Logic : FB_SimpleLogic;` 实例。
  - 然后人为捏造参数喂给它：`Test_Logic(Current_Temp:=85.0, Input_Event:=Mock_Tick_Event);`
  - 接着直接对它的输出进行断言：`AssertTrue(Test_Logic.Effects_Out.Effects[1].EType = EFF_VALVE_CTRL);` 
  - 因为它根本不碰真实的 `Q0.0` 物理输出针脚，也不调用引起时间挂起的网络动作库，测试它就像测试一个简单的加法函数一样瞬间完成，可以被完美集成到现代软件工程的 CI/CD（持续集成）流水线里！

#### 阶段 C：副作用路由 (The Router/Dispatcher)
拿到 `Total_Out_Monoid` 结果集后，路由层开始执行真实的物理操作与底层功能调用。
- 取出阀门类型的 Effect，直接映射到对应内存地址的输出变量（`Q0.0` 等）。
- 取出带网络延迟的 MQTT Payload，推到一个纯异步排程的队列指针后面，让后台运行的异步 IoT Task 从容地排队执行网络发送。

---

## 4. MonoPLC 架构的极大优势细节

### 4.1 核心层免疫一切网络/时序阻塞
传统的网络发送代码（如调用 HTTP REST API）可能会占用 100~500 毫秒。如果写在实时主干代码里，会干扰代码运行。
在 MonoPLC 中，纯逻辑 `FB_SimpleLogic` 永远只耗费不到 1 毫秒计算并吐出了 `Target='mqtt'` 的 Effect 结构体。随后路由层秒速把它丢进带有指针锁的 `Async_Queue` 中跳出。网络发送库可以放在一个极低优先级（甚至 500ms Cycle）的副程式里慢慢轮询队列（Pop）。主代码拥有了类似于 Javascript Promise 级别的**非阻塞特性**。

### 4.2 事件幂等性与丧尸漏洞根除
在分散的传统 PLC 逻辑中，经常存在隐式的状态耦合与竞态条件：多处代码可能同时操作输出，导致最后覆盖者赢。此外，如果在停机命令发出后，下一行检测到水温高又将执行器打开，就形成了所谓的“丧尸状态机”——代码本质上沦为了基于全局状态的条件反射。

当用户通过 HMI 按下一次“Stop”，在上一代代码里这就对应着这凌乱的覆盖冲突。
在这个新架构中，如果 HMI 送进来一个 `EFF_IOT_CMD_STOP` 的 Effect，纯逻辑把它解析出来后，做出的响应不是“动作语句”，而是“声明输出”：
```iecst
System_En := FALSE;
Eff_Valve := FC_ValveEffect('CoolingValve', FALSE);
```
由于这里输出的不是动作命令，而是目标状态陈述（把冷却阀门定在 FALSE 状态），因此这天生是**幂等（Idempotence）**的。哪怕 HMI 卡死连送了二十个停机指令，也只等于收到一个指令。
同时，利用纯分析数据的遍历性，业务内部状态（如 `System_En`）会强行切断因温度跳变而产生的寄生反射开启，**从源头根治了最臭名昭著的丧尸状态机。**

### 4.3 “时间旅行”与重放调试 (Time-Travel Debugging)
既然外界所有的刺激，都已经由阶段 A 变成了统一内存格式的 `Input_Monoid`；所有的行为，都变成了记录在内存中的 `Total_Out_Monoid`。
这就意味着我们可以随意加入“录制功能”！
我们可以把每一个大周期的 `[环境时间, 环境温度, Inputs_In]` 当作切片记录下来，在出了重大停机事故后，将这些文件导入回虚拟的 PLC 运行环境中重新跑一遍。因为 `FB_SimpleLogic` 是 100% 绝对纯净无外部调用的函数，所以给定一模一样的输入，**它一定会100%一字不差地复现事故现场的决定**！

### 4.4 水平扩展：任意新增设备而不碰主线循环
传统项目加多一个按钮，需要在各种 IF 分支里狂塞 `OR Btn3 OR Btn4`，一旦接外网还会多出大量的边缘条件甚至指针错误。
利用 Monoid：HMI 按下 Start 按钮？没问题，把它组装成一个带有 `'Start'` 类型标签的 `Input_Effect` Monoid 扔进共享队列里。因为此时结合律又发挥了威力：我们只是在做数学上的**集合加法**！
主控函数的轮询逻辑（`FOR i := 1 TO Inputs_In.Count DO`）甚至连一个标点符号都不需要动，它自己就能处理所有并发的新输入。

### 4.5 终极大一统：神圣单线程事件循环 (The Grand Event Loop)
起初，我们在工业控制中还依然保留着“输入阶段 (Collect)”和“输出阶段 (Dispatch)”的概念。但是，既然输入的外部刺激命令（Start/Stop）和输出的副作用（开关阀门、发日志）在数据结构上一模一样，并且都属于同一个代数空间，那为什么我们还要区分它们？

**在 MonoPLC 的最极形态中，我们彻底抹除了“输入和输出”的区别，一切皆为事件（Event）。**

我们将 `Control_LOOP` 彻底重构为了一个类似 **Node.js 引擎** 或 **浏览器的 V8 JavaScript 事件循环** 的架构：

我们在 PLC 本地分配了一个极高频的微秒级循环队列 `Event_Bus_Queue`：
1. **注入微扰（Tick）**：每个 PLC 扫描周期开始，我们将网络指令和代表环境物理时间流逝的 `EFF_SYSTEM_TICK` 扔进队列。
2. **事件总线轮询（The Router）**：
   开启一个 `WHILE 队列不为空` 的魔鬼循环，弹出一个事件就分发一次：
   - 碰到物理路由（如 `EFF_VALVE_CTRL`）：直接映射驱动硬件。
   - 碰到异步路由（如 `EFF_IOT_PUB`）：丢给后台非实时网络任务。
   - 碰到业务路由（如 `EFF_SYSTEM_TICK` 或 `EFF_IOT_CMD_START`）：将这个**单一事件**传入纯函数大脑 `FB_SimpleLogic` 去计算。
3. **事件递归（Event Recursion）**：
   最绝妙的一步在于，`FB_SimpleLogic` 大脑计算完后产生的“输出动作（Effects）”，我们**绝不立刻执行**，而是将它们**全部重新推送回 `Event_Bus_Queue` 的尾端**！循环引擎会在下一轮继续把它们弹出来，分配给物理路由或网络路由去执行。

这种架构下，业务逻辑之间天然形成了无穷尽的串联闭环。未来如果你想实现“延迟 10 秒后触发重置”，你甚至不需要增加物理定时器，你只需要让系统在条件达到时吐出一个挂了延迟标签的事件回总线即可！

自此，**MonoPLC 脱胎换骨，从一个“循序渐进的电气继电器扫描器”，变成了一个全异步、全解耦的现代 Actor 通信模型引击！**

## 5. 总结

MonoPLC 证明了抽象的函数式编程和数学概念不仅不虚无缥缈，反而是拯救像 PLC（可编程逻辑控制器）这种极端要求稳定性、却极易陷入复杂逻辑耦合底层硬件的终极解法。它用**数据流代数**替换了**状态变迁图**，将不确定的物理世界严格隔离在纯逻辑的边界之外。
