# Smart Home Future

> **[Tổng quan sản phẩm](docs/project-overview.md)**: vấn đề, người dùng đầu tiên, bốn wireframe web và phạm vi theo mốc.

| Trạng thái 2026-11-23 | |
|---|---|
| ✅ Đã có | Đường điều khiển thiết bị: policy fail-closed, xác minh theo bằng chứng, idempotency, lưu bền Postgres, REST API |
| 🧪 Giả lập | Mọi thiết bị hiện là **simulator**; chưa điều khiển thiết bị thật |
| ⏳ Chưa có | Đăng nhập thật, WebSocket, Home Assistant, web UI, Digital Twin 3D, AI Agent thật |
| 📊 Tiến độ P0 | **5/18 task đạt**, 1 một phần · [Báo cáo tiến độ](docs/PROGRESS.md) |

```mermaid
flowchart LR
    user["Người dùng<br/>web · Kế hoạch P0-11"] -->|HTTPS| be["Backend Python<br/>· Đã có"]
    be -->|LAN| gw["Gateway<br/>simulator · Đã có<br/>Home Assistant · P0-09"]
    gw --> dev["Thiết bị trong nhà"]
    be --> db[("Postgres<br/>· Đã có")]
    be -.->|tùy chọn| llm["LLM<br/>· Kế hoạch P0-15"]
```
*Hình 1. Tổng quan. Chi tiết: [Kiến trúc](docs/ARCHITECTURE.md).*

**Smart Home Future** là quản gia số cho ngôi nhà: nói hoặc gõ yêu cầu, xem Trợ lý định làm gì với từng thiết bị, lưu sở
thích cho những sinh hoạt lặp lại.

**Repo:** `smart-home-future` (public), tách từ repo gốc với baseline sạch, không mang lịch sử cũ; xem
[Repo và nguồn gốc nội dung](docs/product/repo-migration.md).

**Scaffold kỹ thuật:** **FastAPI** làm HTTP API, **LangGraph** điều phối agent. Tên package `smarthome`, tiền tố `SMARTHOME_` và các
lệnh bên dưới giữ nguyên tới lượt triển khai. Luồng mẫu chạy hoàn toàn bằng dữ liệu cục bộ nên **không cần API key**.

```
HTTP POST /api/v1/agent/invoke ──► FastAPI (validate) ──► LangGraph: classify ──► respond ──► {"reply": ...}
```

## Cấu trúc

| Thư mục | Nội dung |
|---|---|
| `src/smarthome/agent/` | LangGraph: `state.py` (trạng thái), `nodes.py` (các bước), `tools.py` (công cụ nhận diện ý định), `graph.py` (build + compile) |
| `src/smarthome/api/` | FastAPI: `main.py` (app), `routes/` (endpoint), `schemas.py` (Pydantic), `deps.py` (cung cấp graph đã compile) |
| `src/smarthome/domain/` | Mô hình nhà, phòng, thiết bị, capability và trạng thái quan sát (Device Abstraction) |
| `src/smarthome/gateway/` | Hợp đồng gateway và simulator (Home Assistant: kế hoạch) |
| `src/smarthome/control/` | Policy Engine, Execution Engine (`ControlService`), audit log |
| `src/smarthome/demo_home.py` | Nhà mẫu **giả lập**: 2 phòng, 10 thiết bị, 3 người dùng |
| `src/smarthome/core/` | Cấu hình (`pydantic-settings`) và logging |
| `src/smarthome/evaluation.py` | Logic eval dùng chung cho runner và eval test |
| `tests/unit`, `tests/integration`, `tests/eval` | Test node/graph, test endpoint, test hồi quy |
| `eval/datasets/`, `eval/scripts/` | Dataset JSONL mẫu và runner |
| `docs/` | Sản phẩm, kiến trúc, hợp đồng API, roadmap |

## Cài đặt

Yêu cầu: Python 3.12.

**Linux/macOS**

```bash
python3.12 -m venv .venv
source .venv/bin/activate
python -m pip install -e ".[dev]"
cp .env.example .env   # tuỳ chọn
```

**Windows PowerShell**

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -e ".[dev]"
Copy-Item .env.example .env   # tuỳ chọn
```

Biến môi trường dùng tiền tố `SMARTHOME_` (xem `.env.example`): `SMARTHOME_APP_NAME`, `SMARTHOME_ENVIRONMENT`,
`SMARTHOME_LOG_LEVEL`, `SMARTHOME_LLM_API_KEY` (để trống, dành cho node LLM sau này).

## Chạy ứng dụng

```bash
uvicorn smarthome.api.main:app --reload --host 127.0.0.1 --port 8000
```

Tài liệu tương tác: http://127.0.0.1:8000/docs

Chạy agent không qua HTTP:

```bash
python -m smarthome.agent "xin chào"
```

## Gọi API

```bash
curl http://127.0.0.1:8000/health
curl -X POST http://127.0.0.1:8000/api/v1/agent/invoke \
  -H "Content-Type: application/json" \
  -d '{"message": "xin chào"}'
# {"reply":"Xin chào! Tôi là trợ lý Smart Home Future. Tôi có thể giúp gì cho bạn?"}
```

> Trên Windows (Git Bash/cmd), curl có thể không gửi tham số `-d` dưới dạng UTF-8, khiến server
> trả `400`. Khi đó hãy lưu body vào file UTF-8 và gửi bằng `--data-binary @body.json`.

PowerShell:

```powershell
Invoke-RestMethod http://127.0.0.1:8000/health
Invoke-RestMethod -Method Post -Uri http://127.0.0.1:8000/api/v1/agent/invoke `
  -ContentType "application/json; charset=utf-8" `
  -Body ([System.Text.Encoding]::UTF8.GetBytes('{"message": "xin chào"}'))
```

Chuỗi rỗng hoặc chỉ có khoảng trắng bị từ chối với mã `422`.

### Control API (thiết bị giả lập)

Đường điều khiển thiết bị đã có với simulator và nhà mẫu `home-demo`. Chưa có đăng nhập thật: bật danh tính dev bằng
`SMARTHOME_DEV_IDENTITY_ENABLED=true` (đã có trong `.env.example`) rồi gửi header `X-Dev-User` (`user-owner`, `user-member`,
`user-guest`). `202` chỉ nghĩa là đã nhận lệnh; đợi `status` thành `succeeded` (đã xác minh), hoặc `timed_out`/`interrupted` (chưa xác
minh). Chi tiết: [Control API](docs/api/control-api.md).

### Lưu bền (PostgreSQL)

Không đặt `SMARTHOME_DATABASE_URL` thì lệnh và audit chỉ nằm trong bộ nhớ (dev). Khi `SMARTHOME_ENVIRONMENT=production`, app từ chối
khởi động nếu thiếu DB hoặc schema chưa đúng phiên bản.

```powershell
$env:SMARTHOME_DATABASE_URL = "postgresql://smarthome:change-me@127.0.0.1:5432/smarthome"
python -m smarthome.persistence status
python -m smarthome.persistence upgrade
```

Backup trước khi nâng cấp (`pg_dump`). Rollback schema (`downgrade --to 0 --confirm-data-loss`) **xóa dữ liệu**. Dọn dữ liệu
quá hạn: `python -m smarthome.persistence purge` (lệnh 90 ngày, audit 365 ngày, cấu hình qua `SMARTHOME_*_RETENTION_DAYS`).

Test Postgres chạy khi có `SMARTHOME_TEST_DATABASE_URL` (server cho phép tạo database), nếu không sẽ bị bỏ qua:

```powershell
$env:SMARTHOME_TEST_DATABASE_URL = "postgresql://postgres@127.0.0.1:5432/postgres"; python -m pytest
```

## Kiểm tra

```bash
ruff check .
ruff format --check .
python -m pytest                       # toàn bộ test
python -m pytest tests/unit            # một nhóm
python -m pytest tests/unit/test_graph.py::test_graph_end_to_end_greeting   # một test
python eval/scripts/run_eval.py        # in "đạt/tổng", mã thoát ≠ 0 nếu có ca lỗi
```

Thêm ca eval: thêm một dòng vào `eval/datasets/agent_regression.jsonl` với các trường
`id`, `message`, `expected_intent`, `expected_reply_contains`. Runner và `tests/eval` cùng đọc file này.

CI (`.github/workflows/ci.yml`) chạy trên mọi push và pull request: Ruff, pytest với Postgres 16 (bắt buộc, không
bỏ qua), eval, và render mọi sơ đồ Mermaid bằng mermaid-cli (`python scripts/render_mermaid.py`).

## Docker

```bash
docker compose up -d --build
curl http://127.0.0.1:8000/health
docker compose down
```

Image chạy bằng user `app` (không phải root) và có `HEALTHCHECK` gọi `/health`.

## Makefile và lệnh PowerShell tương đương

| `make` | PowerShell |
|---|---|
| `make install` | `python -m pip install -e ".[dev]"` |
| `make lint` | `ruff check .; ruff format --check .` |
| `make format` | `ruff check --fix .; ruff format .` |
| `make test` | `python -m pytest` |
| `make eval` | `python eval/scripts/run_eval.py` |
| `make check` | `ruff check .; ruff format --check .; python -m pytest; python eval/scripts/run_eval.py` |
| `make run` | `uvicorn smarthome.api.main:app --reload --host 127.0.0.1 --port 8000` |
| `make docker-build` | `docker compose build` |
| `make up` | `docker compose up -d --build` |
| `make down` | `docker compose down` |

## Tài liệu

Sản phẩm:

- [Tổng quan sản phẩm](docs/project-overview.md)
- [Phạm vi và hành vi](docs/product/scope.md)
- [Giao diện web desktop](docs/product/ui-desktop.md)
- [Repo và nguồn gốc nội dung](docs/product/repo-migration.md)

Nền tảng (định hướng AI Smart Home Digital Twin, cập nhật 2026-11-23):

- [Audit hiện trạng](docs/AUDIT_REPORT.md) · [Roadmap](docs/ROADMAP.md) · [Nhật ký triển khai](docs/IMPLEMENTATION_LOG.md)
- [Tầm nhìn](docs/PRODUCT_VISION.md) · [PRD](docs/PRD.md) · [Kiến trúc](docs/ARCHITECTURE.md) · [Control API](docs/api/control-api.md)
- [Báo cáo tiến độ](docs/PROGRESS.md) · [Design Review 2026-11](docs/DESIGN_REVIEW_2026-11.md) · [ADR](docs/adr/README.md) ·
  [Traceability](docs/TRACEABILITY.md) · [LLD P0-06](docs/lld/P0-06_PERSISTENCE.md) · [LLD P0-07](docs/lld/P0-07_AUTH_RBAC.md) ·
  [Deployment](docs/DEPLOYMENT.md) · [Tiêu chuẩn Visual-first](docs/VISUAL_DESIGN_STANDARD.md) ·
  [Hardening 2026-11](docs/HARDENING_2026-11.md) · [Digital Twin 3D](docs/DIGITAL_TWIN_3D.md) · [IoT](docs/IOT_INTEGRATION.md) · [AI Agent](docs/AI_AGENT_DESIGN.md) ·
  [Automation](docs/AUTOMATION_ENGINE.md) · [An toàn](docs/SECURITY_AND_SAFETY.md) · [Database](docs/DATABASE_DESIGN.md) ·
  [Kiểm thử](docs/TESTING_STRATEGY.md)

Kỹ thuật của scaffold:

- [Hợp đồng API agent mẫu](docs/api/agent-api.md)
