# Control API v1

**Trạng thái:** Đã có (`src/smarthome/api/routes/`: `homes.py`, `auth.py`, `members.py`, `simulator.py`), test ở
`tests/integration/test_homes_api.py` và `test_auth_api.py`. Schema đầy đủ ở `/docs` khi chạy server ở `local`/`test` (tắt ở
`staging`/`production`).

## Danh tính

**Mọi route cần đăng nhập**, trừ `GET /health`, `POST /api/v1/auth/login`, `POST /api/v1/auth/logout` (default-deny). Có hai nguồn
danh tính:

- **Phiên đăng nhập** (cần PostgreSQL): cookie `smarthome_session` (`HttpOnly; Secure; SameSite=Strict`). Request ghi
  (`POST`, `PUT`, `PATCH`, `DELETE`) cần header `Origin` thuộc `SMARTHOME_ALLOWED_ORIGINS` và `X-CSRF-Token` khớp token trả ở login
  hoặc `GET /auth/session`. Thiếu hoặc sai → `403 ORIGIN_FORBIDDEN` / `CSRF_FAILED`.
- **Danh tính dev** (chỉ `local`/`test`, `SMARTHOME_DEV_IDENTITY_ENABLED=true`): header `X-Dev-User` (`user-owner`, `user-member`,
  `user-guest`). Server từ chối khởi động nếu bật cờ này ở `staging`/`production`. Không có cờ thì header bị bỏ qua.

Không có danh tính nào → `401 UNAUTHENTICATED` (hoặc `AUTH_NOT_CONFIGURED` khi chưa có DB và chưa bật danh tính dev). Quyền vào nhà
tính **mỗi request** từ bảng `memberships`: gỡ hoặc hạ quyền có hiệu lực ngay.

## Xác thực

| Phương thức | Đường dẫn | Mô tả |
|---|---|---|
| POST | `/api/v1/auth/login` | `{email, password}` → `{user, homes, csrf_token}` + cookie. Sai thông tin: `401 INVALID_CREDENTIALS` (cùng thân cho mọi nguyên nhân); quá giới hạn: `429 TOO_MANY_ATTEMPTS` + `Retry-After` |
| GET | `/api/v1/auth/session` | Phiên hiện tại, cùng thân với login |
| POST | `/api/v1/auth/logout` | `204`, thu hồi phiên; không có phiên cũng `204` |
| POST | `/api/v1/auth/password` | `{current_password, new_password}` → `204`, thu hồi mọi phiên khác; `401`, `422 WEAK_PASSWORD`, `429` |

Mật khẩu 12–128 ký tự, không nằm trong danh sách phổ biến, không trùng email. Tối đa 5 lần sai mỗi email và 30 mỗi IP trong 15
phút (cửa sổ trượt, tính cả email không tồn tại).

## Thành viên (chỉ chủ nhà)

Tiền tố `/api/v1/homes/{home_id}/members`; cần PostgreSQL (không có thì `501 AUTH_NOT_CONFIGURED`).

| Phương thức | Đường dẫn | Mô tả |
|---|---|---|
| GET | `` | `[{user_id, email, display_name, role, created_at}]` |
| POST | `` | `{email, display_name, role, password}` → `201`; `409 EMAIL_IN_USE`, `422` |
| PATCH | `/{user_id}` | `{role}` → `200`; `409 LAST_OWNER`, `404` |
| DELETE | `/{user_id}` | `204`; `409 LAST_OWNER`, `404` |

Nhà luôn còn ít nhất một chủ nhà, kể cả khi hai chủ nhà cùng tự hạ quyền (khóa dòng `homes` trong một transaction). Mỗi thay đổi ghi
`auth_events` và `audit_events` của nhà.

Nhà mẫu giả lập `home-demo` có ba người dùng: `user-owner` (chủ nhà), `user-member`, `user-guest` (không có mật khẩu: chỉ dùng
qua danh tính dev).

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
| PUT | `/simulator/devices/{device_id}/behavior` | Chỉ `local`/`test`, chỉ simulator, chỉ chủ nhà: `normal`, `silent`, `error`, `offline`. Không tồn tại ở `staging`/`production` |

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
