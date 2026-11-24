# ADR 0007: Realtime bằng WebSocket theo nhà (snapshot + sự kiện)

- **Trạng thái:** 📝 Proposed · **Ngày:** 2026-11-23 · **Chặn:** P0-08, P0-11, P0-12

## Bối cảnh

Hiện client phải poll `GET /commands/{id}` (`docs/api/control-api.md`). Không có endpoint realtime nào
(`api/routes/*.py`). Dashboard và 3D cần thấy trạng thái thiết bị đổi trong ≤ 500 ms (ROADMAP P0-08).

## Phương án

| Phương án | Ưu | Nhược |
|---|---|---|
| Polling | Không thêm gì | Chậm, tốn tải; trễ hiển thị |
| SSE | Đơn giản, một chiều | Không gửi lệnh qua cùng kênh; giới hạn kết nối HTTP/1.1 |
| **WebSocket theo nhà** | Hai chiều; xác thực bằng cùng cookie (ADR 0005) | Cần xử lý kết nối lại |

## Quyết định (đề xuất)

`GET /api/v1/homes/{home_id}/stream` (WebSocket). Bắt tay: cookie phiên + kiểm `Origin` + membership. Server gửi `snapshot`
(mọi `DeviceView`) rồi sự kiện `device.view`, `command.updated`, `safety.alert`, mỗi sự kiện có `seq` tăng dần. Mất kết nối →
client kết nối lại và nhận snapshot mới. **Lệnh vẫn gửi qua REST** để giữ idempotency và mã lỗi HTTP.

## Hệ quả

- Hub phát sự kiện chạy trong cùng tiến trình (ADR 0001).
- Hợp đồng sự kiện chi tiết viết ở LLD P0-08 (chưa có).

## Khi nào xem lại

Nhiều instance (cần pub/sub ngoài).
