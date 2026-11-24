# ADR 0002: PostgreSQL và migration SQL có phiên bản

- **Trạng thái:** ✅ Accepted · **Ngày:** 2026-11-23 · **Ghi lại quyết định đã có trong code**

## Bối cảnh

Các bất biến an toàn dựa vào DB: một lệnh đang gửi mỗi capability (partial unique index), claim approval có điều kiện,
audit chỉ ghi thêm (trigger), giữ chỗ idempotency nguyên tử (`migrations/0001_commands_audit.up.sql`). Chúng phải được
kiểm trên đúng engine sẽ chạy thật.

## Phương án

| Phương án | Ưu | Nhược |
|---|---|---|
| **A. Postgres mọi nơi + runner SQL tự viết** | 1 dialect; DDL transactional; không thêm thư viện lớn; dễ đọc | Tự bảo trì runner (~100 dòng) |
| B. Postgres + SQLAlchemy/Alembic | Chuẩn ngành, autogenerate | 2 dependency lớn cho vài bảng; ORM không cần thiết |
| C. SQLite dev/test, Postgres prod | Test không cần server | 2 dialect, hành vi khóa/ràng buộc khác: test không đáng tin |

## Quyết định

Chọn **A**: `persistence/migrate.py`, file `NNNN_name.up.sql` + `.down.sql`, mỗi migration một transaction; app từ chối
khởi động nếu schema lệch phiên bản (`SchemaVersionError`). psycopg đồng bộ trong `asyncio.to_thread` để chạy được trên
Windows (Proactor loop).

## Hệ quả

- Mọi thay đổi schema = migration mới, có `down`; migration xóa dữ liệu cần cờ `--confirm-data-loss`.
- CI chạy test Postgres bắt buộc (`SMARTHOME_REQUIRE_POSTGRES=1`).
- Một kết nối có khóa: đủ cho một nhà (ADR 0001), không đủ cho tải lớn.

## Khi nào xem lại

Số bảng/migration tăng tới mức cần autogenerate, hoặc cần connection pool vì tải.
