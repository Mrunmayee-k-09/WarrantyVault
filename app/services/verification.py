"""Explainable field-level verification. OCR confidence is not authenticity."""
from datetime import date
from difflib import SequenceMatcher

FIELD_LABELS = {
    "brand": "Brand",
    "retailer": "Retailer",
    "model_number": "Model number",
    "serial_number": "Serial number",
    "purchase_date": "Purchase date",
    "invoice_number": "Invoice number",
    "purchase_price": "Purchase price",
}
PRODUCT_ATTRS = {
    "brand": "brand", "retailer": "retailer", "model_number": "model_number",
    "serial_number": "serial_number", "purchase_date": "purchase_date",
    "invoice_number": "invoice_number", "purchase_price": "purchase_price",
}


def _string(value):
    if value is None:
        return ""
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, float):
        return f"{value:.2f}".rstrip("0").rstrip(".")
    return str(value).strip()


def _normalized(field, value):
    value = _string(value).lower().strip()
    if field in {"brand", "retailer"}:
        return " ".join(value.replace(".", " ").replace(",", " ").split())
    if field == "purchase_price":
        try:
            return "{:.2f}".format(float(value.replace(",", "")))
        except ValueError:
            return value
    return "".join(ch for ch in value if ch.isalnum()) if field in {"serial_number", "model_number", "invoice_number"} else value


def _compare(field, stored, extracted):
    left, right = _normalized(field, stored), _normalized(field, extracted)
    if not left or not right:
        return "Skipped", "A stored or extracted value is unavailable, so this field cannot be compared."
    if left == right:
        return "Match", "The extracted value agrees with the saved product record."
    if field in {"brand", "retailer"} and SequenceMatcher(None, left, right).ratio() >= 0.82:
        return "Partial", "The values are similar but not identical. Review the source document and saved record."
    return "Mismatch", "The value extracted from the document differs from the value saved for this product."


def build_verification(product, extracted_data, field_confidences, manual_corrections=None):
    fields = []
    manual_corrections = manual_corrections or {}
    confidence_rank = {"LOW": 1, "MEDIUM": 2, "HIGH": 3}
    scores = []
    for key, label in FIELD_LABELS.items():
        stored = getattr(product, PRODUCT_ATTRS[key], None)
        extracted = extracted_data.get(key)
        confidence = field_confidences.get(key, "LOW" if extracted else "LOW")
        result, reason = _compare(key, stored, extracted)
        source = "Manual correction" if key in manual_corrections else "OCR"
        review = confidence == "LOW" and bool(extracted) and source == "OCR"
        if confidence in confidence_rank and extracted:
            scores.append(confidence_rank[confidence])
        fields.append({
            "field_name": label,
            "key": key,
            "stored_value": _string(stored) or None,
            "extracted_value": _string(extracted) or None,
            "result": result,
            "confidence": confidence,
            "review_required": review,
            "value_source": source,
            "reason": reason,
        })

    comparable = [f for f in fields if f["result"] != "Skipped"]
    mismatches = [f for f in comparable if f["result"] == "Mismatch"]
    partials = [f for f in comparable if f["result"] == "Partial"]
    reviews = [f for f in fields if f["review_required"]]
    notes = []

    # Cross-field consistency checks: useful warnings, not fraud conclusions.
    purchase = product.purchase_date
    expiry = product.warranty_expiry
    if purchase and expiry and expiry < purchase:
        notes.append("The saved warranty expiry date is earlier than the saved purchase date. Check the product record.")
    extracted_purchase = extracted_data.get("purchase_date")
    if extracted_purchase and expiry:
        try:
            if date.fromisoformat(extracted_purchase) > expiry:
                notes.append("The extracted purchase date is later than the saved warranty expiry date. Review the dates.")
        except ValueError:
            notes.append("The extracted purchase date could not be checked against the warranty dates.")

    if not comparable:
        overall = "UNABLE_TO_VERIFY"
        summary = "There were not enough comparable fields to produce a verification result. Review the extracted information or upload a clearer document."
    elif mismatches:
        overall = "MISMATCH"
        summary = f"{len(mismatches)} field(s) differ from the saved product record. Review the discrepancies before relying on this document."
    elif partials or reviews or notes:
        overall = "REVIEW_REQUIRED"
        summary = "The available values are not all conclusive. Review the flagged fields and consistency notes."
    else:
        overall = "VERIFIED"
        summary = "All comparable extracted fields match the saved product record. This checks consistency only; it does not prove that the document is authentic."

    average = sum(scores) / len(scores) if scores else 1
    overall_confidence = "HIGH" if average >= 2.6 else "MEDIUM" if average >= 1.7 else "LOW"
    if reviews:
        notes.append(f"{len(reviews)} field(s) have LOW extraction confidence and need human review. Confidence describes OCR certainty, not truth or authenticity.")
    return overall, overall_confidence, summary, fields, notes
