# Control API v1

**Trạng thái:** Đã có (`src/smarthome/api/routes/homes.py`), test ở `tests/integration/test_homes_api.py`. Schema đầy đủ ở
`/docs` khi chạy server.

## Danh tính

Chưa có xác thực thật (P0-07). Tạm thời, khi `SMARTHOME_DEV_IDENTITY_ENABLED=true`, server đọc header `X-Dev-User` và tra thành
viên của nhà trong URL. Mặc định cờ này tắt và mọi endpoint dưới đây trả `401 AUTH_NOT_CONFIGURED`. Server từ chối khởi
động nếu bật cờ khi `SMARTHOME_ENVIRONMENT=production`.

Nhà mẫu giả lập `home-demo` có ba người dùng: `user-owner` (chủ nhà), `user-member`, `user-guest`.

## Endpoint

Tất cả có tiền tố `/api/v1/homes/{home_id}`. Người không phải thành viên nhận `404`.

| Phương thức | Đường dẫn | Mô tả |
|---|---|---|
| GET | `` | Cấu trúc nhà (tầng, phòng, `simulated`) và `DeviceView` của mọi thiết bị |
| GET | `/devices` | Danh sách `DeviceView` |
| GET | `/devices/{device_id}` | Một `DeviceView` |
| POST | `/commands` | Gửi lệnh. Header tùy chọn `Idempotency-Key` (≤ 128 ký tự, hiệu lực 24 giờ) |
| GET | `/commands/{command_id}` | Trạng thái lệnh |
| POST | `/commands/{command_id}/confirmation` | Chủ nhà duyệt `{"approve": true}` hoặc từ chối |
| GET | `/audit` | Nhật ký (chỉ chủ nhà) |
| PUT | `/simulator/devices/{device_id}/behavior` | Chỉ với simulator, chỉ chủ nhà: `normal`, `silent`, `error`, `offline` |

## Gửi lệnh

```json
POST /api/v1/homes/home-demo/commands
{"device_id": "dev-living-light", "capability": "on_off", "value": true}
```

| HTTP | Khi nào | `status` trong body |
|---|---|---|
| 202 | Đã nhận; đang gửi, đã gửi, đã xác minh, hoặc chờ xác nhận | `dispatched`, `succeeded`, `awaiting_confirmation`, `failed` |
| 403 | Vai trò không đủ, automation làm thao tác cần xác nhận | `rejected` |
| 404 | Thiết bị không có hoặc thuộc nhà khác | — |
| 409 | Thiết bị offline, đang có lệnh khác chờ xác minh | `rejected` |
| 422 | Capability không hỗ trợ, chỉ đọc, giá trị sai | `rejected` |

**Idempotency:** cùng khóa + cùng yêu cầu (thiết bị, capability, giá trị, kiểu giá trị, nguồn) → trả lại lệnh cũ với
header `Idempotent-Replayed: true`. Cùng khóa + yêu cầu khác → `409 IDEMPOTENCY_KEY_REUSED`.
Không ghi được lệnh vào DB trước khi gửi → `503 STORE_UNAVAILABLE`, **chưa có gì được gửi** tới thiết bị.
Gateway không xác nhận kịp: lệnh **không** bị báo `failed`; hệ thống tiếp tục chờ bằng chứng, kết quả là `succeeded`
hoặc `timed_out` với lý do `GATEWAY_ACK_TIMEOUT`. `failed` chỉ khi gateway từ chối rõ ràng. Phạm vi khóa: người dùng
trong một nhà.

Trường mới (2026-11): `verification` (`correlated` hoặc `state_match`, chỉ có khi `succeeded`), `correlation_id`.
Trạng thái mới: `interrupted` (máy chủ khởi động lại khi lệnh đang xử lý; không gửi lại; `reasons` ghi kết quả đối soát).

`202` **không** có nghĩa thiết bị đã đổi. Client phải đợi `status` về một trạng thái cuối: `succeeded`, `timed_out`, `interrupted`
(chưa xác minh), `failed`, `rejected`, `cancelled`, `expired`.

## `DeviceView.capabilities[]`

| Trường | Ý nghĩa |
|---|---|
| `status` | `confirmed` (thiết bị thật báo, còn mới), `simulated`, `pending`, `stale`, `unknown`, `offline` |
| `value`, `observed_at`, `source` | Giá trị **đã báo** gần nhất, thời điểm và nguồn (`reported`/`simulated`) |
| `pending_value`, `pending_command_id` | Lệnh đang chờ xác minh, nếu có |

## Ví dụ PowerShell

```powershell
$env:SMARTHOME_DEV_IDENTITY_ENABLED = "true"; uvicorn smarthome.api.main:app --port 8000
```

```powershell
Invoke-RestMethod -Method Post -Uri http://127.0.0.1:8000/api/v1/homes/home-demo/commands -Headers @{"X-Dev-User"="user-member"} -ContentType "application/json" -Body '{"device_id":"dev-living-light","capability":"on_off","value":true}'
```
