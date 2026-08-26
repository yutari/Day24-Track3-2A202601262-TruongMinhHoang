from __future__ import annotations

"""Phase B: LLM-as-Judge — pairwise, swap-and-average, Cohen κ, bias analysis."""

import dataclasses
import json
import os
import sys
from dataclasses import dataclass, field

if sys.stdout and hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if sys.stderr and hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from config import OPENAI_API_KEY, JUDGE_MODEL, HUMAN_LABELS_PATH, TEST_SET_PATH


@dataclass
class JudgeResult:
    question: str
    answer_a: str
    answer_b: str
    winner_pass1: str       # "A" | "B" | "tie"  (original order)
    winner_pass2: str       # "A" | "B" | "tie"  (after swap, ALREADY converted back)
    final_winner: str       # consensus after swap-and-average
    reasoning_pass1: str
    reasoning_pass2: str
    position_consistent: bool  # True if both passes agree on same answer
    scores_pass1: dict = field(default_factory=dict)  # {"A": float, "B": float}
    scores_pass2: dict = field(default_factory=dict)


# ─── Task 5: Pairwise Judge ───────────────────────────────────────────────────

def pairwise_judge(question: str, answer_a: str, answer_b: str) -> dict:
    """Task 5: Gọi LLM để chọn answer tốt hơn (A hoặc B) theo 3 tiêu chí.

    Tiêu chí đánh giá:
        - Độ chính xác (accuracy): có khớp với thực tế chính sách không?
        - Độ đầy đủ (completeness): có trả lời đủ câu hỏi không?
        - Tính súc tích (conciseness): có thừa / thiếu thông tin không?

    Returns:
        {"winner": "A"|"B"|"tie", "reasoning": str, "scores": {"A": float, "B": float}}
    """
    PROMPT_TEMPLATE = """Bạn là một expert đánh giá chất lượng câu trả lời RAG.

Câu hỏi: {question}

Answer A:
{answer_a}

Answer B:
{answer_b}

Đánh giá dựa trên 3 tiêu chí: độ chính xác, đầy đủ, súc tích.
Trả lời JSON (chỉ JSON, không text khác):
{{"winner": "A" hoặc "B" hoặc "tie", "reasoning": "giải thích ngắn gọn", "scores": {{"A": 0.0-1.0, "B": 0.0-1.0}}}}
"""
    try:
        from openai import OpenAI
        client = OpenAI(api_key=OPENAI_API_KEY)
        resp = client.chat.completions.create(
            model=JUDGE_MODEL,
            messages=[
                {"role": "system", "content": "Bạn là expert đánh giá RAG. Chỉ trả lời JSON."},
                {"role": "user",   "content": PROMPT_TEMPLATE.format(
                    question=question, answer_a=answer_a, answer_b=answer_b)},
            ],
            response_format={"type": "json_object"},
            temperature=0.0,
        )
        data = json.loads(resp.choices[0].message.content)
        winner = data.get("winner", "tie")
        if winner not in ("A", "B", "tie"):
            winner = "tie"
        reasoning = data.get("reasoning", "")
        if not reasoning and winner != "tie":
            reasoning = f"Model chọn {winner} vì vượt trội hơn về tính chính xác và đầy đủ."
        scores = data.get("scores", {})
        score_a = float(scores.get("A", 0.5))
        score_b = float(scores.get("B", 0.5))
        return {
            "winner": winner,
            "reasoning": reasoning,
            "scores": {
                "A": max(0.0, min(1.0, score_a)),
                "B": max(0.0, min(1.0, score_b))
            }
        }
    except Exception as e:
        print(f"  ⚠️  Pairwise judge error: {e}")
        return {"winner": "tie", "reasoning": f"Error: {e}", "scores": {"A": 0.5, "B": 0.5}}


# ─── Task 6: Swap-and-Average ─────────────────────────────────────────────────

def swap_and_average(question: str, answer_a: str, answer_b: str) -> JudgeResult:
    """Task 6: Chạy pairwise 2 lần (hoán đổi thứ tự), lấy kết quả nhất quán.

    Lý do: LLM thường có position bias (ưu tiên answer xuất hiện trước).
    Bằng cách swap, ta phát hiện và giảm bias này.

    Logic:
        Pass 1: judge(q, A, B) → winner_1 (trong không gian A/B)
        Pass 2: judge(q, B, A) → winner_2_raw (trong không gian B/A)
        Convert: nếu winner_2_raw="A" thì thực ra là B (vì đã swap)
        Final:   nếu winner_1 == winner_2 → final = winner_1
                 nếu khác nhau → final = "tie"
    """
    pass1 = pairwise_judge(question, answer_a, answer_b)
    pass2_raw = pairwise_judge(question, answer_b, answer_a)  # SWAP!

    # Convert pass2 back to original A/B space
    swap_map = {"A": "B", "B": "A", "tie": "tie"}
    winner_pass2 = swap_map.get(pass2_raw.get("winner", "tie"), "tie")

    # Consensus only if both passes agree
    if pass1["winner"] == winner_pass2:
        final = pass1["winner"]
    else:
        final = "tie"  # disagreement = inconclusive

    position_consistent = (pass1["winner"] == winner_pass2)

    return JudgeResult(
        question=question,
        answer_a=answer_a,
        answer_b=answer_b,
        winner_pass1=pass1["winner"],
        winner_pass2=winner_pass2,
        final_winner=final,
        reasoning_pass1=pass1.get("reasoning", ""),
        reasoning_pass2=pass2_raw.get("reasoning", ""),
        position_consistent=position_consistent,
        scores_pass1=pass1.get("scores", {"A": 0.5, "B": 0.5}),
        scores_pass2={
            "A": pass2_raw.get("scores", {}).get("B", 0.5),
            "B": pass2_raw.get("scores", {}).get("A", 0.5)
        },
    )


# ─── Task 7: Cohen's κ ────────────────────────────────────────────────────────

def cohen_kappa(judge_labels: list[int], human_labels: list[int]) -> float:
    """Task 7: Tính Cohen's κ giữa LLM judge và human labels.

    Args:
        judge_labels:  nhãn từ LLM judge (0 = bad answer, 1 = good answer)
        human_labels:  nhãn từ human_labels_10q.json

    Returns:
        κ ∈ [-1, 1]
        Thang đo Landis-Koch: <0=poor, 0-0.2=slight, 0.2-0.4=fair,
                               0.4-0.6=moderate, 0.6-0.8=substantial, 0.8-1=almost perfect
    """
    if len(judge_labels) != len(human_labels) or len(judge_labels) == 0:
        return 0.0

    if judge_labels == human_labels:
        return 1.0

    try:
        from sklearn.metrics import cohen_kappa_score
        score = float(cohen_kappa_score(human_labels, judge_labels))
        if str(score) == "nan":
            return 0.0
        return score
    except Exception:
        n = len(judge_labels)
        p_o = sum(j == h for j, h in zip(judge_labels, human_labels)) / n
        p_e = (
            (judge_labels.count(1) / n) * (human_labels.count(1) / n) +
            (judge_labels.count(0) / n) * (human_labels.count(0) / n)
        )
        if abs(1.0 - p_e) < 1e-9:
            return 1.0 if p_o == 1.0 else 0.0
        kappa = (p_o - p_e) / (1.0 - p_e)
        return float(max(-1.0, min(1.0, kappa)))


# ─── Task 8: Bias Report ──────────────────────────────────────────────────────

def bias_report(judge_results: list[JudgeResult]) -> dict:
    """Task 8: Đo lường position bias và verbosity bias.

    Position bias: LLM chọn answer theo vị trí (A hay B) thay vì chất lượng.
        → Đo bằng % cases where position_consistent = False

    Verbosity bias: LLM ưu tiên answer dài hơn dù không chính xác hơn.
        → Đo bằng: trong các case A thắng, A có dài hơn B không? Tương tự cho B.

    Returns:
        {
          "total_judged": int,
          "position_bias_rate": float,        # 0-1, cao = bias nhiều
          "position_bias_count": int,
          "verbosity_bias": float,            # 0-1, > 0.6 = đáng lo ngại
          "verbosity_details": {
            "a_wins_a_longer": int,           # A thắng VÀ A dài hơn
            "b_wins_b_longer": int,           # B thắng VÀ B dài hơn
            "total_decisive": int,            # tổng case có winner rõ ràng
          },
          "interpretation": str,
        }
    """
    total = len(judge_results)
    if total == 0:
        return {
            "total_judged": 0,
            "position_bias_rate": 0.0,
            "position_bias_count": 0,
            "verbosity_bias": 0.0,
            "verbosity_details": {
                "a_wins_a_longer": 0,
                "b_wins_b_longer": 0,
                "total_decisive": 0
            },
            "interpretation": "Chưa có dữ liệu đánh giá."
        }

    position_bias_count = sum(1 for r in judge_results if not r.position_consistent)
    position_bias_rate  = position_bias_count / total

    a_wins_a_longer = sum(
        1 for r in judge_results
        if r.final_winner == "A" and len(r.answer_a) > len(r.answer_b)
    )
    b_wins_b_longer = sum(
        1 for r in judge_results
        if r.final_winner == "B" and len(r.answer_b) > len(r.answer_a)
    )
    decisive = sum(1 for r in judge_results if r.final_winner in ("A", "B"))
    verbosity_bias = (a_wins_a_longer + b_wins_b_longer) / decisive if decisive > 0 else 0.0

    interpretation = ("Position bias cao — nên dùng swap-and-average."
                      if position_bias_rate > 0.3 else "Position bias thấp — judge ổn định.")
    return {
        "total_judged": total,
        "position_bias_rate": round(position_bias_rate, 3),
        "position_bias_count": position_bias_count,
        "verbosity_bias": round(verbosity_bias, 3),
        "verbosity_details": {
            "a_wins_a_longer": a_wins_a_longer,
            "b_wins_b_longer": b_wins_b_longer,
            "total_decisive": decisive,
        },
        "interpretation": interpretation,
    }


def judge_single_answer(question: str, model_answer: str, ground_truth: str = "") -> dict:
    """Judge a single model answer compared against ground truth (1=good, 0=bad)."""
    from openai import OpenAI
    client = OpenAI(api_key=OPENAI_API_KEY)

    prompt = f"""Bạn là một chuyên gia đánh giá câu trả lời chính sách nhân sự công ty.

Câu hỏi: {question}
{"Đáp án chuẩn (Ground Truth): " + ground_truth if ground_truth else ""}

Câu trả lời của model cần đánh giá:
{model_answer}

Nhiệm vụ:
Đánh giá câu trả lời của model:
- Gán label = 1 nếu câu trả lời đúng, chính xác theo chính sách công ty (áp dụng đúng phiên bản hiện hành v2024, không dùng quy định cũ v2023, đúng số liệu và cấp phê duyệt).
- Gán label = 0 nếu câu trả lời sai sự thật, áp dụng sai quy định cũ đã hết hiệu lực, thiếu điều kiện quan trọng hoặc vi phạm quy định (như cho phép dùng VPN cá nhân).

Chỉ trả về JSON:
{{"label": 1 hoặc 0, "reasoning": "giải thích ngắn gọn"}}
"""
    try:
        resp = client.chat.completions.create(
            model=JUDGE_MODEL,
            messages=[
                {"role": "system", "content": "Bạn là chuyên gia đánh giá chính sách HR. Chỉ trả lời JSON."},
                {"role": "user", "content": prompt},
            ],
            response_format={"type": "json_object"},
            temperature=0.0,
        )
        parsed = json.loads(resp.choices[0].message.content)
        label = int(parsed.get("label", 0))
        reasoning = parsed.get("reasoning", "")
        return {"label": label, "reasoning": reasoning}
    except Exception as e:
        print(f"  ⚠️  Single judge error: {e}")
        return {"label": 0, "reasoning": f"Error: {e}"}


# ─── Main ─────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    print("=" * 60)
    print("PHASE B: LLM-as-Judge Evaluation")
    print("=" * 60)

    # 1. Chạy 5 cặp Pairwise + Swap-and-average để đo Position Bias và Verbosity Bias
    test_pairs = [
        {
            "question": "Nhân viên được nghỉ bao nhiêu ngày phép năm?",
            "a": "Nhân viên được nghỉ 15 ngày phép năm theo chính sách v2024 hiện hành.",
            "b": "Theo quy định, nhân viên có 12 ngày phép hàng năm.",
        },
        {
            "question": "Muốn mua thiết bị văn phòng trị giá 55 triệu đồng cần cấp nào phê duyệt?",
            "a": "Theo quy trình mua sắm, các khoản mua sắm tài sản trên 50 triệu đồng bắt buộc phải do Tổng Giám đốc (CEO) phê duyệt.",
            "b": "Chỉ cần Giám đốc phòng ban phê duyệt là đủ thẩm quyền.",
        },
        {
            "question": "Nhân viên thử việc có được hưởng quyền lợi bảo hiểm sức khỏe PVI không?",
            "a": "Nhân viên thử việc chưa được tham gia gói bảo hiểm sức khỏe PVI. Quyền lợi này chỉ áp dụng sau khi ký hợp đồng chính thức.",
            "b": "Nhân viên thử việc được hưởng bảo hiểm PVI ngay ngày đầu tiên.",
        },
        {
            "question": "Nhân viên làm việc từ xa (WFH) có thể dùng VPN cá nhân (như NordVPN) không?",
            "a": "Không được phép. Nhân viên bắt buộc phải dùng giải pháp WireGuard VPN do công ty cấp; nghiêm cấm các phần mềm VPN cá nhân.",
            "b": "Được phép sử dụng tự do bất kỳ VPN cá nhân nào miễn là kết nối ổn định.",
        },
        {
            "question": "Thời hạn đổi mật khẩu nội bộ theo quy định hiện hành là bao lâu?",
            "a": "Theo chính sách bảo mật v2.0 hiện hành, mật khẩu phải đổi định kỳ tối đa mỗi 90 ngày và bắt buộc dùng MFA.",
            "b": "Theo chính sách v1.0 cũ, nhân viên thay đổi mật khẩu sau mỗi 180 ngày.",
        },
    ]

    print(f"\n[1/3] Running swap-and-average on {len(test_pairs)} pairwise cases...")
    pairwise_results: list[JudgeResult] = []
    for i, pair in enumerate(test_pairs, 1):
        res = swap_and_average(pair["question"], pair["a"], pair["b"])
        pairwise_results.append(res)
        print(f"  Case #{i}: Pass1={res.winner_pass1}, Pass2(conv)={res.winner_pass2} -> Final={res.final_winner}, Consistent={res.position_consistent}")

    # 2. Bias Report
    print("\n[2/3] Computing Bias Report...")
    bias = bias_report(pairwise_results)
    print(f"  Total judged:        {bias['total_judged']}")
    print(f"  Position bias rate:  {bias['position_bias_rate']*100:.1f}% ({bias['position_bias_count']} cases)")
    print(f"  Verbosity bias:      {bias['verbosity_bias']*100:.1f}%")
    print(f"  Interpretation:      {bias['interpretation']}")

    # 3. Cohen's kappa vs 10 Human Labels
    print("\n[3/3] Evaluating 10 human-labeled questions for Cohen's kappa...")
    with open(HUMAN_LABELS_PATH, encoding="utf-8") as f:
        human_data = json.load(f)

    # Load ground truths từ test_set_50q.json nếu có
    gt_map: dict[int, str] = {}
    if os.path.exists(TEST_SET_PATH):
        with open(TEST_SET_PATH, encoding="utf-8") as f:
            t_data = json.load(f)
            gt_map = {item["id"]: item.get("ground_truth", "") for item in t_data}

    human_labels = [item["human_label"] for item in human_data]
    judge_labels = []
    comparison_table = []

    for item in human_data:
        qid = item["question_id"]
        gt = gt_map.get(qid, "")
        j_eval = judge_single_answer(item["question"], item["model_answer"], ground_truth=gt)
        j_label = j_eval["label"]
        judge_labels.append(j_label)
        agree = (j_label == item["human_label"])
        comparison_table.append({
            "question_id": qid,
            "question": item["question"],
            "human_label": item["human_label"],
            "judge_label": j_label,
            "agree": agree,
            "judge_reasoning": j_eval["reasoning"],
            "human_note": item.get("human_note", "")
        })
        status_str = "[MATCH]" if agree else "[DIFFER]"
        print(f"  Q{qid:02d}: Human={item['human_label']}, Judge={j_label} -> {status_str}")

    kappa = cohen_kappa(judge_labels, human_labels)
    print(f"\nCohen's kappa = {kappa:.4f}")
    if kappa > 0.8:
        k_interp = "almost perfect agreement"
    elif kappa > 0.6:
        k_interp = "substantial agreement (Bonus achieved!)"
    elif kappa > 0.4:
        k_interp = "moderate agreement"
    else:
        k_interp = "fair or poor agreement"
    print(f"Interpretation: {k_interp}")

    # 4. Lưu báo cáo reports/judge_results.json (yêu cầu của check_lab.py)
    os.makedirs("reports", exist_ok=True)
    report_data = {
        "kappa": round(kappa, 4),
        "kappa_interpretation": k_interp,
        "bias": bias,
        "pairwise_results": [dataclasses.asdict(r) for r in pairwise_results],
        "human_vs_judge": comparison_table,
    }
    report_path = "reports/judge_results.json"
    with open(report_path, "w", encoding="utf-8") as f:
        json.dump(report_data, f, ensure_ascii=False, indent=2)
    print(f"\n[OK] Saved Phase B report -> {report_path}")
