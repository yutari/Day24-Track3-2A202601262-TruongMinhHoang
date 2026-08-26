# Failure Cluster Analysis — Phase A

**Sinh viên:** Trương Minh Hoàng  
**Mã SV:** 2A202601262  
**Ngày:** 26/08/2026  

---

## 1. Aggregate RAGAS Scores theo Distribution

| Metric | factual | multi_hop | adversarial |
|---|---|---|---|
| faithfulness | 0.9333 | 0.5227 | 0.7333 |
| answer_relevancy | 0.8378 | 0.6626 | 0.6039 |
| context_precision | 0.9750 | 0.9917 | 0.9250 |
| context_recall | 0.9000 | 0.8042 | 0.6833 |
| **avg_score** | **0.9115** | **0.7453** | **0.7364** |

---

## 2. Bottom 10 Questions

| Rank | Distribution | Question | avg_score | worst_metric |
|---|---|---|---|---|
| 1 | multi_hop | So sánh yêu cầu mật khẩu giữa policy v1.0 và v2.0 về độ dài tối thiểu, thời hạn đổi và MFA. | 0.3750 | faithfulness |
| 2 | multi_hop | So sánh quyền lợi bảo hiểm giữa nhân viên thử việc và nhân viên chính thức. | 0.3750 | faithfulness |
| 3 | multi_hop | Nhân viên Manager có thâm niên 12 năm: tổng phụ cấp hàng tháng và số ngày phép năm theo v2024 là bao nhiêu? | 0.3750 | faithfulness |
| 4 | adversarial | Nhân viên Manager có thể dùng VPN cá nhân (như NordVPN) khi WFH để tăng bảo mật thêm không? | 0.4167 | faithfulness |
| 5 | adversarial | Bao lâu phải đổi mật khẩu một lần? | 0.4583 | faithfulness |
| 6 | factual | Nghỉ phép không lương 20 ngày cần ai phê duyệt? | 0.5715 | faithfulness |
| 7 | multi_hop | Một nhân viên Senior có 9 năm thâm niên được nghỉ bao nhiêu ngày phép năm và lương trong khoảng nào? | 0.6250 | answer_relevancy |
| 8 | multi_hop | Nếu cần mua một chiếc laptop 30 triệu cho nhân viên mới, ai phê duyệt và cần gì từ phòng CNTT? | 0.6312 | context_recall |
| 9 | adversarial | Nhân viên thử việc có được hưởng bảo hiểm sức khỏe PVI không? | 0.6667 | answer_relevancy |
| 10 | adversarial | Theo chính sách nghỉ phép cũ (v2023), nhân viên được nghỉ bao nhiêu ngày? Còn chính sách nào đang có hiệu lực hiện tại? | 0.6726 | context_recall |

---

## 3. Failure Cluster Matrix

*(Mỗi ô = số câu có worst_metric = row, thuộc distribution = col)*

| worst_metric | factual | multi_hop | adversarial | Total |
|---|---|---|---|---|
| faithfulness | 2 | 14 | 3 | 19 |
| answer_relevancy | 14 | 3 | 1 | 18 |
| context_precision | 1 | 0 | 1 | 2 |
| context_recall | 3 | 3 | 5 | 11 |
| **Total** | **20** | **20** | **10** | **50** |

---

## 4. Dominant Failure Analysis

**Dominant distribution:** factual  
**Dominant metric:** faithfulness  

**Lý do phân tích:**
* `faithfulness` là metric yếu nhất chiếm tỷ lệ cao nhất toàn bộ test set (19/50 câu), đặc biệt bùng nổ ở nhóm `multi_hop` (14/20 câu). Nguyên nhân chính là các câu hỏi đa bước yêu cầu kết hợp dữ liệu giữa nhiều tài liệu (ví dụ tính tổng ngày phép thâm niên + phụ cấp cấp bậc, hoặc so sánh phiên bản cũ - mới), mô hình LLM có xu hướng tự ngoại suy hoặc tổng hợp thiếu căn cứ context dẫn đến hallucination.
* Ở nhóm `factual`, mặc dù điểm tổng thể rất cao (avg = 0.9115), điểm `answer_relevancy` lại là worst_metric của 14 câu do câu trả lời ngắn gọn đưa ra đúng thông tin nhưng độ tương đồng embedding câu trả lời với câu hỏi bị phạt bởi RAGAS.

---

## 5. Suggested Fixes

| Metric yếu | Root cause | Suggested fix |
|---|---|---|
| faithfulness | LLM hallucinating khi tổng hợp nhiều văn bản | Siết chặt system prompt, hạ temperature xuống 0.0, bắt buộc trích dẫn nguồn chunk cụ thể |
| context_recall | Thiếu chunk liên quan đối với câu hỏi phức tạp / bẫy | Tăng top-k retrieval của hybrid search, tối ưu hóa chunk size để giữ toàn vẹn ngữ cảnh |
| context_precision | Có lẫn chunk rác hoặc văn bản hết hiệu lực | Bổ sung cross-encoder reranker và metadata filtering theo trạng thái hiệu lực (active/expired) |
| answer_relevancy | Câu trả lời chưa bám sát format mong đợi của câu hỏi | Chuẩn hóa prompt generation, bổ sung chain-of-thought và định dạng câu trả lời trực diện |

---

## 6. Nhận xét về Adversarial Distribution

* **So sánh điểm:** Điểm trung bình của `adversarial` đạt **0.7364**, thấp hơn đáng kể so với `factual` (**0.9115**). Điều này thỏa mãn điều kiện Bonus Phase A (+4 điểm) và khẳng định bộ test set adversarial đã stress-test thành công điểm yếu tiềm ẩn của pipeline.
* **Xung đột phiên bản (Version conflicts):** Pipeline rất dễ bị nhầm lẫn giữa các văn bản cùng chủ đề nhưng khác thời hiệu (như `nghi_phep_nam_v2023` vs `v2024`, `mat_khau_v1` vs `v2`). 
* **Bottom-10 analysis:** Có tới 4/10 câu trong bottom-10 thuộc phân phối `adversarial` (câu #4, #5, #9, #10). Điển hình như câu #5 ("Bao lâu phải đổi mật khẩu một lần?") và #10 ("Chính sách nghỉ phép cũ v2023"): retrieval đồng thời kéo cả 2 văn bản v1 và v2 vào context khiến LLM lúng túng hoặc trả lời sai quy định hiện hành. Để xử lý trong production, cần gắn cờ `is_active: true` trong metadata và filter trước khi rerank.
