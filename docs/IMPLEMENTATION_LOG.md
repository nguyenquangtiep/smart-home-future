# Implementation Log

Mỗi đợt ghi: thay đổi, lý do, kiểm chứng, phần chưa xong. Mới nhất ở trên.

## 2026-11-24 · Đợt 10: vòng review PR #2 (R1, R2, R3)

| Mục | Kết quả |
|---|---|
| R1 | `DEPLOYMENT.md` viết lại phần hiện trạng và cấu hình (hết mô tả Compose `docker`, cổng fail-open, "sẽ làm"); tách hiện tại và mục tiêu P0-17; ADR 0005 §1, ADR 0008 đánh dấu phần lịch sử |
| R2 | Smoke test dùng `docker rm -fv`, thư mục tạm cho headers/cookie/CSRF, `cleanup` ở mọi lối thoát; script cũ được đo là bỏ lại 1 volume mỗi lần chạy. `docker_smoke_cleanup_check.sh` ép lỗi ở `db`, `app`, `restart` và xác nhận không sót gì (máy dev); CI kiểm cả hai đường |
| R3 | Trước: 24 đăng nhập khác email/IP → 16 phép Argon2id cùng lúc (~1 GiB), hàng đợi vô hạn. Sau: tối đa 2 cùng lúc (đo lại: đỉnh 2, ~128 MiB), hàng đợi 16, chờ 5 s, `503 AUTH_BUSY`; lần thử bận được trả lại và ghi `login_busy`. Test F2 nới giới hạn hasher để vẫn đo riêng rate limiter |
| Docker máy dev | Docker Desktop chạy lại được: `docker compose config`, `docker build`, smoke test 30/30 đạt |
| Kiểm chứng | pytest Postgres bắt buộc: **444 passed, 0 failed, 0 skipped** (không Postgres: 317 passed, 127 skipped); ruff đạt; eval 7/7; Mermaid render |

## 2026-11-24 · Đợt 9: nghiệm thu bổ sung Sprint 1 (F1, F2, F3)

Chi tiết: [SPRINT1_ACCEPTANCE §Nghiệm thu bổ sung](SPRINT1_ACCEPTANCE.md#nghiệm-thu-bổ-sung-2026-11-24-f1-f2-f3). Nhánh
`fix/sprint1-acceptance-hardening` từ `main` = `b252133`.

| Mục | Kết quả |
|---|---|
| F1 | `Dockerfile` có `\n` theo nghĩa đen trong `ENV` nhiều dòng (build hỏng). Sửa; test tĩnh `test_dockerfile.py` (3 lỗi trên file cũ, đạt trên file mới); hadolint trong CI |
| F2 | Tái hiện được race: 20 lần đăng nhập sai đồng thời đều được kiểm mật khẩu (giới hạn 5), 40 lần từ một IP đều được kiểm (giới hạn 30). Sửa bằng `reserve_attempt` (một transaction, advisory lock email rồi IP, giữ chỗ trước khi xác minh) và `finish_attempt`. 5 test đồng thời trên PostgreSQL thật: 4 lỗi trước, đạt sau |
| F3 | Docker Desktop trên máy dev crash khi khởi động (không sửa cài đặt của chủ máy). Thêm job `docker` trong CI: [CI run 45352819175](https://github.com/nguyenquangtiep/smart-home-future/actions/runs/45352819175), 30 kiểm tra đạt (build, production fail-closed, migration, đăng nhập, CSRF, restart, healthcheck, Compose dev chỉ `127.0.0.1`) |
| Argon2id | `scripts/bench_argon2.py`: máy dev xác minh p50 84,5 ms, p95 118,2 ms, event loop trễ tối đa 6,6 ms. M-07-1: PENDING TARGET HARDWARE |
| Kiểm chứng | pytest Postgres bắt buộc: **438 passed, 0 failed, 0 skipped** (không Postgres: 316 passed, 122 skipped); ruff đạt; eval 7/7; Mermaid render; CI `checks` và `docker` đều đạt |
| Còn mở | Argon2id trên máy đích; topology production (P0-17); Docker trên máy dev |

## 2026-11-24 · Đợt 8: Sprint 1 (P0-06b, P0-07)

Nghiệm thu từng tiêu chí: [SPRINT1_ACCEPTANCE.md](SPRINT1_ACCEPTANCE.md). Thiết kế: [LLD P0-06](lld/P0-06_PERSISTENCE.md),
[LLD P0-07](lld/P0-07_AUTH_RBAC.md). Nhánh `feat/sprint1-persistence-auth-rbac` từ `main` = `a586984`.

| Mục | Kết quả |
|---|---|
| Task | S1-01 … S1-09 đều có code và test |
| Migration | `0002_home_config` (6 bảng), `0003_auth` (5 bảng); `0001` không đổi (có test băm) |
| Module mới | `domain/config.py`, `control/membership.py`, `bootstrap.py`, `persistence/{config_store,pgconn,seed}.py`, `auth/{passwords,service,store,__main__}.py`, `api/security.py`, `api/routes/{auth,members,simulator}.py` |
| Endpoint mới | `POST /auth/login`, `GET /auth/session`, `POST /auth/logout`, `POST /auth/password`; `GET/POST /homes/{id}/members`, `PATCH/DELETE /homes/{id}/members/{user_id}` |
| CLI mới | `python -m smarthome.persistence seed-demo`; `python -m smarthome.auth create-owner / set-password / unlock / disable-user` |
| Thay đổi hành vi | Mọi route cần đăng nhập (kể cả `/agent/invoke`); `environment` là `Literal`; có DB thì cấu hình nhà từ DB, DB trống thì từ chối khởi động; `ControlService.member` thành async và đọc lại người duyệt; Dockerfile `production`, Compose `local` bind `127.0.0.1` |
| Dependency mới | `argon2-cffi` (đã duyệt ở ADR 0005) |
| Kiểm chứng | pytest với Postgres 18 bắt buộc: **428 passed, 0 failed, 0 skipped** (không có Postgres: 311 passed, 117 skipped); ruff đạt (119 file); eval 7/7; Mermaid 49/49 render bằng mermaid-cli 11.17.1 |
| Bug thật tìm thấy khi viết test | (1) `app.routes` của FastAPI mới chỉ có `_IncludedRouter`, nên test "route nào cũng bị chặn" ban đầu duyệt 0 route và đạt oan; đổi sang `app.openapi()`. (2) `TestClient` không dùng `with` hủy tác vụ nền của simulator khi request kết thúc, làm lệnh kẹt `dispatched`; test dùng chung event loop của lifespan. (3) `real` làm số đọc lại lệch (`2.8` thành `2.7999999523`); đổi `double precision` |
| Chưa kiểm | Argon2id trên máy đích (đo trên máy dev: 88–90 ms); `docker compose up` và build image (Docker daemon không chạy); Home Assistant; thiết bị thật; TLS; hiệu năng dưới tải |

## 2026-11-23 · Đợt 7: chốt thiết kế Sprint 1 (Design Finalization)

Báo cáo và sign-off: [DESIGN_REVIEW_2026-11.md](DESIGN_REVIEW_2026-11.md) (v2). Kết luận khi mở PR #1: **PASS WITH CONDITIONS**; sau khi chủ dự án duyệt ADR (2026-11-23): **PASS** cho thiết kế Sprint 1.

| Mục | Kết quả |
|---|---|
| Baseline | `main` = `0461606`, working tree sạch, nhánh `docs/design-finalization-sprint1` |
| Code, schema | **Không đổi.** Chỉ sửa tài liệu và `tests/unit/test_docs.py` (anchor tính từ chữ hiển thị của link, như GitHub) |
| Tài liệu sửa | `DESIGN_REVIEW_2026-11`, ADR 0005 và 0006, LLD P0-06 và P0-07 (viết lại v2), DATABASE_DESIGN, ARCHITECTURE, TRACEABILITY, ROADMAP, DEPLOYMENT, SECURITY_AND_SAFETY, `adr/README` |
| Lỗi LLD v1 đã sửa | Khóa gateway theo tên adapter; placement lệch phòng; CSRF chỉ lưu hash; `ControlService` dùng thành viên tĩnh; IP sau proxy; Argon2 chặn event loop; ID người dùng uuid lệch text |
| Phát hiện từ code | Thành viên tĩnh trong `confirm()` (DR-H08); `/agent/invoke` không xác thực; `/docs` luôn mở; vòng import `agent`; 2 mô tả lỗi thời trong DATABASE_DESIGN |
| Finding | 38 (8 High, 20 Medium, 10 Low); 15 đóng, 9 chờ code Sprint 1, 11 mở sau Sprint 1, 3 chấp nhận |
| ADR | 0005 và 0006: **Accepted** ngày 2026-11-23, chủ dự án duyệt theo khuyến nghị (D1–D6) sau khi PR #1 được mở; **chưa triển khai** |
| Kiểm chứng | ruff đạt (93 file); pytest với Postgres bắt buộc **217 passed, 0 failed, 0 skipped** (gồm 81 test tài liệu); eval 7/7; Mermaid **48/48** khối render bằng mermaid-cli 11.17.1; DDL `0002` 40/40 và `0003` 39/39 trên Postgres 18 tạm (script ngoài repo). Kiểm cục bộ bắt được 1 lỗi định dạng khối Python trong LLD P0-06 trước khi push (CI chạy `ruff format --check .`) |
| Chưa kiểm | Build Docker, Home Assistant, thiết bị thật, hiệu năng; logic ứng dụng của đăng nhập và `HomeConfigStore` (chưa viết) |

## 2026-11-23 · Đợt 6: Formal Design Review

Báo cáo: [DESIGN_REVIEW_2026-11.md](DESIGN_REVIEW_2026-11.md). Kết luận: **PASS WITH CONDITIONS** cho P0-06b, P0-07.

| Mục | Kết quả |
|---|---|
| Tài liệu mới | `DESIGN_REVIEW_2026-11.md`, `TRACEABILITY.md`, `DEPLOYMENT.md`, `adr/README.md` + 8 ADR, `lld/P0-06_PERSISTENCE.md`, `lld/P0-07_AUTH_RBAC.md` |
| Tài liệu sửa | DATABASE_DESIGN (§2.2 ERD, §3 viết lại; DR-H05), ARCHITECTURE (§3 ranh giới module, liên kết), IOT_INTEGRATION (liên kết ADR 0004), ROADMAP (06a/06b, dependency, thứ tự sprint), SECURITY (DR-H03), PROGRESS, README, TESTING_STRATEGY |
| Findings | 0 Critical · 5 High · 11 Medium · 6 Low (thiết kế) |
| Code, schema | **Không đổi** |
| Kiểm chứng | pytest có Postgres bắt buộc: **217 passed, 0 failed, 0 skipped**; ruff đạt; Mermaid 39/39 render bằng mermaid-cli 11.17.1 |
| Không kiểm được | Build Docker, Home Assistant, thiết bị thật, hiệu năng |

## 2026-11-23 · Đợt 5: kiểm chứng tài liệu và CI

| Yêu cầu | Kết quả |
|---|---|
| CI có Postgres? | **Đã có** từ `27d13f0`. Log CI của `72ee113`: 188 passed, 0 skipped, tức 13 test Postgres đã chạy thật |
| Lỗ hổng tìm thấy | CI chỉ chạy khi push `main` (nhánh làm việc không được kiểm); Postgres hỏng thì test **bỏ qua im lặng** |
| Đã sửa | Push mọi nhánh kích hoạt CI; `SMARTHOME_REQUIRE_POSTGRES=1` trong CI biến bỏ qua thành lỗi (`tests/conftest.py`); `pytest -rs` in lý do bỏ qua |
| Mermaid | `scripts/render_mermaid.py` + mermaid-cli 11.17.1 trong CI, artifact `mermaid-svg`. Trên máy: 25/25 render; khối hỏng thử nghiệm bị bắt (`Parse error`, exit 1) |
| Link, SVG | `test_docs.py`: thêm kiểm tra anchor (110 link nội bộ, 12 có anchor; anchor sai thử nghiệm bị bắt), SVG là XML hợp lệ và có `viewBox`. 0 link ngoài |
| Responsive | 27 ảnh (2 wireframe + 25 sơ đồ render) trong khung 375 px: 0 hỏng, 0 tràn |
| Số test thật (máy, có Postgres, bắt buộc) | **189 passed, 0 failed, 0 skipped** (137 unit, 44 integration, 8 eval) |
| Tài liệu đồng bộ | TESTING_STRATEGY (tóm tắt, bảng, biểu đồ, lệnh), PROGRESS, VISUAL_DESIGN_STANDARD §6.3, README (CI) |
| Không làm | Không đổi code chức năng; không frontend, không 3D |

## 2026-11-23 · Đợt 4: tiêu chuẩn Visual-first cho tài liệu và giao diện

Tiêu chuẩn, audit và kế hoạch: [VISUAL_DESIGN_STANDARD.md](VISUAL_DESIGN_STANDARD.md). Báo cáo tiến độ: [PROGRESS.md](PROGRESS.md).

| Mục | Kết quả |
|---|---|
| Tài liệu mới | `VISUAL_DESIGN_STANDARD.md`, `PROGRESS.md`, wireframe `docs/assets/ui/v2/home-3d-{desktop,mobile}.svg` |
| Sơ đồ thêm | 16 khối Mermaid mới trong README, ARCHITECTURE, HARDENING, DATABASE_DESIGN, SECURITY_AND_SAFETY, TESTING_STRATEGY, IOT_INTEGRATION, ROADMAP, AUTOMATION_ENGINE, PRD, VISUAL_DESIGN_STANDARD, PROGRESS; tổng 25 khối |
| Tóm tắt đầu trang | 12 tài liệu chính |
| Lỗi có sẵn được sửa | Sequence diagram trong ARCHITECTURE §4 không render được (dấu `;` trong `Note`); README còn ghi "tính năng chưa triển khai" |
| Kiểm chứng | 25/25 khối Mermaid render bằng Mermaid 11.4.1 trong trình duyệt; 2 wireframe xem bằng trình duyệt; test mới `tests/unit/test_docs.py` (52 ca: link/ảnh tồn tại, Mermaid hợp lệ và không có `;`, SVG có `title`/`desc`/`lang`) |
| Số liệu trong biểu đồ | Chỉ từ `pytest --collect-only`, `gh run list`, bảng ROADMAP/PRD; không có số ước lượng |
| Giao diện | **Chưa có code UI.** Wireframe và đặc tả component chờ P0-11; không làm trước P0-07, P0-08 |

## 2026-11-23 · Đợt 3: kiểm chứng lại hardening (vòng 2)

Chi tiết lỗi và cách sửa: [HARDENING_2026-11.md §4](HARDENING_2026-11.md#4-vòng-2-kiểm-chứng-lại-và-lỗi-còn-sót).

| Mục | Kết quả |
|---|---|
| Baseline `27d13f0` | 127 passed (có Postgres), ruff sạch, eval 7/7; 5 test tái hiện ISSUE-01/02/03 vòng 1 đều đạt |
| Lỗi mới xác minh | GAP-A (Critical), GAP-B, GAP-C, GAP-D (High), GAP-E (Low): 6 test FAIL trước khi sửa |
| Sau sửa | **136 passed** (có Postgres), chạy 5 lần đều đạt; **123 passed, 13 skipped** (không có Postgres); ruff sạch; eval 7/7 |
| Schema | Không đổi |

File sửa: `control/execution.py` (ack timeout, ghi best effort sau side effect, `StoreUnavailableError`, audit không chặn),
`persistence/base.py` (`InstanceLockError`, `acquire_instance_lock`), `persistence/postgres.py` (tự kết nối lại, advisory lock),
`persistence/memory.py` (bộ đếm `seq`), `api/main.py` (lấy instance lock khi khởi động), `api/routes/homes.py` (503).
Test mới: `tests/unit/test_hardening_round2.py`; thêm vào `test_persistence.py`, `test_homes_api.py`.

## 2026-11-23 · Đợt 2: hardening đường điều khiển (ISSUE-01 … ISSUE-04)

Phân tích, thiết kế, phương án đã cân nhắc và rollback: [HARDENING_2026-11.md](HARDENING_2026-11.md).

### Kết quả theo issue

| Issue | Trạng thái | Bằng chứng |
|---|---|---|
| ISSUE-02 Safety policy | **FIXED** | 2 test tái hiện FAIL trên baseline → PASS; thêm test fail-closed, approval chéo nhà, replay, đồng thời, cấu hình làm yếu |
| ISSUE-01 Verification | **FIXED** | 2 test tái hiện FAIL → PASS; thêm test correlation lạ, báo cáo trùng, không khớp, lệch đồng hồ, correlation đến sớm, connectivity sai thứ tự |
| ISSUE-03 Idempotency | **FIXED** | 1 test tái hiện FAIL → PASS; thêm test replay, xung đột theo giá trị/nguồn/kiểu, phạm vi, đồng thời, TTL, lệnh nguy hiểm, API 409 |
| ISSUE-04 Persistence | **PARTIALLY FIXED** | Lệnh, idempotency, audit lưu bền trên Postgres; restart/recovery/migration có test trên Postgres 18 thật. Chưa lưu cấu hình nhà/thiết bị/thành viên (P0-07/P0-10); chưa tự kết nối lại DB |

### File mới

`src/smarthome/control/commands.py` (record, trạng thái, fingerprint), `src/smarthome/persistence/{base,memory,postgres,migrate,__main__}.py`,
`src/smarthome/persistence/migrations/0001_commands_audit.{up,down}.sql`, `tests/conftest.py`, `tests/unit/test_issue_repro.py`,
`tests/unit/test_hardening.py`, `tests/integration/test_persistence.py`, `docs/HARDENING_2026-11.md`.

### File sửa

| File | Thay đổi |
|---|---|
| `domain/capabilities.py` | `safe_values`; `requires_confirmation` fail-closed theo `SafetyClass`; khóa cửa khai báo `safe_values=("locked",)` |
| `domain/state.py` | `apply()` trả capability được chấp nhận; connectivity cũng chống sai thứ tự; `Observation.correlation_id`; `PendingCommand.dispatched_at/correlation_ids` |
| `gateway/base.py`, `gateway/simulator.py` | `send(..., correlation_id) -> str | None`; `StateReport.correlation_id`; simulator có chế độ `correlate` |
| `control/execution.py` | Dùng `CommandStore`; chính sách xác minh; ghi `dispatching` trước khi gửi; claim approval; `submit_command` trả `replayed`; `recover()`, `reconcile_interrupted()` |
| `control/audit.py` | Chỉ còn model; lưu trữ chuyển vào store |
| `control/policy.py` | Thông điệp xác nhận ghi lớp an toàn |
| `api/main.py`, `api/routes/homes.py` | Mở store, recovery khi khởi động, 409 idempotency, header replay, endpoint async |
| `core/config.py` | `database_url`, TTL, clock skew, retention; chuỗi rỗng = chưa cấu hình |
| `demo_home.py` | Bình nóng lạnh dùng `safe_values=(False,)` |
| `pyproject.toml` | Thêm `psycopg[binary]>=3.2` (dependency duy nhất thêm vào) |
| `.github/workflows/ci.yml` | Service `postgres:16`, `SMARTHOME_TEST_DATABASE_URL` |
| `.env.example`, README, ARCHITECTURE, SECURITY_AND_SAFETY, DATABASE_DESIGN, IOT_INTEGRATION, TESTING_STRATEGY, ROADMAP, PRD, `api/control-api.md` | Cập nhật theo thiết kế thực tế |
| `tests/unit/test_execution.py` | Đọc audit qua `await control.list_audit()` (API đổi sang async); không đổi kỳ vọng |

### Kiểm chứng

| Lệnh | Baseline (`d9b67ff`) | Sau sửa |
|---|---|---|
| `python -m pytest -q` (có Postgres 18 cục bộ) | 79 passed | **127 passed**, 0 failed, 0 skipped; chạy 5 lần liên tiếp đều đạt |
| `python -m pytest -q` (không có Postgres) | 79 passed | **115 passed, 11 skipped** (test Postgres) |
| `ruff check .` / `ruff format --check .` | đạt | đạt |
| `python eval/scripts/run_eval.py` | 7/7 | 7/7 |
| Type check | chưa cấu hình | chưa cấu hình |
| CLI migration (`status`, `upgrade`, `downgrade` có/không cờ, `purge`) | — | chạy tay trên DB tạm, đúng như mong đợi |

Postgres dùng để test: cluster tạm khởi tạo trong thư mục scratchpad, cổng 55432, không đụng cài đặt PostgreSQL có sẵn. CI
GitHub chưa chạy (chưa push). Không thử với thiết bị thật.

### Lỗi phát hiện thêm trong lúc làm

- `SMARTHOME_DATABASE_URL=` (rỗng, như trong `.env.example`) trở thành `SecretStr("")` và sẽ khiến app kết nối bằng DSN rỗng
  (mặc định libpq). Đã sửa: chuỗi rỗng = chưa cấu hình; có test.

## 2026-11-23 · Đợt 1: nền đường điều khiển (P0-01 … P0-05)

### Bối cảnh

Audit ([AUDIT_REPORT.md](AUDIT_REPORT.md)) cho thấy repo chỉ có scaffold. Mọi tính năng (3D, agent, automation, HA) đều cần
một mô hình thiết bị và một đường điều khiển có xác minh, nên làm phần này trước.

### File mới

| File | Nội dung |
|---|---|
| `src/smarthome/domain/capabilities.py` | `Capability` (kiểu, khoảng, bước, lựa chọn, `min_role`, `safety`, `confirm_values`, `max_age_s`), `validate_value`, `matches`, factory |
| `src/smarthome/domain/models.py` | `Home`, `Floor`, `Room(footprint)`, `Member`, `Device`, `Placement`, `DeviceType` |
| `src/smarthome/domain/state.py` | `Observation`, `Connectivity`, `DisplayStatus`, `StateStore`, `DeviceView`, `CapabilityView` |
| `src/smarthome/gateway/base.py` | Hợp đồng `DeviceGateway`, `StateReport`, `GatewayError` |
| `src/smarthome/gateway/simulator.py` | `SimulatorGateway`, hành vi `normal/silent/error/offline`, báo cáo `source=simulated` |
| `src/smarthome/control/policy.py` | `Actor`, `Origin`, `CommandRequest`, `evaluate`, `may_confirm` |
| `src/smarthome/control/execution.py` | `ControlService`, `CommandRecord`, `CommandStatus`; xác minh, timeout, idempotency, `DEVICE_BUSY`, xác nhận có hạn |
| `src/smarthome/control/audit.py` | `AuditLog` chỉ ghi thêm (bộ nhớ) |
| `src/smarthome/demo_home.py` | Nhà mẫu giả lập: 1 tầng, 2 phòng, 10 thiết bị, 3 người dùng |
| `src/smarthome/api/identity.py` | Dev identity qua `X-Dev-User`, mặc định tắt |
| `src/smarthome/api/routes/homes.py` | Control API v1 |
| `tests/unit/test_capabilities_and_state.py`, `test_policy.py`, `test_execution.py` | 40 test |
| `tests/integration/test_homes_api.py` | 11 test |
| `docs/*.md` (13 file theo brief), `docs/api/control-api.md` | Tài liệu |

### File sửa

| File | Thay đổi | Tương thích |
|---|---|---|
| `src/smarthome/api/main.py` | `create_app(settings=None)`; lifespan khởi tạo `ControlService` với simulator và nhà mẫu; gắn router `homes` | `app = create_app()` giữ nguyên; endpoint cũ không đổi; test cũ đạt |
| `src/smarthome/core/config.py` | Thêm `device_gateway`, `sim_latency_s`, `verify_timeout_s`, `dispatch_timeout_s`, `confirmation_ttl_s`, `dev_identity_enabled` | Có giá trị mặc định; không biến nào bắt buộc |
| `.env.example`, `README.md`, `AGENTS.md`, `docs/project-overview.md` | Biến môi trường mới, cấu trúc, liên kết tài liệu, ghi chú phạm vi mới | Chỉ thêm nội dung; giữ nguyên các thay đổi chưa commit của chủ dự án |

Không thêm dependency. Không xóa file. Không commit.

### Kiểm chứng

| Lệnh | Kết quả |
|---|---|
| `python -m pytest -q` | **79 passed** (28 cũ + 51 mới), chạy 5 lần liên tiếp đều đạt |
| `ruff check .` | All checks passed |
| `ruff format --check .` | đạt (không file nào cần format) |
| `python eval/scripts/run_eval.py` | 7/7 |

Chưa chạy: build Docker, CI trên GitHub (chưa push), đo độ trễ thực (chưa có thiết bị thật).

### Chưa xong, rủi ro

- Lệnh, trạng thái, audit nằm trong bộ nhớ, mất khi khởi động lại (P0-06).
- Chưa có auth thật; dev identity chỉ dùng ở máy dev (P0-07).
- Chưa có realtime; client phải poll `GET /commands/{id}` (P0-08).
- Chưa có adapter Home Assistant; chưa thiết bị thật nào được điều khiển (P0-09).
- Agent vẫn là mẫu, chưa nối với `ControlService` (P0-15).
