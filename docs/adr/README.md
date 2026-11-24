# Architecture Decision Records

> **Tóm tắt.** 8 quyết định kiến trúc trọng yếu. 3 **Accepted** đã có code chứng minh (0001–0003); 3 **Accepted** nhưng
> **chưa triển khai**: 0004 (thiết kế), 0005 và 0006 (chủ dự án duyệt 2026-11-23); 2 **Proposed** (0007, 0008) không chặn Sprint 1.
> Lập trong [Design Review 2026-11](../DESIGN_REVIEW_2026-11.md).

| ADR | Quyết định | Trạng thái | Bằng chứng / chặn task |
|---|---|---|---|
| [0001](0001-modular-monolith-single-instance.md) | Monolith có module, một tiến trình, một instance mỗi DB | ✅ Accepted | `persistence/postgres.py` (advisory lock), `api/main.py` |
| [0002](0002-postgresql-versioned-sql-migrations.md) | PostgreSQL + migration SQL có phiên bản, không Alembic | ✅ Accepted | `persistence/migrate.py`, `migrations/0001_*` |
| [0003](0003-evidence-based-command-verification.md) | Thành công chỉ khi có bằng chứng từ thiết bị | ✅ Accepted | `control/execution.py` (`_verification`) |
| [0004](0004-home-assistant-integration-layer.md) | Home Assistant WebSocket là lớp tích hợp IoT | 📐 Accepted (thiết kế) | Chặn P0-09 |
| [0005](0005-session-auth-csrf.md) | Đăng nhập: session phía server, cookie HttpOnly, CSRF token, Argon2id | ✅ Accepted (2026-11-23, chưa triển khai) | P0-07 (S1-01, S1-05…S1-09) |
| [0006](0006-home-configuration-source-of-truth.md) | Cấu hình nhà lưu trong Postgres; YAML chỉ để nhập/xuất | ✅ Accepted (2026-11-23, chưa triển khai) | P0-06b, P0-07, P0-10 (S1-02…S1-04) |
| [0007](0007-realtime-websocket-per-home.md) | Realtime: WebSocket theo nhà, snapshot + sự kiện | 📝 Proposed | Chặn P0-08 |
| [0008](0008-local-deployment-topology.md) | Triển khai: một máy trong LAN, Compose, reverse proxy TLS, NTP | 📝 Proposed | Chặn P0-17 |

Mẫu: Bối cảnh (có dẫn chứng) → Phương án → Quyết định → Hệ quả → Khi nào xem lại. Đổi quyết định đã Accepted bằng ADR
mới thay thế, không sửa ADR cũ.
