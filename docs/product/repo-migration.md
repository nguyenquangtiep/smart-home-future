# Repo và nguồn gốc nội dung

> **Cập nhật 23/11/2026: đã tách.** Repo `smart-home-future` (public) được tạo với một commit đầu tiên sạch, không mang lịch sử
> bản gốc. Đã đưa sang các mục "Đưa sang" ở §4 và `eval/datasets/agent_regression.jsonl` (cần cho test). Chưa đưa sang:
> `docs/adr/*`, `docs/development-process.md`, `docs/architecture/*` (chờ xác nhận) và mọi mục "Để ngoài repo mới".
> Đổi tên kỹ thuật ở §5 vẫn chưa làm. Phần dưới là bản rà soát gốc, giữ nguyên.

**Trả lời câu hỏi:** vì sao repo chưa đổi tên thành `smart-home-future`, phần nào đưa được sang sản phẩm mới, tên kỹ thuật nào
cần đổi ở lượt triển khai. Rà soát ngày 23/11/2026.

## 1. Hiện trạng đã xác minh

| Mục | Giá trị |
|---|---|
| Repo hiện tại | `(repo gốc, đã ẩn)`, **private**, nhánh mặc định `main` |
| Nhánh khác | `docs/pm-overview` (cả cục bộ và trên GitHub) |
| Tên đích | `smart-home-future`, **chưa có** repo nào mang tên này dưới cùng owner |
| URL đích dự kiến | `https://github.com/nguyenquangtiep/smart-home-future` (chưa tồn tại) |
| Thư mục cục bộ | Giữ tên cũ trong lượt này |

## 2. Vì sao dừng bước đổi tên

Lịch sử Git của repo bản gốc chứa nội dung lấy từ **đề bài gốc của chương trình** (file `de-tai-cohort-v-20261120.xlsx`,
nằm ngoài repo). Ví dụ:

- `docs/design/01-requirements.md` mở đầu bằng "Tóm tắt từ đề bài gốc" và chép phạm vi, ràng buộc, tiêu chí theo đề bài.
- `docs/design/07-verification-release.md`, `docs/assets/pm/*.svg` và bản cũ của `docs/project-overview.md` dẫn lại các yêu
  cầu và câu hỏi gửi người ra đề, mentor.
- Commit đầu tiên `a3c6aa4` đã có các file trên.

Quyền dùng lại đề bài trong một sản phẩm cá nhân **chưa rõ**. Đổi tên repo sẽ mang nguyên lịch sử này sang tên mới, nên
bước đổi tên dừng lại. Không force-push, không viết lại lịch sử, không xóa repo cũ.

## 3. Đề xuất: repo mới với baseline sạch

1. Tạo repo **private** `smart-home-future` dưới `nguyenquangtiep`.
2. Commit đầu tiên chỉ gồm phần được phân loại "Đưa sang" ở §4, không mang lịch sử cũ.
3. Giữ repo bản gốc private, không sửa lịch sử.
4. Cập nhật link trong README và tài liệu sản phẩm sang URL mới sau khi repo đã tạo.

Cần chủ dự án đồng ý trước khi làm bước 1.

## 4. Phân loại nội dung

| Phân loại | Đường dẫn | Lý do |
|---|---|---|
| **Đưa sang** | `docs/project-overview.md`, `docs/product/*`, `docs/assets/ui/01-ca-nha.svg`, `02-phong-khach.svg`, `03-tro-ly-theo-phong.svg`, `04-so-thich.svg` | Viết mới cho Smart Home Future, hình tự vẽ |
| **Đưa sang** | `src/`, `tests/`, `eval/scripts/`, `pyproject.toml`, `ruff.toml`, `Makefile`, `Dockerfile`, `docker-compose.yml`, `.github/workflows/ci.yml`, `.gitignore`, `.dockerignore`, `.env.example`, `AGENTS.md` | Scaffold kỹ thuật chung (FastAPI, LangGraph mẫu chào hỏi), không chứa nội dung đề bài; cần đổi tên kỹ thuật ở §5 |
| **Cần bạn xác nhận** | `eval/datasets/agent_regression.jsonl`, `docs/adr/*` | Tự viết nhưng nhắc bản gốc và quyết định gắn với đề bài cũ |
| **Cần bạn xác nhận** | `docs/development-process.md`, `docs/architecture/*` | Viết cho bản gốc; brief cũ ghi kiến trúc "dựa trên sơ đồ có sẵn", chưa rõ sơ đồ gốc của ai |
| **Để ngoài repo mới** | `docs/design/01-requirements.md` … `07-verification-release.md`, `docs/design/README.md` | Tóm tắt và bám theo đề bài gốc |
| **Để ngoài repo mới** | `docs/assets/pm/*`, `docs/assets/ui/01-dashboard.svg`, `02-ke-hoach-va-duyet.svg`, `03-ket-qua-va-lich-su.svg`, `docs/assets/ui/web/*` | Hình minh họa cho phạm vi đề bài cũ |
| **Để ngoài repo mới** | `presentation/README.md` | Dành cho Demo Day của chương trình |

Các file "để ngoài" vẫn nằm nguyên trong repo bản gốc. `docs/design/README.md` có ghi chú rằng bộ tài liệu đó thuộc dự án cũ.

## 5. Tên kỹ thuật cần đổi ở lượt triển khai

Giữ nguyên trong lượt này để scaffold, test và CI không bị hỏng.

| Hiện tại | Ở đâu | Đề xuất |
|---|---|---|
| Package `smarthome`, thư mục `src/smarthome/` | `pyproject.toml` (`name`, `packages`), `ruff.toml` (`known-first-party`), mọi `import smarthome…` trong `src/`, `tests/`, `eval/scripts/` | `smart_home_future` |
| Đường dẫn app `smarthome.api.main:app` | `Makefile`, `Dockerfile`, `README.md`, docstring `src/smarthome/api/main.py` | `smart_home_future.api.main:app` |
| Tiền tố biến môi trường `SMARTHOME_` | `src/smarthome/core/config.py`, `.env.example`, `docker-compose.yml`, `README.md` | `SHF_` |
| `app_name = "<tên cũ> Agent API"` | `src/smarthome/core/config.py`, `.env.example` | `Smart Home Future API` |
| Logger `smarthome` | `src/smarthome/core/logging.py`, `tests/integration/test_api.py` | `smart_home_future` |
| Câu chào "trợ lý Smart Home Future" | `src/smarthome/agent/nodes.py`, `eval/datasets/agent_regression.jsonl` | "trợ lý Smart Home Future" |
| Image `smart-home-future-api:local` | `docker-compose.yml` | `smart-home-future-api:local` |
| `description`, `authors` "bản gốc" | `pyproject.toml` | Theo tên sản phẩm |
| Thư mục cục bộ bản gốc | Máy của bạn | `smart-home-future`, sau khi repo mới đã tạo |
