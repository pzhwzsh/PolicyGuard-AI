from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class AbstentionPoint:
    threshold: float
    precision: float
    recall: float
    f1: float
    false_answer_rate: float


def calibrate_abstention(rows: list[dict], thresholds: list[float]) -> dict:
    points = []
    positives = sum(row["answerable"] for row in rows)
    negatives = len(rows) - positives
    for threshold in thresholds:
        tp = fp = 0
        for row in rows:
            answered = row["top_score"] >= threshold and row.get("expected_in_top_k", False)
            if answered and row["answerable"]:
                tp += 1
            elif row["top_score"] >= threshold and not row["answerable"]:
                fp += 1
        precision = tp / (tp + fp) if tp + fp else 1.0
        recall = tp / positives if positives else 0.0
        f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
        points.append(AbstentionPoint(
            threshold=threshold,
            precision=round(precision, 6), recall=round(recall, 6),
            f1=round(f1, 6),
            false_answer_rate=round(fp / negatives, 6) if negatives else 0.0,
        ))
    best = max(points, key=lambda item: (item.f1, -item.false_answer_rate, item.threshold))
    return {"best": best, "points": points, "development_only": True}
