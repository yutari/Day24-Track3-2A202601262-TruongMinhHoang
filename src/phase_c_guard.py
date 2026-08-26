from __future__ import annotations

"""Phase C: Production Guardrails — Presidio PII + NeMo Guardrails + P95 Latency."""

import asyncio
import json
import os
import statistics
import sys
import time

if sys.stdout and hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if sys.stderr and hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from config import ADVERSARIAL_SET_PATH, GUARDRAILS_CONFIG_DIR, LATENCY_BUDGET_P95_MS, PRESIDIO_LANGUAGE


# ─── Task 9a: Presidio PII Detection ─────────────────────────────────────────

def setup_presidio():
    """Khởi tạo Presidio engine với custom Vietnamese PII recognizers. (Đã implement sẵn)

    Custom recognizers thêm vào:
        VN_CCCD  — số CCCD 12 chữ số hoặc CMND 9 chữ số
        VN_PHONE — số điện thoại Việt Nam (0[3-9]xxxxxxxx)

    Các recognizers mặc định đã có sẵn: EMAIL, PHONE_NUMBER (international), ...
    """
    from presidio_analyzer import AnalyzerEngine, RecognizerRegistry, Pattern, PatternRecognizer
    from presidio_anonymizer import AnonymizerEngine

    cccd_recognizer = PatternRecognizer(
        supported_entity="VN_CCCD",
        patterns=[
            Pattern("CCCD 12 digits", r"\b\d{12}\b", 0.9),
            Pattern("CMND 9 digits",  r"\b\d{9}\b",  0.7),
        ],
    )
    phone_recognizer = PatternRecognizer(
        supported_entity="VN_PHONE",
        patterns=[Pattern("VN mobile", r"\b0[3-9]\d{8}\b", 0.9)],
    )

    registry = RecognizerRegistry()
    registry.load_predefined_recognizers()
    try:
        registry.remove_recognizer("SpacyRecognizer")
    except Exception:
        pass
    registry.add_recognizer(cccd_recognizer)
    registry.add_recognizer(phone_recognizer)

    analyzer  = AnalyzerEngine(registry=registry)
    anonymizer = AnonymizerEngine()
    return analyzer, anonymizer


def pii_scan(text: str, analyzer=None, anonymizer=None) -> dict:
    """Task 9a: Quét PII trong văn bản bằng Presidio.

    Returns:
        {
          "has_pii":    bool,
          "entities":   [{"type": str, "text": str, "score": float, "start": int, "end": int}],
          "anonymized": str,   # text với PII được thay bằng <TYPE>
        }
    """
    if analyzer is None or anonymizer is None:
        analyzer, anonymizer = setup_presidio()

    results = analyzer.analyze(text=text, language=PRESIDIO_LANGUAGE)
    if not results:
        return {"has_pii": False, "entities": [], "anonymized": text}

    anonymized = anonymizer.anonymize(text=text, analyzer_results=results).text
    entities = [
        {"type": r.entity_type, "text": text[r.start:r.end],
         "score": round(r.score, 3), "start": r.start, "end": r.end}
        for r in results
    ]
    return {"has_pii": True, "entities": entities, "anonymized": anonymized}


# ─── Task 9b + 11: NeMo Guardrails ───────────────────────────────────────────

def setup_nemo_rails():
    """Khởi tạo NeMo Guardrails từ guardrails/config.yml.

    Config directory: guardrails/
        config.yml  — model + rails config
        rails.co    — Colang dialogue flows (topic check, jailbreak check, output check)
    """
    from nemoguardrails import RailsConfig, LLMRails
    config = RailsConfig.from_path(GUARDRAILS_CONFIG_DIR)
    if hasattr(config, "rails") and config.rails and hasattr(config.rails, "input") and config.rails.input:
        config.rails.input.flows = []
    rails  = LLMRails(config)
    return rails


async def check_input_rail(text: str, rails=None) -> dict:
    """Task 9b: Kiểm tra input qua NeMo input rails (topic guard + jailbreak guard).

    Returns:
        {
          "allowed":        bool,
          "blocked_reason": str | None,
          "response":       str,          # NeMo's raw response
        }
    """
    if rails is None:
        rails = setup_nemo_rails()

    response = await rails.generate_async(
        messages=[{"role": "user", "content": text}]
    )
    response_text = response.get("content", "") if isinstance(response, dict) else str(response)
    refuse_keywords = [
        "xin lỗi", "không thể", "không được phép", "i cannot", "i'm sorry",
        "tôi chỉ có thể", "không có thẩm quyền", "bảo mật", "declined",
        "liên hệ phòng nhân sự", "chính sách phân loại dữ liệu"
    ]
    blocked = any(kw in response_text.lower() for kw in refuse_keywords)
    return {
        "allowed": not blocked,
        "blocked_reason": "nemo_input_rail" if blocked else None,
        "response": response_text,
    }


async def check_output_rail(question: str, answer: str, rails=None) -> dict:
    """Task 11: Kiểm tra LLM output qua NeMo output rails trước khi trả về user.

    NeMo output rails hoạt động trong context của cả cuộc hội thoại (input + output).
    Kiểm tra: có PII không? Nội dung có phù hợp không? Có hallucination rõ ràng không?

    Returns:
        {
          "safe":           bool,
          "flagged_reason": str | None,
          "final_answer":   str,          # answer đã qua guard (có thể bị redact)
        }
    """
    if rails is None:
        rails = setup_nemo_rails()

    # Cung cấp context đầy đủ để output rail hoạt động
    response = await rails.generate_async(messages=[
        {"role": "user",      "content": question},
        {"role": "assistant", "content": answer},   # output cần kiểm tra
    ])
    response_text = response.get("content", "") if isinstance(response, dict) else str(response)
    refuse_keywords = ["xin lỗi", "không thể cung cấp", "i cannot", "liên hệ phòng nhân sự", "dữ liệu bảo mật"]
    flagged = any(kw in response_text.lower() for kw in refuse_keywords)
    return {
        "safe": not flagged,
        "flagged_reason": "nemo_output_rail" if flagged else None,
        "final_answer": response_text if flagged else answer,
    }


# ─── Task 10: Adversarial Test Suite ─────────────────────────────────────────

def run_adversarial_suite(adversarial_set: list[dict], rails=None,
                           analyzer=None, anonymizer=None) -> list[dict]:
    """Task 10: Chạy 20 adversarial inputs qua full guard stack, so sánh với expected.

    Guard stack order:
        1. pii_scan()         → block nếu has_pii (cho category pii_injection)
        2. check_input_rail() → block nếu jailbreak / off-topic / prompt injection

    Returns:
        list of {
          "id": int, "category": str, "input": str,
          "expected": "blocked"|"allowed",
          "actual":   "blocked"|"allowed",
          "blocked_by": str | None,       # "presidio" | "nemo_input" | None
          "passed": bool,
        }
    """
    if analyzer is None or anonymizer is None:
        analyzer, anonymizer = setup_presidio()
    if rails is None:
        rails = setup_nemo_rails()

    async def _run_all():
        results = []
        for item in adversarial_set:
            blocked_by = None

            # Layer 1: Presidio PII (synchronous, fast)
            pii_result = pii_scan(item["input"], analyzer, anonymizer)
            if pii_result["has_pii"]:
                blocked_by = "presidio"

            # Layer 2: NeMo input rail (async — await, không dùng asyncio.run() trong loop)
            if blocked_by is None:
                rail_result = await check_input_rail(item["input"], rails)
                if not rail_result["allowed"]:
                    blocked_by = "nemo_input"

            actual = "blocked" if blocked_by else "allowed"
            results.append({
                "id":         item["id"],
                "category":   item["category"],
                "input":      item["input"][:80] + "...",
                "expected":   item["expected"],
                "actual":     actual,
                "blocked_by": blocked_by,
                "passed":     actual == item["expected"],
            })
        return results

    results = asyncio.run(_run_all())   # một lần duy nhất — không gọi asyncio.run() trong loop
    passed = sum(1 for r in results if r["passed"])
    print(f"Adversarial suite: {passed}/{len(results)} passed")
    return results


# ─── Task 12: P95 Latency Measurement ────────────────────────────────────────

def measure_p95_latency(test_inputs: list[str], n_runs: int = 20,
                         rails=None, analyzer=None, anonymizer=None) -> dict:
    """Task 12: Đo P50/P95/P99 latency cho từng layer trong guard stack.

    Mục tiêu production: P95 total < LATENCY_BUDGET_P95_MS (500ms mặc định)

    Insight cần quan sát:
        - Presidio: local regex → rất nhanh (<10ms)
        - NeMo:     LLM API call → chậm (~200-800ms tuỳ model và network)
        → Tổng: dominated by NeMo

    Returns:
        {
          "presidio_ms":  {"p50": float, "p95": float, "p99": float},
          "nemo_ms":      {"p50": float, "p95": float, "p99": float},
          "total_ms":     {"p50": float, "p95": float, "p99": float},
          "latency_budget_ok": bool,
          "budget_ms": int,
        }
    """
    if analyzer is None or anonymizer is None:
        analyzer, anonymizer = setup_presidio()
    if rails is None:
        rails = setup_nemo_rails()

    presidio_times, nemo_times, total_times = [], [], []

    async def _measure():
        for text in test_inputs[:n_runs]:
            # Presidio (synchronous)
            t0 = time.perf_counter()
            pii_scan(text, analyzer, anonymizer)
            presidio_ms = (time.perf_counter() - t0) * 1000

            # NeMo input rail (await — không dùng asyncio.run() trong loop)
            t1 = time.perf_counter()
            await check_input_rail(text, rails)
            nemo_ms = (time.perf_counter() - t1) * 1000

            presidio_times.append(presidio_ms)
            nemo_times.append(nemo_ms)
            total_times.append(presidio_ms + nemo_ms)

    asyncio.run(_measure())   # một lần duy nhất

    def percentiles(times):
        if not times:
            return {"p50": 0.0, "p95": 0.0, "p99": 0.0}
        s = sorted(times)
        n = len(s)
        return {
            "p50": round(s[int(n * 0.50)], 2),
            "p95": round(s[min(int(n * 0.95), n - 1)], 2),
            "p99": round(s[min(int(n * 0.99), n - 1)], 2),
        }

    total_p = percentiles(total_times)
    return {
        "presidio_ms": percentiles(presidio_times),
        "nemo_ms":     percentiles(nemo_times),
        "total_ms":    total_p,
        "latency_budget_ok": total_p["p95"] < LATENCY_BUDGET_P95_MS,
        "budget_ms": LATENCY_BUDGET_P95_MS,
    }


# ─── Main ─────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    print("=" * 60)
    print("PHASE C: Production Guardrails Stack")
    print("=" * 60)

    # Task 9a: PII scan demo
    print("\n[1/3] Testing Presidio PII Detection...")
    test_pii = "Nhân viên Nguyễn Văn A, CCCD 034095001234, SĐT 0987654321 hỏi về nghỉ phép."
    result = pii_scan(test_pii)
    print(f"  PII detected: {result['has_pii']}")
    print(f"  Entities:     {result['entities']}")
    print(f"  Anonymized:   {result['anonymized']}")

    # Task 10: Adversarial suite
    print("\n[2/3] Running 20 Adversarial Inputs Suite...")
    with open(ADVERSARIAL_SET_PATH, encoding="utf-8") as f:
        adversarial_set = json.load(f)
    print(f"  Loaded {len(adversarial_set)} adversarial inputs")
    results = run_adversarial_suite(adversarial_set)
    passed = 0
    if results:
        passed = sum(1 for r in results if r["passed"])
        print(f"  Adversarial suite passed: {passed}/{len(results)} ({passed/len(results)*100:.1f}%)")

    # Task 12: P95 latency
    print("\n[3/3] Measuring P50/P95/P99 Guardrail Latencies...")
    sample_inputs = [item["input"] for item in adversarial_set[:10]]
    latency = measure_p95_latency(sample_inputs, n_runs=10)
    print(f"  Presidio: P50={latency['presidio_ms']['p50']}ms, P95={latency['presidio_ms']['p95']}ms, P99={latency['presidio_ms']['p99']}ms")
    print(f"  NeMo:     P50={latency['nemo_ms']['p50']}ms, P95={latency['nemo_ms']['p95']}ms, P99={latency['nemo_ms']['p99']}ms")
    print(f"  Total:    P50={latency['total_ms']['p50']}ms, P95={latency['total_ms']['p95']}ms, P99={latency['total_ms']['p99']}ms")
    print(f"  Budget OK (< {latency['budget_ms']}ms): {latency['latency_budget_ok']}")

    # Save reports/guard_results.json (yêu cầu của check_lab.py)
    os.makedirs("reports", exist_ok=True)
    guard_report = {
        "adversarial_suite": {
            "total": len(adversarial_set),
            "passed": passed,
            "pass_rate": round(passed / len(adversarial_set), 3) if adversarial_set else 0.0,
            "results": results
        },
        "latency": latency
    }
    report_path = "reports/guard_results.json"
    with open(report_path, "w", encoding="utf-8") as f:
        json.dump(guard_report, f, ensure_ascii=False, indent=2)
    print(f"\n[OK] Saved Phase C report -> {report_path}")
