# Kiến trúc tổng thể

> **Tóm tắt.** Một backend Python chạy trong mạng nhà. Mọi nguồn lệnh (nút, 3D, AI, automation) đi qua cùng `ControlService` →
> Policy → Gateway, và chỉ thành công khi thiết bị báo lại. **Đã có:** domain, policy, execution, simulator, Postgres
> store, REST API. **Kế hoạch:** đăng nhập, WebSocket, Home Assistant, web, 3D, agent.

**Trạng thái:** kiến trúc mục tiêu. Mỗi thành phần ghi rõ **Đã có** (có code và test) hoặc **Kế hoạch** (mã task trong
[ROADMAP.md](ROADMAP.md)). Cập nhật 2026-11-23.

## 1. Nguyên tắc

1. **Một đường điều khiển duy nhất.** Nút bấm trên dashboard, điểm bấm trên mô hình 3D, AI Agent và automation đều tạo
   `CommandRequest` và đi qua cùng `ControlService` → Policy → Gateway. Không có đường tắt.
2. **Chỉ báo thành công khi có bằng chứng.** `202 Accepted` nghĩa là đã nhận lệnh. `SUCCEEDED` chỉ có khi thiết bị báo lại
   giá trị khớp lệnh. Hết thời gian chờ là `TIMED_OUT` (chưa xác minh), không phải thành công.
3. **AI đề xuất, policy quyết định.** LLM không bao giờ gọi gateway. Policy là hàm thuần, xác định, có test.
4. **Local-first.** Backend chạy trong mạng nhà, nói chuyện trực tiếp với Home Assistant trong LAN. LLM cloud là tùy chọn;
   mất Internet thì điều khiển trực tiếp và automation vẫn chạy.
5. **3D là một view.** Digital Twin đọc trạng thái và gửi lệnh qua API như mọi client khác. Nó không biết gateway là gì.
6. **Monolith có module rõ ràng.** Một tiến trình Python (API và agent chung tiến trình), tách module theo trách nhiệm. Chỉ tách service khi có
   số đo cho thấy cần.

## 1b. Tổng quan một hình

```mermaid
flowchart LR
    user["Người dùng<br/>web · Kế hoạch P0-11"] -->|HTTPS| be["Backend Python<br/>· Đã có"]
    be -->|LAN| gw["Gateway<br/>simulator · Đã có<br/>Home Assistant · P0-09"]
    gw --> dev["Thiết bị trong nhà"]
    be --> db[("Postgres<br/>· Đã có")]
    be -.->|tùy chọn| llm["LLM<br/>· Kế hoạch P0-15"]
```
*Hình 1. Ngữ cảnh hệ thống. Chi tiết từng module ở §2.*

## 2. Thành phần

```mermaid
flowchart LR
    subgraph client["Trình duyệt"]
        dash["Dashboard · Kế hoạch P0-11"]
        twin["Digital Twin 3D · Kế hoạch P0-12"]
        chat["Chat Trợ lý · Kế hoạch P0-15"]
    end
    subgraph backend["Backend Python · một tiến trình, chạy trong mạng nhà"]
        api["HTTP API · Đã có<br/>WebSocket · Kế hoạch P0-08"]
        identity["Danh tính và RBAC theo nhà<br/>dev identity · Đã có / auth thật · P0-07"]
        agent["AI Agent LangGraph<br/>mẫu · Đã có / planner · P0-15"]
        scenes["Scene · P0-14<br/>Automation · P1"]
        control["ControlService · Đã có"]
        policy["Policy Engine · Đã có"]
        exec["Execution và xác minh · Đã có"]
        state["State store · Đã có, cache không lưu"]
        audit["Command store và audit · Đã có<br/>bộ nhớ hoặc Postgres"]
        layout["Home layout và placement 3D<br/>seed · Đã có / lưu và sửa · P0-13"]
    end
    subgraph gw["Gateway adapters"]
        sim["Simulator · Đã có"]
        ha["Home Assistant WebSocket · P0-09"]
    end
    db[("Postgres · lệnh, idempotency, audit: Đã có<br/>cấu hình nhà: P0-10")]
    llm["LLM cloud hoặc local · tùy chọn"]
    hass["Home Assistant trong LAN"]
    devices["Thiết bị thật<br/>Zigbee · Matter · Wi-Fi"]

    dash & twin & chat <-->|HTTPS / WSS| api
    api --> identity
    identity --> control
    identity --> agent
    identity --> layout
    agent -.->|ngữ cảnh tối thiểu| llm
    agent -->|CommandRequest origin=agent| control
    scenes -->|CommandRequest| control
    control --> policy
    control --> exec
    exec --> sim
    exec --> ha
    ha <--> hass <--> devices
    sim -->|StateReport| control
    ha -->|StateReport| control
    control --> state
    control --> audit
    state & audit & layout -.-> db
```
*Hình 2. Thành phần chi tiết và trạng thái triển khai.*

## 3. Ranh giới module (mã nguồn)

| Module | Trách nhiệm | Được import | Không được import |
|---|---|---|---|
| `smarthome.domain` | Home, Floor, Room, Device, Capability, Placement, trạng thái quan sát, `DeviceView` | pydantic | mọi module khác |
| `smarthome.gateway` | Hợp đồng `DeviceGateway`, `StateReport`; adapter simulator, HA | `domain` | `control`, `api`, `agent` |
| `smarthome.control` | Policy, vòng đời lệnh, xác minh, xác nhận; định nghĩa `CommandRecord`, `AuditEvent` | `domain`, `gateway.base`, `persistence.base/memory`, `core.logging` | `api`, `agent`, FastAPI |
| `smarthome.persistence` | `CommandStore` (bộ nhớ, Postgres), migration, CLI | `control.commands`, `control.audit`, `domain`, `core` | `api`, `agent`, `gateway`, FastAPI |
| `smarthome.agent` | Hiện: mẫu chào hỏi. Mục tiêu: hiểu câu nói, lập kế hoạch, tool đọc trạng thái | hiện: chỉ `agent`; mục tiêu: `domain`, `control` (qua facade) | `gateway`, FastAPI |
| `smarthome.api` | HTTP/WS, danh tính, ánh xạ lỗi sang mã HTTP | tất cả ở trên | — |
| `web/` (P0-11) | Dashboard, 3D, chat | chỉ HTTP/WS API | — |

```mermaid
flowchart LR
    api["api"] --> agent["agent"]
    api --> control["control"]
    api --> persistence["persistence"]
    api --> gateway["gateway"]
    api --> demo["demo_home"]
    api --> domain["domain"]
    control --> persistence
    persistence --> control
    control --> gateway
    control --> domain
    persistence --> domain
    gateway --> domain
    demo --> domain
    control --> core["core"]
    persistence --> core
    api --> core
    api -.-> auth["auth · P0-07"]
    auth -.-> persistence
    auth -.-> control
    auth -.-> domain
```
*Hình 2b. Phụ thuộc giữa các package. Nét liền: đã kiểm bằng cách đọc import của mã nguồn (commit `0461606`). Nét đứt:
**kế hoạch**, chưa có code. `domain` và `core` không import package nào khác của `smarthome`; `agent` chỉ import chính nó.*

`control` và `persistence` phụ thuộc hai chiều ở mức package: `persistence` lưu các model do `control` định nghĩa
(`control.commands`, `control.audit`), `control` dùng giao diện `CommandStore`. **Không tạo vòng import ở mức file** (đã kiểm
bằng đồ thị import 39 module); chấp nhận cho P0, xem DR-L06 trong [Design Review](DESIGN_REVIEW_2026-11.md). Vòng duy nhất
tìm thấy là `agent/__init__` ↔ `agent/graph` (DR-L09): chạy được vì `nodes` là submodule, mã scaffold có sẵn, ghi nhận không sửa.

Sprint 1 thêm: `MembershipSource` (giao diện do `control` định nghĩa, `persistence`/`auth` cài đặt) thay dict thành viên tĩnh
trong `ControlService`; `persistence.home_config` và model `GatewayConnection` + `validate_home_config` trong `domain`
(P0-06b). Chi tiết: [LLD P0-06](lld/P0-06_PERSISTENCE.md), [LLD P0-07](lld/P0-07_AUTH_RBAC.md).

## 4. Luồng điều khiển thiết bị (Đã có)

```mermaid
sequenceDiagram
    participant UI as Client (nút, 3D, agent)
    participant API as API
    participant CS as ControlService
    participant P as Policy
    participant GW as Gateway
    participant D as Thiết bị
    UI->>API: POST /homes/{id}/commands (Idempotency-Key)
    API->>CS: submit(actor, request)
    CS->>P: evaluate(actor, device, request, view)
    alt DENY
        CS-->>API: REJECTED + lý do → 403/404/409/422
    else REQUIRE_CONFIRMATION
        CS-->>API: AWAITING_CONFIRMATION → 202
        Note over UI,CS: Chủ nhà gọi /confirmation, policy chạy lại
    else ALLOW
        CS->>CS: ghi DISPATCHING vào store (trước khi gửi)
        CS->>GW: send(..., correlation_id) (đăng ký pending trước)
        GW-->>CS: đã nhận → DISPATCHED
        CS-->>API: 202
        D-->>GW: trạng thái mới
        GW-->>CS: StateReport
        CS->>CS: khớp giá trị → SUCCEEDED, hoặc hết giờ → TIMED_OUT
    end
    UI->>API: GET /commands/{id} (WS ở P0-08)
```

*Hình 3. Luồng một lệnh qua API, policy, gateway.*

```mermaid
stateDiagram-v2
    [*] --> requested
    requested --> rejected: policy từ chối
    requested --> awaiting_confirmation: cần xác nhận
    awaiting_confirmation --> requested: chủ nhà duyệt (claim)
    awaiting_confirmation --> cancelled: chủ nhà từ chối
    awaiting_confirmation --> expired: quá hạn
    requested --> dispatching: policy cho phép, ghi DB trước
    dispatching --> rejected: thiết bị bận
    dispatching --> failed: gateway từ chối hoặc không ghi được DB
    dispatching --> dispatched: gateway nhận hoặc ack trễ
    dispatching --> succeeded: báo cáo xác minh tới sớm
    dispatched --> succeeded: đạt chính sách xác minh
    dispatched --> timed_out: hết giờ chờ
    requested --> interrupted: restart
    dispatching --> interrupted: restart
    dispatched --> interrupted: restart
    rejected --> [*]
    cancelled --> [*]
    expired --> [*]
    failed --> [*]
    succeeded --> [*]
    timed_out --> [*]
    interrupted --> [*]
```
*Hình 4. Vòng đời `CommandStatus` (khớp `src/smarthome/control/commands.py` và `execution.py`). `timed_out` và
`interrupted` nghĩa là **chưa xác minh**, không phải thất bại.*

Ba mốc được phân biệt rõ: **API đã nhận** (`REQUESTED`/202), **gateway đã nhận** (`DISPATCHED`), **thiết bị đã xác nhận**
(`SUCCEEDED`). Trong lúc chờ, `DeviceView` giữ giá trị đã báo gần nhất và hiện `pending_value` bên cạnh.

## 5. Luồng AI Agent (Kế hoạch P0-15)

```mermaid
flowchart LR
    msg["Câu tiếng Việt"] --> intent["Hiểu ý định<br/>LLM hoặc luật"]
    intent --> ctx["Xác định phòng, ngữ cảnh"]
    ctx --> read["Tool đọc trạng thái<br/>chỉ đọc"]
    read --> plan["Kế hoạch có cấu trúc<br/>danh sách CommandRequest"]
    plan --> validate["Validate theo capability"]
    validate --> preview["Policy xem trước từng bước"]
    preview --> show["Hiện kế hoạch · người dùng bấm Chạy"]
    show --> run["Scene executor → ControlService<br/>origin=agent"]
    run --> report["Báo kết quả theo bằng chứng"]
```

LLM chỉ có tool đọc và tool `propose_plan`. Không có tool nào thực thi. Thực thi bắt đầu từ một request riêng của người dùng
("Chạy"), đi qua policy với `origin=agent`. Chi tiết: [AI_AGENT_DESIGN.md](AI_AGENT_DESIGN.md).

## 6. Luồng cập nhật 3D (Kế hoạch P0-08, P0-12)

```mermaid
flowchart LR
    gw["Gateway StateReport"] --> cs["ControlService<br/>cập nhật state store"]
    cs --> ws["WebSocket theo nhà<br/>device.view, command.updated"]
    ws --> store["Store phía client"]
    store --> twin["Scene 3D: vẽ lại overlay của thiết bị đó"]
    store --> dash["Dashboard"]
```

Mô hình 3D không suy ra trạng thái từ lệnh đã gửi. Nó chỉ vẽ `DeviceView.capabilities[].status/value/pending_value`.

## 7. Luồng kiểm soát an toàn

| Lớp | Cơ chế | Trạng thái |
|---|---|---|
| Danh tính | Thành viên của đúng nhà; người ngoài nhận 404 | ✅ Đăng nhập thật (phiên phía server, CSRF) và danh tính dev chỉ ở `local`/`test` ([LLD P0-07](lld/P0-07_AUTH_RBAC.md)) |
| Thành viên động | Vai trò tra DB mỗi request và mỗi lần duyệt lệnh (`MembershipSource`); người duyệt được đọc lại lúc bấm (DR-H08 đã đóng) | ✅ `control/membership.py`, `auth/store.py`, `test_membership_source.py`, `test_auth_api.py` |
| Cổng môi trường | Chỉ `local`/`test` được dễ dãi (bộ nhớ, danh tính dev, seed-demo, `/docs`, route simulator); `Literal` từ chối giá trị lạ (DR-H03 đã đóng) | ✅ `core/config.py`, `test_environment_gate.py` |
| Default-deny | Mọi route trừ `/health`, `login`, `logout` cần phiên, gồm `/api/v1/agent/invoke` | ✅ `api/security.py`, `test_every_route_is_denied_without_a_session` |
| Capability | Thiết bị có hỗ trợ không, có ghi được không, giá trị có hợp lệ không | Đã có |
| Vai trò | `min_role` theo capability (khách không điều khiển được) | Đã có |
| Kết nối | Thiết bị offline → từ chối, không gửi mù | Đã có |
| Xác nhận | Fail-closed: capability `HIGH_POWER`/`SECURITY` cần xác nhận cho mọi giá trị trừ `safe_values`; chủ nhà xác nhận phía server, có hạn, dùng một lần (claim nguyên tử), chạy lại policy | Đã có |
| Automation | Không được thực hiện thao tác cần xác nhận | Đã có |
| Chống trùng | `Idempotency-Key` + fingerprint (khác nội dung → 409); một lệnh đang gửi mỗi thiết bị + capability (bộ nhớ và unique index DB) | Đã có |
| Xác minh | Báo cáo phải được state store chấp nhận (không sai thứ tự), khớp giá trị, và có correlation đúng hoặc không cũ hơn lúc gửi | Đã có |
| Khôi phục | Lệnh dở dang khi restart → `interrupted`, không gửi lại; đối soát sau snapshot. Chỉ một backend được dùng một DB (advisory lock), để recovery không đóng nhầm lệnh của tiến trình khác | Đã có |
| Lỗi lưu trữ | Ghi trước khi gửi phải thành công, nếu không thì không gửi (`503 STORE_UNAVAILABLE`); ghi sau khi gửi là best effort, có log, không làm kẹt thiết bị; store tự kết nối lại ở lần gọi sau | Đã có |
| Chống bật/tắt liên tục | Cooldown, hysteresis, giới hạn tần suất | P1 (AUTOMATION_ENGINE.md) |
| Audit | Mọi chuyển trạng thái của lệnh; Postgres chặn UPDATE/DELETE bằng trigger | Đã có |

## 8. Triển khai mục tiêu (local-first)

Chi tiết, sơ đồ và hiện trạng đã kiểm: [DEPLOYMENT.md](DEPLOYMENT.md), [ADR 0008](adr/0008-local-deployment-topology.md).

| Thành phần | Chạy ở đâu |
|---|---|
| Home Assistant | Thiết bị riêng trong nhà (HA OS/mini PC), do chủ nhà quản lý |
| Backend + Postgres + web tĩnh | Docker Compose trên cùng mini PC hoặc máy khác trong LAN |
| Truy cập từ xa | Tùy chọn qua VPN/tunnel; không mở cổng trực tiếp |
| LLM | Cloud qua API key (tùy chọn) hoặc model local; khi không có, agent dùng bộ lập kế hoạch theo luật |

## 9. API contracts

Hợp đồng HTTP hiện có: [api/control-api.md](api/control-api.md). Thiết kế API đăng nhập: [LLD P0-07](lld/P0-07_AUTH_RBAC.md#6-api).
Realtime: [ADR 0007](adr/0007-realtime-websocket-per-home.md) (Proposed). Schema dữ liệu: [DATABASE_DESIGN.md](DATABASE_DESIGN.md).

## 10. Tài liệu liên quan

[DIGITAL_TWIN_3D.md](DIGITAL_TWIN_3D.md) · [IOT_INTEGRATION.md](IOT_INTEGRATION.md) ·
[AI_AGENT_DESIGN.md](AI_AGENT_DESIGN.md) · [AUTOMATION_ENGINE.md](AUTOMATION_ENGINE.md) ·
[SECURITY_AND_SAFETY.md](SECURITY_AND_SAFETY.md) · [ADR](adr/README.md) · [Design Review](DESIGN_REVIEW_2026-11.md) ·
[Traceability](TRACEABILITY.md)
