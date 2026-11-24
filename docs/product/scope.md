# Smart Home Future: phạm vi và hành vi

> **Phạm vi v1.** Một số điểm (sơ đồ 2D, một người dùng) đã được thay bởi [PRD](../PRD.md) và
> [PRODUCT_VISION §5](../PRODUCT_VISION.md). Các ca nghiệm thu hành vi Trợ lý vẫn dùng được.

**Trả lời câu hỏi:** bản web đầu tiên làm gì, Trợ lý hiểu câu nói và chạy kế hoạch thế nào, nghiệm thu bằng ca nào.
Trang tổng quan ngắn hơn ở [Tổng quan sản phẩm](../project-overview.md), hình các màn ở [Giao diện web desktop](ui-desktop.md).

Tất cả là **thiết kế mục tiêu**, chưa triển khai. Mục ghi **(đề xuất)** cần chủ dự án xác nhận trước khi code.

## 1. Phạm vi

### 1.1 Bản web đầu tiên

| Hạng mục | Gồm | Ngoài phạm vi |
|---|---|---|
| Nhà | Một nhà, hai phòng: Phòng khách và Phòng ngủ, trên sơ đồ 2D bấm được | Nhiều nhà, nhiều tầng, tự vẽ sơ đồ |
| Thiết bị | Phòng khách: đèn trần (bật/tắt, độ sáng), rèm (mở/đóng), điều hòa (nhiệt độ). Phòng ngủ: đèn, chỉ xem trạng thái | Thiết bị thật, thêm/bớt thiết bị |
| Trợ lý | Chat chữ tiếng Việt, kế hoạch theo từng bước, nút **Chạy** và **Sửa**, kết quả từng bước | Giọng nói, gợi ý chủ động, tự chạy |
| Thiết bị mô phỏng | Do dự án tự viết; trả phản hồi, có thể chậm hoặc im lặng để thử ca lỗi | Home Assistant |
| Sở thích | Một sinh hoạt "Xem phim" ở Phòng khách; một giá trị lưu được (nhiệt độ điều hòa); xem, sửa, xóa | Nhiều sinh hoạt, học tự động từ thói quen |
| Người dùng | Một người dùng ("Bạn") | Nhiều thành viên, phân quyền |

**Chế độ xem phim mặc định (đề xuất):** đèn trần 20%, đóng rèm, điều hòa 26°C. Đây là mẫu có sẵn; sở thích đã lưu ghi đè
từng giá trị.

### 1.2 Mốc tiếp theo: giọng nói

- Bấm micro, nói; câu đã nhận dạng hiện trong ô chat như câu gõ, người dùng sửa được trước khi gửi khi cần.
- Sau khi nhận dạng, đi đúng luồng chat: cùng ngữ cảnh phòng, cùng kế hoạch, cùng nút **Chạy**.
- Trên bản web đầu tiên, micro hiện mờ với nhãn "Giọng nói: mốc sau".

### 1.3 Mở rộng: gợi ý theo ngữ cảnh

- Ví dụ: bật TV ở Phòng khách → Trợ lý đề xuất "Dùng chế độ xem phim đã lưu?".
- Người dùng có thể **bỏ qua** một gợi ý hoặc **tắt** cả loại gợi ý đó.
- **Quyền tự chạy** là cài đặt riêng, mặc định tắt. Khi chưa bật, gợi ý luôn chờ người dùng bấm.

### 1.4 Thiết bị thật

Hướng tích hợp là **Home Assistant**: một adapter thay cho thiết bị mô phỏng, giữ nguyên luồng kế hoạch, xác minh và nhãn
trạng thái. Chưa thuộc bản web đầu tiên.

## 2. Hành vi cần chốt trước khi code

### 2.1 Ngữ cảnh phòng

1. Phòng được gọi rõ trong câu ("tắt đèn phòng ngủ") được ưu tiên.
2. Nếu câu không gọi tên phòng, dùng **phòng đang mở** làm ngữ cảnh mặc định. Ngữ cảnh luôn hiện trên đầu khung chat.
3. Nếu vẫn mơ hồ (đang ở trang Cả nhà và có nhiều phòng phù hợp), Trợ lý **hỏi lại** bằng nút chọn phòng và chưa làm gì
   ([màn 1](ui-desktop.md#1-cả-căn-nhà)).

### 2.2 Ba loại câu nói

| Loại | Ví dụ | Trợ lý làm |
|---|---|---|
| Mô tả | "Tôi sắp xem phim" | Gợi ý hoặc hỏi xác nhận: "Chuẩn bị Phòng khách để xem phim nhé?". Không lập kế hoạch chạy ngay |
| Yêu cầu đề xuất | "Xem phim thì nên chỉnh gì?" | Đưa kế hoạch, không có thiết bị nào thay đổi |
| Lệnh thực thi | "Chuẩn bị xem phim", "Tắt đèn phòng khách" | Đưa kế hoạch kèm nút **Chạy** |

**(đề xuất)** Mọi kế hoạch do Trợ lý lập, kể cả một bước, cần một chạm **Chạy**. Nút trên bảng thiết bị và điểm bấm trên
hình vẫn chạy ngay.

### 2.3 Chạy kế hoạch và xác minh

- Chạy tuần tự theo thứ tự hiện trên kế hoạch.
- Mỗi bước có một trong bốn nhãn: **Chưa chạy**, **Đang chờ**, **Đã xác minh**, **Không xác minh được**.
- Trạng thái trên hình và trên bảng thiết bị **chỉ đổi khi có phản hồi xác minh**. Trước đó hình giữ giá trị đã xác minh gần
  nhất.
- **(đề xuất)** Hết **5 giây** không có phản hồi khớp lệnh → bước đó "Không xác minh được".
- **(đề xuất)** Một bước không xác minh được thì **dừng**: các bước sau giữ nhãn "Chưa chạy" và Trợ lý nói rõ bước nào chưa
  chạy. Nếu đó là bước cuối, Trợ lý nói "không còn bước nào chưa chạy".
- Sau lỗi, Trợ lý đề nghị **Thử lại** bước lỗi hoặc **Để vậy**. Không tự thử lại.

### 2.4 Sở thích

- Sở thích gắn với bộ khóa **người dùng + phòng + sinh hoạt**, ví dụ Bạn · Phòng khách · Xem phim.
- Câu như "Lần sau để 27 độ" luôn dẫn tới câu hỏi: **Chỉ lần này** hoặc **Lưu cho chế độ xem phim**. Chưa trả lời thì không
  ghi gì.
- **Chỉ lần này:** đặt 27°C ngay, không lưu. Lần sau vẫn đề xuất giá trị mặc định 26°C.
- **Lưu cho chế độ xem phim:** ghi sở thích. **(đề xuất)** Không tự đổi nhiệt độ lần này; Trợ lý đưa nút "Đổi luôn 27°C".
- Khi lập kế hoạch đúng bộ khóa, Trợ lý dùng giá trị đã lưu và gắn nhãn **Theo sở thích**. Bộ khóa so với **phòng đã xác
  định** theo §2.1, không phải trang đang mở: ở trang Cả nhà, câu "xem phim" chỉ khớp Phòng khách thì vẫn áp dụng. Ở phòng
  khác, không áp dụng.
- Sở thích chỉ làm thay đổi đề xuất. Người dùng vẫn bấm **Chạy**.
- Màn Sở thích cho **xem**, **sửa** (bước tăng/giảm, Lưu/Hủy) và **xóa** (hỏi xác nhận; sau khi xóa quay về mặc định).
- Nếu câu "Lần sau…" đến khi chưa rõ sinh hoạt nào, Trợ lý hỏi sinh hoạt trước.

## 3. Ca nghiệm thu

Mỗi ca chạy trên bản web đầu tiên với thiết bị mô phỏng, bắt đầu từ dữ liệu sạch (chưa có sở thích).

### AT-01 Luồng chính: lưu sở thích và dùng lại

1. Mở trang Cả nhà. Thấy sơ đồ hai phòng.
2. Bấm Phòng khách. Thấy hình phòng với đèn, rèm, điều hòa và bảng thiết bị.
3. Gõ "Chuẩn bị phòng khách để xem phim". Ngữ cảnh hiện "Phòng khách".
4. Thấy kế hoạch ba bước: đèn 20%, đóng rèm, điều hòa 26°C. Chưa thiết bị nào đổi.
5. Bấm **Chạy**. Mỗi bước qua "Đang chờ" rồi "Đã xác minh"; hình đổi theo từng bước sau khi xác minh.
6. Gõ "Lần sau để 27 độ". Thấy hai lựa chọn. Chọn **Lưu cho chế độ xem phim**.
7. Mở Sở thích. Thấy một mục: Bạn · Phòng khách · Xem phim · điều hòa 27°C, có Sửa và Xóa.
8. Quay lại Phòng khách, gõ "Chuẩn bị xem phim". Kế hoạch đề xuất điều hòa **27°C** với nhãn "Theo sở thích".
9. Mở Phòng ngủ, gõ "Chuẩn bị xem phim": Trợ lý không dùng 27°C và nói rõ Phòng ngủ chưa có chế độ xem phim.

**Đạt khi:** bước 8 hiện 27°C mà người dùng không phải nhắc lại; bước 4 và bước 8 không có thiết bị nào đổi trước khi bấm Chạy.

### AT-02 Chỉ lần này: không lưu sở thích

1–5 như AT-01.
6. Gõ "Lần sau để 27 độ", chọn **Chỉ lần này**. Điều hòa chuyển sang 27°C sau khi xác minh.
7. Mở Sở thích: không có mục nào.
8. Gõ lại "Chuẩn bị xem phim" ở Phòng khách: kế hoạch đề xuất **26°C**, không có nhãn "Theo sở thích".

### AT-03 Điều hòa không phản hồi

Cấu hình điều hòa mô phỏng không trả lời. Chạy kế hoạch xem phim.
**Đạt khi:** kết quả "2/3 bước đã xác minh"; điều hòa "Không xác minh được"; hình vẫn hiện 28°C; Trợ lý nói rõ không còn
bước nào chưa chạy và đưa nút Thử lại. Biến thể: rèm im lặng → bước điều hòa hiện "Chưa chạy".

### AT-04 Câu mơ hồ ở trang Cả nhà

Ở trang Cả nhà, gõ "Tắt đèn". **Đạt khi:** Trợ lý hỏi Phòng khách, Phòng ngủ hay Cả hai; không đèn nào đổi trước khi chọn.

### AT-05 Câu mô tả không gây thao tác

Ở Phòng khách, gõ "Tôi sắp xem phim". **Đạt khi:** Trợ lý hỏi có muốn chuẩn bị chế độ xem phim không; không có kế hoạch chạy
và không thiết bị nào đổi.

## 4. Đối chiếu nhanh

| Mục tiêu | Màn | Ca |
|---|---|---|
| Biết hệ thống định làm gì | [3 · Trợ lý theo phòng](ui-desktop.md#3-trợ-lý-theo-phòng) | AT-01 bước 4 |
| Biết đã làm gì và bước nào chưa xác minh | [2 · Phòng khách](ui-desktop.md#2-phòng-khách), [3](ui-desktop.md#3-trợ-lý-theo-phòng) | AT-01 bước 5, AT-03 |
| Lần sau nói ít hơn | [4 · Sở thích](ui-desktop.md#4-sở-thích) | AT-01 bước 8 |
| Chỉ lưu khi người dùng đồng ý | [4 · Sở thích](ui-desktop.md#4-sở-thích) | AT-02 |
| Hỏi lại khi mơ hồ | [1 · Cả căn nhà](ui-desktop.md#1-cả-căn-nhà) | AT-04, AT-05 |
