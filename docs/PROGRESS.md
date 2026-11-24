# Báo cáo tiến độ

> **Tóm tắt (2026-11-24).** Sprint 1 xong: cấu hình nhà lưu trong PostgreSQL, đăng nhập thật (phiên, CSRF, Argon2id), RBAC động
> theo nhà, 438 test đạt với Postgres bắt buộc. **Chưa** có web UI, 3D, Home Assistant, thiết bị thật; Argon2id chưa đo trên
> máy đích. Docker build và runtime đã kiểm trong CI. Việc tiếp theo: **Sprint 2 (P0-08 WebSocket, P0-09 Home Assistant)**.

## 1. Executive Summary

| Câu hỏi | Trả lời |
|---|---|
| ✅ **Đã xong** | P0-01 … P0-07 (mô hình thiết bị, policy, execution, simulator, REST API, lưu bền gồm cấu hình nhà, đăng nhập và RBAC); 2 vòng hardening; tiêu chuẩn Visual-first; Sprint 1 ([nghiệm thu](SPRINT1_ACCEPTANCE.md)) |
| 🔧 **Đang làm** | Không có task P0 nào đang mở |
| 📋 **Còn lại** | 11 task P0 chưa làm (P0-08 … P0-18) |
| ⚠️ **Blocker / rủi ro** | Không còn Critical. High: Argon2id chưa đo trên máy đích (PENDING TARGET HARDWARE); chưa có TLS và topology production (P0-17). Cần thông tin Home Assistant và thiết bị pilot cho P0-09 |
| ➡️ **Tiếp theo** | LLD P0-08 rồi Sprint 2: P0-08 WebSocket, P0-09 Home Assistant |

### Tiến độ P0

```
Đạt       ███████░░░░░░░░░░░  7 / 18  (39 %)
Một phần  ░░░░░░░░░░░░░░░░░░  0 / 18
```
*Hình 1. Phương pháp: số task P0 đạt **mọi** tiêu chí nghiệm thu có test, chia tổng 18 task trong [ROADMAP.md](ROADMAP.md).*

### Ma trận trạng thái task

| Khu vực | Task |
|---|---|
| Điều khiển | ✅ 01 Capability · ✅ 02 Policy · ✅ 03 Execution · ✅ 04 Simulator · ✅ 05 REST API · ⏳ 14 Scene |
| Dữ liệu | ✅ 06 Postgres (lệnh, audit, cấu hình nhà) · ⏳ 10 Import cấu hình nhà |
| Danh tính, realtime | ✅ 07 Đăng nhập, RBAC · ⏳ 08 WebSocket |
| IoT | ⏳ 09 Home Assistant |
| Giao diện | ⏳ 11 Web app · ⏳ 12 3D view · ⏳ 13 Sửa vị trí 3D |
| AI | ⏳ 15 AI Agent |
| Vận hành | ⏳ 16 Observability · ⏳ 17 Triển khai local · ⏳ 18 Pilot |

*Hình 2. ✅ đạt · 🟡 một phần · ⏳ chưa làm. Ký hiệu luôn đi kèm chữ ở chú thích này.*

### Mốc đã đạt

```mermaid
timeline
    title Mốc trên main, 2026-11-23
    d9b67ff : Repo public, baseline sạch : Đường điều khiển nền P0-01 … P0-05
    27d13f0 : Hardening vòng 1 : ISSUE-01 … ISSUE-04
    a0e09e4 : Hardening vòng 2 : GAP-A … GAP-E
```
*Hình 3. Chỉ ghi mốc đã có commit. Chưa có lịch cho các task tiếp theo nên không vẽ Gantt.*

### Kết quả test và CI

| Commit | Test đạt (có Postgres) | | CI GitHub |
|---|---|---|---|
| `d9b67ff` | 79 | `████████████` | ✅ success |
| `27d13f0` | 127 | `███████████████████` | ✅ success |
| `a0e09e4` | 136 | `████████████████████` | ✅ success |
| `72ee113` | 188 | `███████████████████████████` | ✅ success |

*Hình 4. 0 test lỗi, 0 bỏ qua trên CI ở mọi commit. Từ `72ee113` gồm cả 52–53 test tài liệu. Nguồn: `python -m pytest -q`
trên máy và log của `gh run view`. Số test hiện tại (438, gồm 83 test tài liệu) và kết quả render Mermaid: xem IMPLEMENTATION_LOG.*

### Rủi ro

| Mức | Rủi ro chính |
|---|---|
| ⛔ Critical | Không có |
| 🔴 High | Argon2id chưa đo trên máy đích; chưa có TLS; Compose dev chưa có Postgres (P0-17); chưa có Home Assistant và chưa kiểm thiết bị thật |
| 🟠 Medium | Audit có thể thiếu khi DB gián đoạn; xác minh `state_match` phụ thuộc đồng hồ; rate limit chỉ cho đăng nhập |
| 🟡 Low | Lệnh chờ hết hạn nằm trong bộ nhớ |

*Hình 5. Ma trận đầy đủ theo khu vực: [SECURITY_AND_SAFETY.md §7](SECURITY_AND_SAFETY.md#7-rủi-ro-còn-mở).*

## 2. Technical Details

| Cần tra | Ở đâu |
|---|---|
| Từng thay đổi, file, lệnh kiểm chứng | [IMPLEMENTATION_LOG.md](IMPLEMENTATION_LOG.md) |
| Phân tích lỗi, thiết kế sửa, rollback | [HARDENING_2026-11.md](HARDENING_2026-11.md) |
| Tiêu chí nghiệm thu từng task | [ROADMAP.md](ROADMAP.md) |
| Phân bố test, cách chạy test Postgres | [TESTING_STRATEGY.md](TESTING_STRATEGY.md) |
| Tiêu chuẩn tài liệu và giao diện | [VISUAL_DESIGN_STANDARD.md](VISUAL_DESIGN_STANDARD.md) |

Lệnh tái tạo số liệu:

```powershell
python -m pytest -q
```

```powershell
gh run list --repo nguyenquangtiep/smart-home-future --branch main
```
