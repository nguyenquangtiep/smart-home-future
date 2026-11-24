# ADR 0005: Đăng nhập bằng session phía server, cookie HttpOnly, CSRF token, Argon2id

- **Trạng thái:** ✅ **Accepted** (2026-11-23, chủ dự án duyệt theo khuyến nghị kỹ thuật, D1–D6 ở mục 6) · **Chưa triển khai**
- **Ngày:** 2026-11-23 (rà soát lần 2) · **Chặn:** P0-07 · **LLD:** [P0-07](../lld/P0-07_AUTH_RBAC.md)

## 1. Bối cảnh (có dẫn chứng)

- Chỉ có danh tính dev qua header `X-Dev-User`, mặc định tắt (`api/identity.py`).
- Cổng môi trường fail-open: chỉ so `environment == "production"` đúng chữ; `environment` là chuỗi tự do, mặc định `local`
  (`api/main.py`, `core/config.py`). Compose hiện đặt `docker`, tức đã nằm ngoài cổng.
- Thành viên của nhà là dict tĩnh trong `ControlService` (`execution.py`); `confirm()` dùng nó để kiểm tra lại người yêu cầu.
- `/api/v1/agent/invoke` không có xác thực; OpenAPI luôn mở.
- Một worker duy nhất ([ADR 0001](0001-modular-monolith-single-instance.md)): việc nặng CPU trong coroutine chặn cả điều khiển thiết bị.
- Web (P0-11) và WebSocket (P0-08) dùng chung một cơ chế; truy cập từ xa chỉ qua VPN ([ADR 0008](0008-local-deployment-topology.md)).

## 2. Phương án

| Phương án | Ưu | Nhược |
|---|---|---|
| **A. Session phía server (Postgres) + cookie HttpOnly + CSRF token** | Thu hồi tức thì; JS không đọc được token; dùng chung cho WebSocket | Mỗi request tra DB (một nhà: rẻ; đo ở P0-16) |
| B. JWT trong localStorage | Không cần DB | XSS đọc được token; khó thu hồi |
| C. JWT trong cookie | JS không đọc được | Vẫn khó thu hồi; vẫn cần CSRF |
| D. Đăng nhập qua Home Assistant (OAuth) | Một tài khoản | Gắn chặt vào HA; không có HA thì không đăng nhập được |
| E. OIDC ngoài (Google…) | Không giữ mật khẩu | Phụ thuộc Internet: ngược local-first |

Băm mật khẩu: **Argon2id** (`argon2-cffi`) so với `hashlib.scrypt` của thư viện chuẩn. `scrypt` tránh được dependency mới nhưng
Argon2id là khuyến nghị hiện hành của OWASP và có tham số bộ nhớ/thời gian/song song rõ ràng; chọn Argon2id.

## 3. Quyết định đề xuất

Thông số đầy đủ ở [LLD P0-07 §3](../lld/P0-07_AUTH_RBAC.md#3-quyết-định-thiết-kế); đây là phần cốt lõi:

| # | Quyết định |
|---|---|
| 1 | Token phiên 256 bit ngẫu nhiên; DB chỉ lưu SHA-256; mỗi lần đăng nhập tạo token mới |
| 2 | Cookie `HttpOnly; Secure; SameSite=Strict; Path=/`, không `Domain` |
| 3 | CSRF: token theo phiên + kiểm `Origin` allowlist cho mọi request ghi; `login` kiểm `Origin` |
| 4 | Argon2id, chạy trong thread pool; chống liệt kê tài khoản (băm giả, cùng phản hồi) |
| 5 | Giới hạn thử sai bền trong DB: 5/email và 30/IP trong 15 phút; IP chỉ từ proxy tin cậy |
| 6 | Membership tra DB mỗi request và mỗi lần duyệt lệnh (`MembershipSource`) |
| 7 | Default-deny: mọi route trừ `/health` và `login` cần phiên |
| 8 | Cổng môi trường theo allowlist `{local, test}`; ngoài đó bắt buộc DB, cấm danh tính dev |

## 4. Mô hình mối đe dọa

| # | Mối đe dọa | Biện pháp | Còn lại |
|---|---|---|---|
| T1 | Dò mật khẩu, nhồi thông tin đăng nhập | Giới hạn 5/email, 30/IP; Argon2id; chính sách mật khẩu | Khóa tài khoản bằng cách cố ý nhập sai (Medium, chấp nhận; CLI `unlock`) |
| T2 | Liệt kê tài khoản | Cùng `401`, băm giả, giới hạn theo hash email kể cả email không tồn tại | `EMAIL_IN_USE` khi owner thêm thành viên (Low) |
| T3 | Trộm token phiên qua XSS | `HttpOnly`; CSP chặt ở P0-11 | XSS vẫn gửi được request ngay trong trang (Medium) |
| T4 | CSRF, login CSRF | `SameSite=Strict` + `Origin` + token; login kiểm `Origin` | — |
| T5 | Chiếm WebSocket từ trang khác | Mục 5 | — |
| T6 | Session fixation | Token mới mỗi lần đăng nhập; client không đặt được ID | — |
| T7 | Phát lại cookie qua HTTP thuần | `Secure` + TLS ở proxy ([ADR 0008](0008-local-deployment-topology.md)) | Cần proxy đúng cấu hình |
| T8 | Lộ nội dung DB | Chỉ lưu SHA-256 token và hash Argon2id | `csrf_token` lưu nguyên văn: vô dụng nếu thiếu cookie HttpOnly |
| T9 | Leo thang đặc quyền bằng sửa membership | Chỉ owner; last-owner; ghi `auth_events` và `audit_events` | — |
| T10 | Truy cập chéo nhà (IDOR) | Membership mỗi request, người ngoài nhận `404`; test tham số hóa mọi route | — |
| T11 | Danh tính dev lọt vào môi trường thật | Allowlist môi trường, từ chối khởi động | — |
| T12 | Lộ bí mật qua log | Access log chỉ method, path, status; `auth_events` không chứa bí mật | IP là dữ liệu cá nhân (Medium, retention) |
| T13 | Giả IP để né giới hạn | Chỉ tin header từ `SMARTHOME_TRUSTED_PROXY_IPS` | Cấu hình sai danh sách proxy làm vô hiệu giới hạn IP |
| T14 | Clickjacking | `frame-ancestors 'none'` ở proxy / P0-11 | Chưa có (P0-11, P0-17) |
| T15 | Argon2 làm nghẽn worker duy nhất | Thread pool; AC-07-17 | Chưa đo trên máy đích (M-07-1) |

## 5. WebSocket: ràng buộc cho ADR 0007

Áp dụng cho [ADR 0007](0007-realtime-websocket-per-home.md).

| Ràng buộc | Lý do |
|---|---|
| Xác thực bằng **cùng cookie phiên** ở bước bắt tay; không đưa token vào URL hay query | Tránh lộ token qua log, lịch sử, Referer |
| Kiểm `Origin` ở bước bắt tay theo allowlist | Bắt tay WebSocket không chịu CORS; đây là biện pháp chống chiếm kết nối từ trang khác |
| Kiểm membership của `home_id` ở bắt tay | Cô lập nhà |
| Server → client một chiều; lệnh vẫn đi qua REST | Giữ idempotency, CSRF, mã lỗi HTTP; không cần CSRF trên WebSocket |
| Phiên bị thu hồi, hết hạn, user bị tắt, hoặc membership bị gỡ → server đóng kết nối (mã 4401) trong tối đa 60 giây | Quyền không kéo dài sau khi bị thu hồi |

Chưa có code WebSocket; các ràng buộc trên trở thành AC của LLD P0-08.

## 6. Quyết định cần chủ dự án

| # | Câu hỏi | Khuyến nghị | Quyết định (2026-11-23) | Đánh đổi |
| --- | --- | --- | --- | --- |
| D1 | Duyệt ADR này? | **Accept** | ✅ Chấp nhận khuyến nghị | Không duyệt thì P0-07 và mọi task phụ thuộc (P0-08, P0-11) đứng |
| D2 | Chấp nhận dependency `argon2-cffi`? | **Có** | ✅ Chấp nhận khuyến nghị | Thay bằng `hashlib.scrypt`: không thêm dependency, kém chuẩn hơn |
| D3 | `SameSite=Strict` hay `Lax`? | **Strict** | ✅ Chấp nhận khuyến nghị | Strict: mở link từ trang khác thì lần điều hướng đầu không mang cookie; SPA gọi API vẫn bình thường |
| D4 | Chấp nhận khóa tài khoản do nhập sai cố ý? | **Chấp nhận** | ✅ Chấp nhận khuyến nghị | Phòng thủ bằng CLI `unlock`; bỏ giới hạn theo email sẽ yếu chống dò mật khẩu |
| D5 | Phiên: nhàn rỗi 7 ngày, tối đa 30 ngày? | **Chấp nhận** | ✅ Chấp nhận khuyến nghị | Chỉnh được bằng biến môi trường |
| D6 | Tạo owner đầu tiên bằng CLI, không qua web? | **Chấp nhận** | ✅ Chấp nhận khuyến nghị | Cần quyền truy cập máy chủ; không có đường tạo owner nặc danh |

### Phê duyệt

| Người duyệt | Quyết định | Ngày | Ghi chú |
|---|---|---|---|
| Chủ dự án | ☑ **Accepted** theo khuyến nghị: D1–D6 đều chấp nhận, gồm dependency `argon2-cffi` | 2026-11-23 | Xác nhận trong phiên làm việc (trả lời "Duyệt cả hai") |

Trạng thái và [mục lục ADR](README.md) đã cập nhật. Chấp nhận ADR **không** có nghĩa đã triển khai: việc này thuộc Sprint 1 (S1-01, S1-05…S1-09).

## 7. Hệ quả

- Thêm dependency `argon2-cffi`; thêm migration `0003`.
- Web phục vụ **cùng origin** với API qua reverse proxy; khác origin cần CORS có credentials (không khuyến nghị).
- Cần `SMARTHOME_TRUSTED_PROXY_IPS` đúng; thiếu thì giới hạn theo IP hoặc khóa tất cả, hoặc không có tác dụng.
- Sự kiện đăng nhập vào bảng `auth_events` riêng vì `audit_events.home_id` là `NOT NULL`.
- Test dùng base URL `https://` vì cookie `Secure`.

## 8. Khi nào xem lại

Cần đăng nhập không mật khẩu (passkey/WebAuthn), nhiều hộ dùng chung danh tính (P2), hoặc đo được tra phiên làm tăng độ trễ
điều khiển vượt NFR-01.
