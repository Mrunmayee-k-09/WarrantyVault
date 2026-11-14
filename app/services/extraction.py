"""Document text extraction and transparent invoice-field parsing.

Scanned PDFs are rendered page-by-page with PyMuPDF, then OCR'd with Tesseract.
Tesseract itself is a separate system application; pytesseract is only its wrapper.
"""
from pathlib import Path
import os
import re
from datetime import datetime

FIELD_PATTERNS = {
    "brand": [r"(?:brand|manufacturer)\s*[:#-]\s*([^\n,;]+)"],
    "retailer": [r"(?:retailer|seller|sold\s+by|store\s+name)\s*[:#-]\s*([^\n,;]+)"],
    "model_number": [r"(?:model(?:\s*(?:no|number))?)\s*[:#-]\s*([A-Z0-9][A-Z0-9._/-]{2,})"],
    "serial_number": [r"(?:serial(?:\s*(?:no|number))?|s\s*/\s*n)\s*[:#-]\s*([A-Z0-9][A-Z0-9._/-]{3,})"],
    "purchase_date": [r"(?:purchase\s*date|invoice\s*date|date)\s*[:#-]\s*(\d{1,2}[/-]\d{1,2}[/-]\d{2,4}|\d{4}-\d{2}-\d{2})"],
    "invoice_number": [r"(?:invoice\s*(?:no|number|#))\s*[:#-]?\s*([A-Z0-9/-]{3,})"],
    "purchase_price": [r"(?:total\s+amount|grand\s+total|purchase\s+price|amount\s+paid|total)\s*[:#-]?\s*(?:INR|RS\.?|₹)?\s*([0-9,]+(?:\.\d{1,2})?)"],
}
PDF_OCR_MAX_PAGES = 10


def _normalize_date(value):
    for fmt in ("%d/%m/%Y", "%d-%m-%Y", "%m/%d/%Y", "%m-%d-%Y", "%Y-%m-%d", "%d/%m/%y", "%d-%m-%y"):
        try:
            return datetime.strptime(value.strip(), fmt).date().isoformat()
        except ValueError:
            continue
    return value.strip()


def _configure_tesseract(pytesseract):
    configured_path = os.getenv("TESSERACT_CMD", "").strip()
    if configured_path:
        pytesseract.pytesseract.tesseract_cmd = configured_path


def _ocr_image(image):
    """OCR a PIL image and return text, quality notes, and mean word confidence."""
    from PIL import ImageFilter, ImageOps, ImageStat
    import pytesseract

    _configure_tesseract(pytesseract)
    image = ImageOps.exif_transpose(image).convert("RGB")
    notes = []
    if image.width < 700 or image.height < 500:
        notes.append("Image resolution is relatively low; upload a larger, clearer scan where possible.")
    edge_variance = ImageStat.Stat(image.convert("L").filter(ImageFilter.FIND_EDGES)).var[0]
    if edge_variance < 18:
        notes.append("The image may be blurry or low-contrast. Check the extracted values carefully.")

    data = pytesseract.image_to_data(image, output_type=pytesseract.Output.DICT)
    scores = []
    for item in data.get("conf", []):
        try:
            score = float(item)
            if score >= 0:
                scores.append(score)
        except (TypeError, ValueError):
            continue
    confidence = sum(scores) / len(scores) if scores else 0.0
    return pytesseract.image_to_string(image), notes, confidence



def _ocr_pdf_pages(path: Path, text_pages: list[str]) -> tuple[str, list[str], float | None]:
    """OCR scanned and image-containing PDF pages while preserving extracted text."""
    try:
        import fitz  # PyMuPDF
        from PIL import Image
        import pytesseract
    except ImportError as exc:
        missing = "PyMuPDF" if "fitz" in str(exc) else "Pillow/pytesseract"
        return "\n\n".join(text_pages), [
            f"PDF image/scanned-page OCR dependencies are missing. Install {missing} from requirements.txt and install the Tesseract OCR application."
        ], None

    _configure_tesseract(pytesseract)

    notes = []
    confidence_scores = []
    parts = list(text_pages)

    def merge_page_text(existing: str, recognized: str) -> str:
        """Append OCR lines without repeating exact existing lines."""
        existing_lines = existing.splitlines()
        seen = {
            " ".join(line.split()).casefold()
            for line in existing_lines
            if line.strip()
        }

        new_lines = []
        for line in recognized.splitlines():
            normalized = " ".join(line.split()).casefold()

            if normalized and normalized not in seen:
                new_lines.append(line)
                seen.add(normalized)

        return "\n".join(existing_lines + new_lines).strip()

    try:
        with fitz.open(str(path)) as pdf:
            if len(parts) < len(pdf):
                parts.extend([""] * (len(pdf) - len(parts)))

            # OCR pages that are scanned OR contain embedded images.
            ocr_indices = []

            for page_index in range(len(pdf)):
                page = pdf.load_page(page_index)

                has_no_text = not parts[page_index].strip()
                has_images = bool(page.get_images(full=True))

                if has_no_text or has_images:
                    ocr_indices.append(page_index)

            if len(ocr_indices) > PDF_OCR_MAX_PAGES:
                notes.append(
                    f"The PDF has {len(ocr_indices)} pages requiring OCR. "
                    f"Only the first {PDF_OCR_MAX_PAGES} pages were OCR-processed in this pass."
                )
                ocr_indices = ocr_indices[:PDF_OCR_MAX_PAGES]

            for page_index in ocr_indices:
                page = pdf.load_page(page_index)

                pixmap = page.get_pixmap(
                    matrix=fitz.Matrix(2.2, 2.2),
                    alpha=False
                )

                image = Image.frombytes(
                    "RGB",
                    [pixmap.width, pixmap.height],
                    pixmap.samples
                )

                page_text, page_notes, score = _ocr_image(image)

                if page_text.strip():
                    parts[page_index] = merge_page_text(
                        parts[page_index],
                        page_text
                    )

                notes.extend(
                    f"Page {page_index + 1}: {item}"
                    for item in page_notes
                )

                if score is not None:
                    confidence_scores.append(score)

        combined = "\n\n".join(
            part for part in parts if part.strip()
        )

        if not combined.strip():
            notes.append(
                "No readable text was found in the scanned PDF. "
                "Try a sharper, correctly oriented scan."
            )

        average_score = (
            sum(confidence_scores) / len(confidence_scores)
            if confidence_scores else None
        )

        return combined, notes, average_score

    except Exception as exc:
        message = str(exc)

        if "tesseract" in message.lower() or "not found" in message.lower():
            notes.append(
                "Tesseract OCR could not be started. Install the Tesseract "
                "application and set TESSERACT_CMD or add it to PATH."
            )
        else:
            notes.append(f"Scanned-PDF OCR failed: {exc}")

        return "\n\n".join(
            part for part in parts if part.strip()
        ), notes, None



def extract_text(path: str, extension: str) -> tuple[str, list[str], float | None]:
    path = Path(path)
    notes = []
    ext = extension.lower().lstrip(".")
    if ext == "txt":
        return path.read_text(encoding="utf-8", errors="replace"), notes, None
    
    if ext == "pdf":
        try:
            from pypdf import PdfReader

            reader = PdfReader(str(path))
            text_pages = [(page.extract_text() or "") for page in reader.pages]

            # Also inspect pages containing embedded images.
            has_images = any(
                len(getattr(page, "images", [])) > 0
                for page in reader.pages
            )

            if has_images or any(not page.strip() for page in text_pages):
                text, ocr_notes, score = _ocr_pdf_pages(path, text_pages)
                notes.extend(ocr_notes)

                if score is not None:
                    notes.append(
                        "Scanned PDF pages were OCR-processed. OCR confidence "
                        "describes text-recognition certainty, not document authenticity."
                    )

                return text, notes, score

            return "\n\n".join(text_pages), notes, None

        except Exception as exc:
            # If pypdf cannot parse the file, attempt a raster/OCR pass.
            text, ocr_notes, score = _ocr_pdf_pages(path, [])
            notes.extend(ocr_notes)

            if not text.strip():
                notes.append(f"PDF text extraction failed: {exc}")

            return text, notes, score

    if ext == "docx":
        try:
            from docx import Document as DocxDocument
            doc = DocxDocument(str(path))
            return "\n".join(p.text for p in doc.paragraphs), notes, None
        except Exception as exc:
            return "", [f"DOCX extraction failed: {exc}"], None
    if ext in {"png", "jpg", "jpeg", "tif", "tiff"}:
        try:
            from PIL import Image
            with Image.open(path) as source_image:
                text, image_notes, score = _ocr_image(source_image)
            return text, image_notes, score
        except ImportError:
            return "", ["Image OCR dependencies are missing. Install Pillow and pytesseract, then install the Tesseract OCR application."], None
        except Exception as exc:
            message = str(exc)
            if "tesseract" in message.lower() or "not found" in message.lower():
                return "", ["Tesseract OCR could not be started. Install the Tesseract application and set TESSERACT_CMD or add it to PATH."], None
            return "", [f"Image OCR could not process this file: {exc}. Confirm that Tesseract OCR is installed and available."], None
    return "", ["Unsupported document type."], None


def parse_invoice_fields(text: str, ocr_score: float | None = None) -> tuple[dict, dict, str]:
    extracted, confidences = {}, {}
    if not text.strip():
        return extracted, confidences, "LOW"
    for field, patterns in FIELD_PATTERNS.items():
        for pattern in patterns:
            match = re.search(pattern, text, flags=re.IGNORECASE)
            if match:
                value = match.group(1).strip().strip(" .,:;")
                if field == "purchase_date":
                    value = _normalize_date(value)
                elif field == "purchase_price":
                    try:
                        value = str(float(value.replace(",", "")))
                    except ValueError:
                        pass
                extracted[field] = value
                if ocr_score is None:
                    confidences[field] = "HIGH"
                elif ocr_score >= 80:
                    confidences[field] = "HIGH"
                elif ocr_score >= 50:
                    confidences[field] = "MEDIUM"
                else:
                    confidences[field] = "LOW"
                break
    if not extracted:
        return extracted, confidences, "LOW"
    rank = {"LOW": 1, "MEDIUM": 2, "HIGH": 3}
    mean_confidence = sum(rank[value] for value in confidences.values()) / len(confidences)
    overall = "HIGH" if mean_confidence >= 2.6 else "MEDIUM" if mean_confidence >= 1.7 else "LOW"
    return extracted, confidences, overall


def process_document(path: str, extension: str) -> tuple[dict, dict, str, str, list[str]]:
    text, notes, ocr_score = extract_text(path, extension)
    data, confidences, overall = parse_invoice_fields(text, ocr_score)
    if not text.strip() and not notes:
        notes.append("The document did not contain readable text.")
    if text.strip() and not data:
        notes.append("Text was extracted, but no supported invoice fields were recognized. Add clear field labels or review the document manually.")
    return data, confidences, overall, text[:12000], notes
