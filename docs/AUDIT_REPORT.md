# Audit Report: hiện trạng repository

> Audit thực hiện trên repo gốc trước khi tách sang repo này. Một số file được nhắc tới (bộ `docs/design/`, ADR,
> `docs/architecture/`, `docs/development-process.md`) không được đưa sang; xem [Repo và nguồn gốc nội dung](product/repo-migration.md).

**Ngày audit:** 2026-11-23 · **Nhánh:** `main` (commit `5ed004b`) · **Người thực hiện:** Claude Code theo brief "AI Smart Home
Digital Twin Platform".

Báo cáo này ghi lại hiện trạng **trước** đợt triển khai đầu tiên, rồi ghi phần đã thay đổi ở §7. Mọi nhận định đều kèm
đường dẫn file hoặc lệnh kiểm chứng.

## 1. Đã đọc những gì

| Nhóm | File |
|---|---|
| Mã nguồn | toàn bộ `src/smarthome/**` (agent, api, core, evaluation): 14 file Python, khoảng 300 dòng |
| Test, eval | `tests/unit`, `tests/integration`, `tests/eval`, `eval/datasets/agent_regression.jsonl`, `eval/scripts/run_eval.py` |
| Cấu hình | `pyproject.toml`, `ruff.toml`, `Makefile`, `Dockerfile`, `docker-compose.yml`, `.env.example`, `.github/workflows/ci.yml`, `.gitignore` |
| Tài liệu hiện hành | `README.md`, `docs/project-overview.md`, `docs/product/*.md` (đang sửa, chưa commit), `docs/development-process.md` |
| Tài liệu kỹ thuật | `docs/architecture/*.md`, `docs/adr/0001…0003`, `docs/api/agent-api.md` |
| Tài liệu cũ | Mục lục và tiêu đề `docs/design/01…07` (đọc cấu trúc, **không** dùng làm nguồn nội dung, xem §5) |
| Git | `git status`, `git log`: 5 commit; 3 file đã sửa và 5 mục chưa track, đều là thay đổi của chủ dự án |

## 2. Kết quả kiểm chứng ban đầu (trước khi sửa)

| Lệnh | Kết quả |
|---|---|
| `python -m pytest -q` | 28 passed |
| `ruff check .` / `ruff format --check .` | đạt / 47 file đã format |
| `python eval/scripts/run_eval.py` | 7/7 |
| Node.js | v22.12.0, npm 11.12.1 có trên máy (chưa có frontend để build) |

## 3. Phân loại từng module

| Module | Hiện trạng | Bằng chứng |
|---|---|---|
| HTTP API | **Chạy được, đã test**, nhưng chỉ có `/health` và `/api/v1/agent/invoke` | `src/smarthome/api/main.py`, `tests/integration/test_api.py` |
| AI Agent | **Chỉ là mẫu**: LangGraph 2 node nhận diện chào/cảm ơn/giúp bằng từ khóa. Không LLM, không tool, không điều khiển | `src/smarthome/agent/nodes.py`, `tools.py` |
| Cấu hình, logging | **Có thể giữ nguyên**: `pydantic-settings`, log chỉ ghi metadata, có test chống lộ nội dung | `src/smarthome/core/*`, `test_invoke_does_not_log_message` |
| Eval | **Chạy được** cho agent mẫu; dataset không liên quan smart home | `src/smarthome/evaluation.py` |
| CI, Docker | **Có code, CI đã từng đạt** (theo `docs/development-process.md`); Docker chưa kiểm lại trong lượt này | `.github/workflows/ci.yml`, `Dockerfile` |
| Mô hình nhà, thiết bị, capability | **Chưa triển khai** | không có file |
| IoT, gateway, Home Assistant, MQTT | **Chưa triển khai** (chỉ có thiết kế) | không có file |
| Policy, HITL, execution, audit | **Chưa triển khai** (chỉ có thiết kế) | không có file |
| Database | **Chưa triển khai**. ADR 0003 đã chọn Postgres nhưng chưa có dependency, schema hay migration | `docs/adr/0003-*.md` |
| Authentication, RBAC | **Chưa triển khai** | không có file |
| Frontend, dashboard | **Chưa triển khai**. Chỉ có wireframe SVG | `docs/assets/ui/*` |
| Digital Twin 3D | **Chưa triển khai**, chưa có thiết kế. Phạm vi hiện hành còn ghi "sơ đồ 2D" | `docs/product/scope.md` §1.1 |
| Automation, memory, energy | **Chưa triển khai** | không có file |

**Kết luận:** repository là một scaffold kỹ thuật sạch (FastAPI + LangGraph, test, lint, CI, Docker) cùng một bộ tài liệu
thiết kế dày. Chưa có chức năng smart home nào chạy được. Không có thành phần nào "nhận là chạy nhưng thực ra là giả".

## 4. Vấn đề kiến trúc và kỹ thuật

1. **Hai hướng sản phẩm song song.** Bộ `docs/design/` (bản gốc: tám thiết bị, hai vai trò, MQTT) và bộ `docs/product/`
   (Smart Home Future: hai phòng, một người dùng, sơ đồ 2D, thiết bị mô phỏng) mô tả phạm vi khác nhau. Brief mới (3D,
   RBAC, thiết bị thật qua Home Assistant ở P0) lại rộng hơn cả hai. Cần một nguồn phạm vi duy nhất → `PRD.md`.
2. **Thiếu lớp trừu tượng thiết bị.** Không có chỗ để policy, agent, 3D và gateway cùng nói về một thiết bị. Đây là
   dependency của mọi tính năng còn lại, nên được làm đầu tiên (§7).
3. **Agent chưa được tách khỏi đường điều khiển** chỉ vì đường điều khiển chưa tồn tại. Rủi ro là xây tool cho LLM trước khi
   có hợp đồng lệnh ổn định.
4. **Không có khái niệm trạng thái đã xác minh.** Cần phân biệt "API nhận lệnh", "gateway đã gửi", "thiết bị đã báo".
5. **Không có xác thực.** Mọi endpoint điều khiển thêm vào đều phải có danh tính và phạm vi theo nhà.
6. **Tên kỹ thuật cũ** (`smarthome`, `SMARTHOME_`) còn khắp nơi; `docs/product/repo-migration.md` §5 đã có kế hoạch đổi tên, chưa làm.
   Lượt này giữ nguyên để không phá test/CI.

## 5. Về tài liệu cũ trong `docs/design/`

`docs/product/repo-migration.md` ghi rằng bộ `docs/design/01…07` bám theo đề bài của chương trình và **chưa rõ quyền dùng
lại**. Vì vậy bộ tài liệu mới trong `docs/` được **viết mới**, không chép nội dung từ `docs/design/`. Các ý tưởng kỹ thuật phổ
biến (vòng đời lệnh có xác minh, xác nhận phía server, phạm vi theo hộ) là kiến thức chung của ngành, được thiết kế lại cho
kiến trúc ở `ARCHITECTURE.md`. `docs/design/` giữ nguyên, không sửa.

## 6. Mức độ phù hợp với mục tiêu sản phẩm

| Câu hỏi | Trả lời |
|---|---|
| Kiến trúc hiện tại có phát triển thành sản phẩm được không? | **Có.** Monolith Python có module rõ (agent không import FastAPI) là nền phù hợp. Không cần microservices |
| Giữ lại | `core/` (config, logging), cấu trúc `src/`, test/eval/CI, Docker non-root, ADR 0002 (agent chung tiến trình), ADR 0003 (Postgres) |
| Cần refactor | `api/main.py` để nhận `Settings` và khởi tạo đường điều khiển (đã làm); agent mẫu sẽ được thay khi làm P0-15 |
| Phụ thuộc chặt | Chưa có, vì mã còn ít. Cần giữ ranh giới mới: 3D và agent chỉ đi qua API/`ControlService` |
| Trùng trách nhiệm | Chưa có trong code. Trong tài liệu: hai bộ phạm vi chồng nhau (§4.1) |
| Cần thêm module | domain, gateway, control (đã thêm); persistence, auth, realtime, HA adapter, web, twin, scenes, automation, memory (roadmap) |
| DB hỗ trợ multi-home? | Chưa có DB. Thiết kế mới đặt `home_id` trên mọi bảng (`DATABASE_DESIGN.md`) |
| 3D tách khỏi IoT? | Mô hình mới: `Placement` chỉ để hiển thị, không bao giờ dùng để điều khiển (`domain/models.py`) |
| AI tách khỏi an toàn? | Mô hình mới: policy là hàm thuần trong `control/policy.py`; agent không có đường nào khác để điều khiển |
| Rủi ro ổn định khi mở rộng | Trạng thái lệnh đang ở bộ nhớ, mất khi khởi động lại; một worker. Xử lý ở P0-06 |

## 7. Thay đổi trong đợt này

Chi tiết từng file, lý do và kết quả kiểm chứng ở [IMPLEMENTATION_LOG.md](IMPLEMENTATION_LOG.md). Tóm tắt: thêm Device
Capability Model, trạng thái có nguồn gốc, Policy Engine, Execution Engine có xác minh và xác nhận phía server, audit log,
simulator, nhà mẫu giả lập và REST API theo nhà. 79 test đạt.
