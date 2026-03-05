# ProofOfAction: 防黑客安防说明书 (Security Architecture)

为了对抗极其复杂的攻击环境（例如中间人、代理劫持、录屏重放乃至结果篡改），ProofOfAction (POA) 架构在 API 层和前端环境部署了四道纵深防御围墙：

## 1. 强制限流抢攻防御 (Rate Limiting)
- **实现原理**：基于 `slowapi` 和令牌桶算法对 IP 层限流。
- **阈值配置**：
  - `/debug/markets`: `10次/分钟` (低算力常规接口)
  - `/analyze`: `2次/分钟` (高算力消耗，调用 Gemini 多模态识别引擎)
- **防御目标**：防止攻击脚本高频爆发请求耗尽服务器计算力或 AI 大模型配额 (DDoS / Token Exhaustion Attack)。

## 2. 客户端零信任强校验 (EIP-712 Verification Middleware)
- **实现原理**：强制使用 EIP-712 标准要求调用方（通过外接 Web3 钱包）对“挑战规则(Rule)”和“发包时间(Timestamp)”进行密码学数字签名。
- **中间件校验逻辑** (`src/api/auth.py`)：在 /analyze 进行计算前，拦截请求并检查前端表单中夹带的 `client_address`，`client_signature` 与 `client_timestamp`。
- **防重放攻击 (Replay Attack)**：服务器获取客户端签名时的时间戳，判断其是否超时（目前设定为 300 秒）。对于过期重放数据一律拦截并返回 `HTTP 401 Unauthorized`。
- **防御目标**：确认判决动作请求方的真实身份，防范未授权代理发包和拦截器伪造。

## 3. 防结果篡改的数据密封链 (Backend Result Hashing)
- **实现原理**：在综合分析结果（包含真实性校验、进球数、置信度及上链存证）组装完毕后，服务端向其附加一个私密盐 (`backend_salt_POA`) 计算 SHA-256 哈希，附加在返回值 `backend_signature` 内。
- **防御目标**：确保数据在从服务器到客户端设备之间的生命周期内不会被破坏。如果中间人尝试将 `is_goal: false` 改为 `true`，因其没有后端盐作为掩码生成新的 `backend_signature` 会立刻导致前端校验失败。

## 4. 二进制数据传输混淆 (MessagePack Encrypted Transfer)
- **实现原理**：摒弃传统的 HTTP JSON 明文交流，在 `/analyze` 的最后出口通过 `msgpack` 包将所有内容（包括系统异常）打平并强制压缩成混杂了数据结构的 `application/x-msgpack` 二进制流。
- **WASM 引擎配合**：前端的 `window.fetch` 请求获取的将是 `ArrayBuffer` 格式，交由 `@msgpack/msgpack` WASM 解释器进行内存还原与展示。
- **防御目标**：彻底摧毁中间人 (MITM) 黑客拦截抓包后通过常规 JSON 库轻松解析、修改和调试的能力。在没接驳特定的 SDK 或 WASM 的前提下，截包得来的将是无效的乱码字节组合。

---
**核心安全设计原则**：*在任何无法完全掌控网络栈的环境下，默认所有外部输入都充满敌意，永远通过“防伪验证”、“签名背书”和“通道混淆”保障 AI 判断逻辑的不可靠干预。*
