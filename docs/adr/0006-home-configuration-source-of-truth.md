# ADR 0006: Cấu hình nhà lưu trong Postgres; YAML chỉ để nhập/xuất

- **Trạng thái:** ✅ **Accepted** (2026-11-23, chủ dự án duyệt theo khuyến nghị kỹ thuật, D1–D6 ở mục 5) · **Chưa triển khai**
- **Ngày:** 2026-11-23 (rà soát lần 2) · **Chặn:** P0-06b, P0-07 (khóa ngoại `memberships → homes`), P0-10, P0-13 ·
  **LLD:** [P0-06](../lld/P0-06_PERSISTENCE.md)

## 1. Bối cảnh (có dẫn chứng)

- `build_control` luôn nạp nhà mẫu giả lập `build_demo_home()`, kể cả khi có Postgres (`api/main.py`); không có đường nào
  khai báo nhà thật.
- Cấu hình nhà thay đổi lúc chạy: kéo thả vị trí 3D (P0-13), thêm/xóa thiết bị, đổi vai trò thành viên (P0-07).
- `ControlService` khóa thiết bị theo `Device.id` **toàn cục** (`self.devices = {d.id: d}`, `device_in_home`) và nạp **một lần**
  lúc khởi động; `commands_one_in_flight_idx ON (device_id, capability)` không có `home_id` (migration `0001`).
- Lệnh và audit tham chiếu `home_id`, `device_id` dạng text, không khóa ngoại (lịch sử sống lâu hơn thiết bị).

## 2. Phương án

| Phương án | Ưu | Nhược |
|---|---|---|
| Seed trong code (hiện tại) | Đơn giản | Không dùng được cho nhà thật |
| File YAML đọc lúc khởi động | Dễ sửa tay, có Git | Không sửa lúc chạy được (3D EDIT); hai nguồn nếu UI cũng sửa |
| **Postgres là nguồn chuẩn; YAML chỉ nhập/xuất** | Một nguồn; sửa lúc chạy; khóa ngoại và version | Cần migration và công cụ import |

## 3. Quyết định đề xuất

Chi tiết và lý do từng điểm ở [LLD P0-06 §2](../lld/P0-06_PERSISTENCE.md#2-quyết-định-thiết-kế).

| Chủ đề | Quyết định |
|---|---|
| Nguồn chuẩn | Postgres: `homes`, `floors`, `rooms`, `gateway_connections`, `devices`, `device_placements` (migration `0002`) |
| YAML | **Không bao giờ đọc lúc khởi động.** Chỉ `import`/`export` qua CLI (P0-10), cùng `validate_home_config` |
| Hai nguồn mâu thuẫn | Không tồn tại: có DB thì DB là duy nhất; không có DB (chỉ `local`/`test`) thì nhà mẫu trong bộ nhớ như hiện nay |
| Định danh | `devices.id` toàn cục, thiết bị thật dùng ID tự sinh; tầng/phòng khóa theo `(home_id, id)`; gateway là **instance** có ID riêng, `Device.gateway` mang ID đó |
| Toàn vẹn | Khóa ngoại tổng hợp theo `home_id`; placement cùng phòng với thiết bị; unique một phần `(gateway_id, external_ref)`; `RESTRICT` khi xóa |
| Thêm/xóa thiết bị | Thêm bằng `replace_home`; xóa **mềm** (`removed_at`), ID không dùng lại; xóa phòng/tầng/gateway còn thiết bị bị chặn |
| Transaction | `replace_home` một transaction, khóa dòng `homes`; `load_all` một transaction `REPEATABLE READ` (ảnh chụp nhất quán) |
| Version | `homes.config_version` tăng 1 mỗi lần ghi, ghi có điều kiện `expected_version`; sai → `ConfigVersionConflict`; `device_placements.version` cho 3D EDIT |
| Ai được ghi | Chỉ khi giữ **instance lock** (app dừng) bằng CLI; ghi lúc chạy chỉ qua `ControlService` (P0-13) |
| Khởi động | Có DB mà chưa có nhà → **từ chối khởi động**; cấu hình sai → từ chối, nêu vi phạm; không rơi về nhà mẫu |
| Recovery | Cấu hình bền sẵn; lệnh dở dang của thiết bị đã xóa mềm đối soát `STATE_UNKNOWN_AFTER_RESTART` (`reconcile_interrupted` xử lý thiết bị không còn) |
| Seed demo | Chỉ `local`/`test`, chỉ khi giữ instance lock |

## 4. Đánh giá

| Tiêu chí | Kết quả |
|---|---|
| Nhất quán giao dịch | ✅ một transaction mỗi lần ghi; ảnh chụp nhất quán khi đọc |
| Đồng thời | ✅ version + khóa dòng; hai bên ghi không xảy ra khi giữ instance lock |
| Toàn vẹn khi thêm/xóa thiết bị | ✅ đã thử trên Postgres 18: 40 phép thử (ghi ở [Design Review §8](../DESIGN_REVIEW_2026-11.md#8-validation)) |
| Không có hai nguồn | ✅ |
| Recovery | ✅ thiết kế; test AC-06-11 |
| Chưa kiểm chứng | Ghi cấu hình lúc chạy (LLD P0-13); thiết bị ánh xạ hai gateway cùng lúc; đổi schema `Capability` với JSON đã lưu |

## 5. Quyết định cần chủ dự án

| # | Câu hỏi | Khuyến nghị | Quyết định (2026-11-23) | Đánh đổi |
| --- | --- | --- | --- | --- |
| D1 | Duyệt ADR này? | **Accept** | ✅ Chấp nhận khuyến nghị | Không duyệt thì P0-06b, P0-07, P0-10, P0-13 đứng |
| D2 | `devices.id` duy nhất toàn cục, thiết bị thật dùng ID tự sinh? | **Có** | ✅ Chấp nhận khuyến nghị | ID không đọc được bằng mắt; muốn ID theo nhà phải sửa `ControlService` và migration `0001` |
| D3 | Thiết bị chỉ xóa mềm? | **Có** | ✅ Chấp nhận khuyến nghị | Bảng giữ dòng đã xóa; phòng có thiết bị đã xóa mềm chưa xóa được tới khi có lệnh purge (P1-08) |
| D4 | Đưa `gateway_connections` vào `0002` (thay vì P0-09) và để `Device.gateway` là ID instance? | **Có** | ✅ Chấp nhận khuyến nghị | Thêm một bảng và một model domain sớm hơn kế hoạch; đổi lại tránh va chạm giữa các nhà |
| D5 | Ghi cấu hình khi app dừng (CLI) ở P0? | **Có** | ✅ Chấp nhận khuyến nghị | Sửa cấu hình cần dừng app; ghi lúc chạy đến cùng P0-13 |
| D6 | Có DB mà chưa có nhà thì từ chối khởi động? | **Có** | ✅ Chấp nhận khuyến nghị | Lần đầu triển khai phải chạy `seed-demo` hoặc `import` trước |

### Phê duyệt

| Người duyệt | Quyết định | Ngày | Ghi chú |
|---|---|---|---|
| Chủ dự án | ☑ **Accepted** theo khuyến nghị: D1–D6 đều chấp nhận | 2026-11-23 | Xác nhận trong phiên làm việc (trả lời "Duyệt cả hai") |

Trạng thái và [mục lục ADR](README.md) đã cập nhật. Chấp nhận ADR **không** có nghĩa đã triển khai: việc này thuộc Sprint 1 (S1-02…S1-04).

## 6. Hệ quả

- `ControlService` giữ nguyên constructor; `build_control` nạp từ `HomeConfigStore`.
- Thêm model domain `GatewayConnection` và hàm thuần `validate_home_config`.
- `commands.device_id` **không** có khóa ngoại tới `devices` (lịch sử sống lâu hơn thiết bị).
- Đổi cấu hình lúc chạy cần cơ chế làm mới bộ nhớ của `ControlService` (P0-13).

## 7. Khi nào xem lại

Nhiều instance cần đồng bộ cấu hình ([ADR 0001](0001-modular-monolith-single-instance.md) thay đổi), hoặc cần ID thiết bị theo
phạm vi nhà.
