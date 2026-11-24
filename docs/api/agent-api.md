# Hợp đồng API

Base URL khi chạy cục bộ: `http://127.0.0.1:8000`. Schema OpenAPI đầy đủ có tại `/docs` và `/openapi.json`.

## `GET /health`

Trả `200`:

```json
{"status": "ok"}
```

## `POST /api/v1/agent/invoke`

Request (`Content-Type: application/json`):

| Trường | Kiểu | Ràng buộc |
|---|---|---|
| `message` | string | bắt buộc; khoảng trắng đầu/cuối được cắt bỏ; sau khi cắt phải dài 1–4000 ký tự |

```json
{"message": "xin chào"}
```

Response `200`:

```json
{"reply": "Xin chào! Tôi là trợ lý Smart Home Future. Tôi có thể giúp gì cho bạn?"}
```

Lỗi:

| Mã | Khi nào |
|---|---|
| `422` | thiếu `message`, sai kiểu, chuỗi rỗng hoặc chỉ có khoảng trắng, quá 4000 ký tự, body không phải JSON |

Body lỗi `422` theo định dạng mặc định của FastAPI: `{"detail": [{"loc": [...], "msg": "...", ...}]}`.
