# ADR 0003: Thành công chỉ khi có bằng chứng từ thiết bị

- **Trạng thái:** ✅ Accepted · **Ngày:** 2026-11-23 · **Ghi lại quyết định đã có trong code**

## Bối cảnh

Nguyên tắc sản phẩm: không báo thành công khi thiết bị chưa xác nhận (PRD NFR-03). Hai lỗi thật đã xảy ra: báo cáo cũ xác
minh nhầm lệnh mới (ISSUE-01) và ack trễ ghi đè `succeeded` thành `failed` (GAP-A).

## Phương án

| Phương án | Vì sao không đủ / chọn |
|---|---|
| Gateway nhận lệnh = thành công | Không phải bằng chứng; sai với thiết bị mất kết nối |
| Báo cáo khớp giá trị = thành công | Báo cáo cũ hoặc sai thứ tự gây xác minh nhầm (đã tái hiện) |
| **Correlation nếu có; nếu không, báo cáo phải được state store chấp nhận và không cũ hơn lúc gửi** | Chọn: mạnh nhất có thể với từng giao thức, gắn nhãn độ tin cậy |

## Quyết định

Chính sách trong `ControlService._verification` (Hình 1 trong [HARDENING](../HARDENING_2026-11.md#21-chính-sách-xác-minh-issue-01)):
`verification = correlated | state_match`. Không có bằng chứng trong thời gian chờ → `timed_out` (chưa xác minh). Ack trễ
không phải thất bại. `failed` chỉ khi gateway từ chối.

## Hệ quả

- Adapter mới phải cung cấp `StateReport.observed_at` đúng và, nếu được, `correlation_id` (HA: `context.id`, ADR 0004).
- `state_match` phụ thuộc đồng hồ gateway lệch ≤ 1 s → triển khai cần NTP (ADR 0008).
- UI phải hiển thị "Chưa xác minh", không phải "Thất bại" (VISUAL_DESIGN_STANDARD §3.3).

## Khi nào xem lại

Khi một giao thức cung cấp xác nhận đồng bộ đáng tin (ví dụ Matter có phản hồi thuộc tính) cho phép rút ngắn chờ.
