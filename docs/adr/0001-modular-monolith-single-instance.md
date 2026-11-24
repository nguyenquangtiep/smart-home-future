# ADR 0001: Monolith có module, một tiến trình, một instance mỗi database

- **Trạng thái:** ✅ Accepted · **Ngày:** 2026-11-23 · **Ghi lại quyết định đã có trong code**

## Bối cảnh

- API, agent, control, gateway chạy chung một tiến trình FastAPI (`src/smarthome/api/main.py`, `create_app`).
- Trạng thái đang chờ của lệnh (`_pending`, `_verified`) nằm trong bộ nhớ `ControlService` (`control/execution.py`).
- Khi khởi động, `recover()` đánh `interrupted` mọi lệnh dở dang. Nếu hai tiến trình dùng chung DB, tiến trình mới sẽ đóng
  nhầm lệnh đang sống của tiến trình kia (GAP-D, [HARDENING §4](../HARDENING_2026-11.md#4-vòng-2-kiểm-chứng-lại-và-lỗi-còn-sót)).

## Phương án

| Phương án | Ưu | Nhược |
|---|---|---|
| **A. Một tiến trình, một instance mỗi DB** | Đơn giản; trạng thái chờ nằm cạnh xác minh; đủ cho một nhà | Không mở rộng ngang |
| B. Nhiều worker + Redis pub/sub cho pending/sự kiện | Mở rộng ngang | Thêm hạ tầng, recovery phân tán phức tạp |
| C. Microservice (agent, control, gateway riêng) | Cô lập lỗi | Quá nặng cho pilot một nhà; phải nhân đôi policy hoặc gọi mạng |

## Quyết định

Chọn **A**. Một tiến trình Uvicorn một worker. `PostgresCommandStore.acquire_instance_lock()` giữ advisory lock phiên;
instance thứ hai từ chối khởi động (`api/main.py` lifespan).

## Hệ quả

- Không chạy `uvicorn --workers > 1`; Dockerfile hiện chạy 1 worker (`CMD uvicorn ...`).
- Multi-home trong **một** instance được hỗ trợ ở mức dữ liệu (`home_id` mọi nơi), nhưng nhiều instance cho nhiều nhà cần
  DB riêng mỗi instance.
- Agent và LLM phải async, có timeout, để không chặn điều khiển (AI_AGENT_DESIGN §8).

## Khi nào xem lại

Quản lý nhiều hộ trên một backend chung (P2), hoặc đo được độ trễ điều khiển vượt NFR-01 do tải.
