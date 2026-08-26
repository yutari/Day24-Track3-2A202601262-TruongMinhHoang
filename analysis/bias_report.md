# LLM Judge Bias Report — Phase B

**Sinh viên:** Trương Minh Hoàng  
**Mã SV:** 2A202601262  
**Ngày:** 26/08/2026  
**Judge model:** gpt-4o-mini  

---

## 1. Pairwise Judge Results

*(Chạy `pairwise_judge()` trên 5 cặp câu trả lời A vs B điển hình)*

| # | Question (tóm tắt) | Winner | Reasoning tóm tắt |
|---|---|---|---|
| 1 | Số ngày phép năm (v2024 vs v2023) | A | Answer A cung cấp thông tin chính xác theo chính sách v2024 (15 ngày); Answer B đưa thông tin cũ v2023 (12 ngày). |
| 2 | Mua sắm thiết bị văn phòng 55 triệu | A | Answer A nêu đúng quy trình: trên 50 triệu phải do CEO phê duyệt; Answer B sai thẩm quyền phê duyệt. |
| 3 | Bảo hiểm sức khỏe PVI cho nhân viên thử việc | A | Answer A chính xác: thử việc chưa có PVI (chỉ có sau khi ký HĐCT); Answer B sai lệch hoàn toàn. |
| 4 | Dùng VPN cá nhân (NordVPN) khi WFH | A | Answer A tuân thủ chính sách bảo mật: bắt buộc dùng WireGuard của công ty và cấm VPN cá nhân; Answer B cho phép tự do là vi phạm. |
| 5 | Thời hạn đổi mật khẩu nội bộ | A | Answer A cập nhật chính xác theo policy v2.0 (tối đa 90 ngày + MFA); Answer B dùng chính sách v1.0 cũ đã hết hiệu lực (180 ngày). |

---

## 2. Swap-and-Average Results

*(Chạy `swap_and_average()` trên cùng 5 cặp đối chứng đảo vị trí)*

| # | Pass 1 Winner | Pass 2 Winner (đã convert) | Final | Position Consistent? |
|---|---|---|---|---|
| 1 | A | A | A | True |
| 2 | A | A | A | True |
| 3 | A | A | A | True |
| 4 | A | A | A | True |
| 5 | A | A | A | True |

**Position bias rate:** **0.0%** (0 / 5 cases inconsistent)  
**Nhận xét:** Kết quả cho thấy judge giữ vững quyết định ở cả hai lượt đảo thứ tự (Pass 1 và Pass 2), không bị thiên vị vị trí đứng trước.

---

## 3. Cohen's κ Analysis

**Human labels:** `human_labels_10q.json` (10 câu, 5 label=1, 5 label=0)  
**Judge labels:** Kết quả chạy judge trên 10 câu tương ứng qua `judge_single_answer()`

| Question ID | Human Label | Judge Label | Agree? | Ghi chú từ nhãn người |
|---|---|---|---|---|
| 1 | 1 | 1 | Match | Nghỉ kết hôn 3 ngày có lương (Chính xác & đầy đủ). |
| 5 | 0 | 0 | Match | 55 triệu vượt ngưỡng 50 triệu nên phải CEO duyệt (Model trả lời Director). |
| 12 | 1 | 1 | Match | Thưởng Tết tối thiểu 1 tháng lương (Đúng & súc tích). |
| 21 | 1 | 1 | Match | Senior 9 năm thâm niên: 18 ngày phép, lương 20-35tr (Tính đúng cả 2). |
| 23 | 1 | 1 | Match | Khóa học 25tr nghỉ việc sau 8 tháng: bồi hoàn 100% (25tr). |
| 29 | 0 | 0 | Match | Tạm ứng 8tr: thiếu Kế toán trưởng duyệt và tính phạt pro-rata. |
| 33 | 1 | 1 | Match | Manager 12 năm thâm niên: 19 ngày phép, 1.500.000 VNĐ phụ cấp. |
| 41 | 0 | 0 | Match | Trả lời 12 ngày phép theo v2023 cũ đã hết hiệu lực (v2024 là 15 ngày). |
| 46 | 1 | 1 | Match | Thử việc không có phép năm, phải xin nghỉ không lương. |
| 50 | 0 | 0 | Match | Cho phép dùng VPN cá nhân là sai chính sách WireGuard v1.3. |

**Cohen's κ:** **1.0000**  
**Interpretation:** **almost perfect agreement** (Đạt điều kiện Bonus Phase B: $\kappa > 0.6$).

---

## 4. Verbosity Bias

Trong các case có winner rõ ràng (không phải tie):
- A thắng + A dài hơn B: **5 / 5** cases (100%)
- B thắng + B dài hơn A: **0 / 5** cases
- **Verbosity bias rate:** **100.0%**

**Kết luận:** 
Trong bộ 5 câu thử nghiệm, câu A vừa đúng chính xác vừa trình bày đầy đủ điều kiện bối cảnh hơn câu B (ngắn nhưng sai hoặc thiếu). Tuy nhiên, tỷ lệ 100% cũng phản ánh LLM Judge có xu hướng đánh giá cao các câu trả lời dài, phân tích nhiều khía cạnh. Trong môi trường production, cần bổ sung cơ chế kiểm soát độ súc tích (conciseness constraint) để tránh trường hợp model bị đánh lừa bởi các câu trả lời dài dòng nhưng ngụy biện.

---

## 5. Nhận xét chung

1. **Độ tin cậy của Judge:** Hệ số tương quan Cohen's $\kappa = 1.0000$ với nhãn chuyên gia con người cho thấy LLM Judge (`gpt-4o-mini`) hoàn toàn đáng tin cậy khi được cung cấp prompt rõ ràng, có tiêu chuẩn đánh giá chặt chẽ (độ chính xác, đầy đủ, súc tích) và neo theo quy định hiện hành.
2. **Hiệu quả của Swap-and-Average:** Kỹ thuật swap-and-average là bắt buộc trong production eval. Khi đảo thứ tự A/B, nếu judge chọn tráo đổi thì chứng tỏ có position bias và cần trả về `tie` thay vì công nhận winner giả mạo.
3. **Ứng dụng trong CI/CD:** LLM Judge kết hợp swap-and-average có thể triển khai thành Automated Regression Gate trong CI pipeline: mỗi khi prompt template hoặc chunking strategy thay đổi, hệ thống sẽ so sánh pairwise giữa output mới và baseline cũ để đảm bảo không bị suy giảm chất lượng.
