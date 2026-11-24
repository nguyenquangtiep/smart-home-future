# Product Vision: AI Smart Home Digital Twin Platform

> **Tóm tắt.** Nền tảng web quản lý một ngôi nhà thật: Digital Twin 3D, AI tiếng Việt, điều khiển đa hãng qua Home Assistant.
> Khác biệt: trung thực về trạng thái, AI có trách nhiệm, chạy được khi mất Internet.

Tên tạm thời. Cập nhật 2026-11-23. Tài liệu này **thay thế phạm vi** trong `project-overview.md` và `product/scope.md`
ở các điểm khác nhau (xem §5); ví dụ xuyên suốt và nguyên tắc "chỉ lưu sở thích khi người dùng đồng ý" vẫn giữ.

## 1. Sản phẩm là gì

Nền tảng web để quản lý, điều khiển và tự động hóa một ngôi nhà thông minh thật, gồm:

- **Digital Twin 3D**: thấy cả căn nhà, từng tầng, từng phòng và trạng thái thực của từng thiết bị; bấm để điều khiển.
- **AI Agent tiếng Việt**: hiểu yêu cầu theo mục đích ("tối nay có khách, chuẩn bị phòng khách"), lập kế hoạch, giải thích,
  chỉ thực thi qua lớp kiểm soát an toàn.
- **Điều khiển đa hãng** qua Home Assistant, với một mô hình capability chung.
- **Tự động hóa có kiểm soát xung đột** và tối ưu tiện nghi, năng lượng, an toàn.

Không phải chatbot đơn thuần, không phải dashboard dữ liệu giả.

## 2. Khách hàng

| Giai đoạn | Khách hàng | Nhu cầu chính |
|---|---|---|
| Pilot (P0) | Chủ một ngôi nhà có sẵn vài thiết bị thông minh và Home Assistant | Một chỗ điều khiển tin cậy, trực quan, nói tiếng Việt |
| Mở rộng (P1) | Gia đình nhiều thành viên | Phân quyền, sở thích riêng, tự động hóa không làm phiền |
| Thương mại (P2) | Đơn vị lắp đặt smart home, dự án căn hộ, quản lý tòa nhà | Triển khai nhanh, quản lý nhiều nhà, hỗ trợ từ xa |

## 3. Giá trị khác biệt

1. **Trung thực về trạng thái.** Luôn phân biệt đã xác nhận, đang chờ, giả lập, cũ, mất kết nối. Không bao giờ báo
   thành công khi thiết bị chưa xác nhận.
2. **Thấy được ngôi nhà.** Mô hình 3D tương tác thay cho danh sách thiết bị phẳng.
3. **AI có trách nhiệm.** AI đề xuất và giải thích; quyết định cho phép thuộc về policy xác định; hành động nhạy cảm cần
   người xác nhận phía server.
4. **Local-first.** Nhà vẫn điều khiển được khi mất Internet hoặc LLM không khả dụng.

## 4. Thứ tự ưu tiên khi đánh đổi

1. Đúng đắn và an toàn · 2. Điều khiển được thiết bị thật · 3. Ổn định, phục hồi lỗi · 4. Kiến trúc dễ mở rộng ·
5. Trải nghiệm 3D · 6. AI đáng tin · 7. Tự động hóa thông minh · 8. Khả năng thương mại.

## 5. Thay đổi so với phạm vi trước (cần chủ dự án xác nhận)

| Mục | Phạm vi trước (`product/scope.md`) | Hướng mới |
|---|---|---|
| Hình nhà | Sơ đồ 2D | Digital Twin 3D (P0 cơ bản, P1 trình chỉnh sửa) |
| Người dùng | Một người | Chủ nhà, thành viên, khách theo nhà (RBAC) |
| Thiết bị | Mô phỏng ở bản đầu tiên, Home Assistant sau | Simulator cho phát triển; thiết bị thật qua HA ngay trong P0 |
| Số thiết bị | 3 thiết bị tương tác | Pilot 8–12 thiết bị, hai phòng |
| Hành vi Trợ lý | Kế hoạch cần bấm Chạy, dừng khi một bước chưa xác minh | **Giữ nguyên** |

## 6. Chỉ số thành công của pilot

- 0 lần báo "đã xong" khi thiết bị chưa xác nhận (kiểm bằng audit).
- p95 thời gian từ bấm đến xác nhận < 2 s cho đường điều khiển cục bộ (đo ở P0-16).
- Mất Internet: 100% thiết bị cục bộ vẫn điều khiển được trong LAN.
- Người dùng dùng mô hình 3D hoặc agent cho ít nhất một nửa số thao tác sau một tuần (đo bằng `origin` trong audit).
