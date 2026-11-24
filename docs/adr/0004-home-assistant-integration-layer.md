# ADR 0004: Home Assistant WebSocket là lớp tích hợp IoT

- **Trạng thái:** 📐 Accepted (thiết kế, **chưa có code**) · **Ngày:** 2026-11-23 · **Chặn:** P0-09

## Bối cảnh

Cần điều khiển thiết bị nhiều hãng (Zigbee, Matter, Wi-Fi) mà không tự viết lại giao thức. Hợp đồng gateway đã có
(`gateway/base.py`: `send(..., correlation_id) -> str | None`, `StateReport`). Chỉ có simulator; `Settings.device_gateway`
chỉ nhận `"simulator"` (`core/config.py`).

**Mâu thuẫn tài liệu cần chốt:** IOT_INTEGRATION §6 lưu HA token ở biến môi trường `SMARTHOME_HA_TOKEN`, còn DATABASE_DESIGN §3
có bảng `gateway_connections.secret_ref`.

## Phương án

| Phương án | Ưu | Nhược |
|---|---|---|
| **A. HA WebSocket API** | Realtime `state_changed` + `call_service` trên một kết nối; có `context.id` làm correlation | Phụ thuộc HA của chủ nhà |
| B. HA REST | Đơn giản | Phải poll; không có luồng sự kiện |
| C. MQTT/Zigbee2MQTT trực tiếp | Không cần HA | Tự lo ánh xạ từng thiết bị |
| D. Cloud API từng hãng | — | Ngược local-first |

## Quyết định

**A.** Kết nối LAN `ws://<ha>:8123/api/websocket`. Correlation = `context.id` trả về từ `call_service`. `unavailable` →
offline; `assumed_state: true` không được tính là xác minh.

**Secret (chốt mâu thuẫn):** P0 một nhà → token trong biến môi trường `SMARTHOME_HA_TOKEN` (`SecretStr`, không lưu DB, không log).
Bảng `gateway_connections` chỉ chứa `url`, `kind` và **tên** biến/secret (`secret_ref`), không chứa token. Multi-home dùng
secret store (P2).

## Hệ quả

- `device_gateway` mở rộng thành `Literal["simulator", "home_assistant"]` trong P0-09.
- Cần HA dev container cho integration test; kiểm thử thiết bị thật chỉ với thiết bị an toàn.
- Mất HA → mọi thiết bị của adapter offline, lệnh đang chờ `timed_out`.

## Khi nào xem lại

Chủ nhà không dùng HA, hoặc cần thiết bị HA không hỗ trợ.
