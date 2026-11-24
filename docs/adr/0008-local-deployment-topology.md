# ADR 0008: Triển khai trên một máy trong LAN với Docker Compose, reverse proxy TLS và NTP

- **Trạng thái:** 📝 Proposed · **Ngày:** 2026-11-23 · **Chặn:** P0-17 · **Chi tiết:** [DEPLOYMENT.md](../DEPLOYMENT.md)

## Bối cảnh

- `docker-compose.yml` chỉ dành cho phát triển: service `api`, `SMARTHOME_ENVIRONMENT=local`, publish `127.0.0.1:8000`, không có
  Postgres, migrate, proxy → chạy bằng Compose thì lệnh và audit **không bền** (Finding DR-H04). Image mặc định `production` và
  fail-closed khi thiếu Postgres. (Trước Sprint 1 Compose đặt `docker`, giá trị nay bị từ chối.)
- Build Docker và chạy image với PostgreSQL được kiểm trong CI (`scripts/docker_smoke.sh`, từ 2026-11-24); topology đầy đủ của ADR này chưa kiểm.
- Xác minh `state_match` cần đồng hồ chính xác (ADR 0003). Cookie `Secure` cần HTTPS (ADR 0005).

## Phương án

| Phương án | Ưu | Nhược |
|---|---|---|
| **A. Một máy (mini PC) trong LAN: Compose gồm proxy TLS + app + Postgres; HA chạy riêng** | Local-first; đủ cho một nhà | Một điểm lỗi phần cứng |
| B. VPS cloud + kết nối về nhà | Truy cập từ xa dễ | Ngược local-first; mất Internet là mất điều khiển |
| C. Chạy cùng máy HA (add-on) | Ít phần cứng | Gắn vào HA OS; khó kiểm soát Postgres |

## Quyết định (đề xuất)

**A.** Compose: `proxy` (Caddy, TLS nội bộ), `app` (1 worker), `db` (Postgres 16, volume riêng), job `migrate` chạy trước
`app`. Truy cập từ xa chỉ qua VPN (WireGuard/Tailscale), không mở cổng. Máy chủ bật NTP. Backup `pg_dump` hằng ngày, đã thử
khôi phục trước pilot.

## Hệ quả

- Sửa `docker-compose.yml` và thêm cấu hình proxy trong P0-17 (không thuộc lượt review này).
- `/health` cần tách `live` và `ready` (DB, gateway) để Compose/proxy biết khi nào app sẵn sàng (P0-16).

## Khi nào xem lại

Thương mại hóa với nhiều nhà (P2) hoặc cần HA phần cứng.
