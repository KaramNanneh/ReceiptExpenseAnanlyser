import os
import platform
import re

import cv2
import numpy as np
import pandas as pd
import pytesseract
from PIL import Image, ImageOps

if platform.system() == "Windows":
    _win_path = r"C:\Program Files\Tesseract-OCR\tesseract.exe"
    if os.path.exists(_win_path):
        pytesseract.pytesseract.tesseract_cmd = _win_path


def load_image(file):
    img = Image.open(file)
    img = ImageOps.exif_transpose(img)
    return img.convert("RGB")


def _to_gray(image, target_width=1800):
    gray = cv2.cvtColor(np.array(image.convert("RGB")), cv2.COLOR_RGB2GRAY)
    h, w = gray.shape
    if w < target_width:
        gray = cv2.resize(gray, None, fx=target_width / w, fy=target_width / w,
                          interpolation=cv2.INTER_CUBIC)
    elif w > 2.5 * target_width:
        gray = cv2.resize(gray, None, fx=2 * target_width / w, fy=2 * target_width / w,
                          interpolation=cv2.INTER_AREA)
    return gray


def _remove_long_lines(binary):
    inverted = cv2.bitwise_not(binary)
    h, w = binary.shape
    horiz = cv2.morphologyEx(inverted, cv2.MORPH_OPEN,
                             cv2.getStructuringElement(cv2.MORPH_RECT, (w // 3, 1)))
    vert = cv2.morphologyEx(inverted, cv2.MORPH_OPEN,
                            cv2.getStructuringElement(cv2.MORPH_RECT, (1, h // 3)))
    lines = cv2.dilate(cv2.bitwise_or(horiz, vert), np.ones((5, 5), np.uint8))
    cleaned = inverted.copy()
    cleaned[lines > 0] = 0
    return cv2.bitwise_not(cleaned)


def _deskew(binary, max_angle=8.0):
    small = cv2.resize(cv2.bitwise_not(binary), None, fx=0.4, fy=0.4, interpolation=cv2.INTER_AREA)
    h, w = small.shape
    best_angle, best_score = 0.0, -1.0
    for angle in np.arange(-max_angle, max_angle + 0.01, 0.5):
        M = cv2.getRotationMatrix2D((w / 2, h / 2), angle, 1.0)
        rotated = cv2.warpAffine(small, M, (w, h), flags=cv2.INTER_NEAREST)
        score = np.var(rotated.sum(axis=1).astype(np.float64))
        if score > best_score:
            best_angle, best_score = angle, score
    H, W = binary.shape
    M = cv2.getRotationMatrix2D((W / 2, H / 2), best_angle, 1.0)
    return cv2.warpAffine(binary, M, (W, H), flags=cv2.INTER_CUBIC,
                          borderMode=cv2.BORDER_CONSTANT, borderValue=255)


def preprocess_image(image):
    gray = _to_gray(image)
    background = cv2.medianBlur(cv2.dilate(gray, np.ones((7, 7), np.uint8)), 21)
    normalised = cv2.divide(gray, background, scale=255)
    _, binary = cv2.threshold(normalised, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    binary = _remove_long_lines(binary)
    binary = _deskew(binary)
    return cv2.copyMakeBorder(binary, 40, 40, 40, 40, cv2.BORDER_CONSTANT, value=255)


END_PRICE_RE = re.compile(r"[£$€]?\s*(\d{1,4}[.,]\d{2})\s*[A-Za-z]?[\s|:;!'\"\])_~.\-]*$")
ANY_PRICE_RE = re.compile(r"(?<![\d.,])(\d{1,4}[.,]\d{2})(?![\d])")

SKIP_RE = re.compile(
    r"\b(subtotal|sub total|total|vat|tax|change|tendered|cash|card|visa|mastercard|"
    r"amex|contactless|balance|due|discount|saving|savings|receipt|invoice|"
    r"order no|order number|tel|telephone|phone|www|http|address|thank|"
    r"auth|terminal|merchant|approved|points|loyalty|items?)\b",
    re.IGNORECASE,
)
DATE_RE = re.compile(r"\b\d{1,2}[/.-]\d{1,2}[/.-]\d{2,4}\b|\b\d{1,2}:\d{2}\b")
QTY_DETAIL_RE = re.compile(r"@|/\s*kg|\b\d+\s*x\s*[£$€]?\d+[.,]\d{2}", re.IGNORECASE)
TOTAL_RE = re.compile(r"\b(total|amount due|balance due|to pay)\b", re.IGNORECASE)
SUBTOTAL_RE = re.compile(r"sub\s*total", re.IGNORECASE)

CATEGORIES = {
    "Groceries": ["milk", "bread", "apple", "banana", "egg", "cheese", "butter", "chicken",
                 "beef", "rice", "pasta", "tomato", "potato", "onion", "veg", "fruit",
                 "yogurt", "yoghurt", "cereal", "jelly", "confect", "prepack", "belly",
                 "water", "juice", "crisps", "chocolate", "biscuit", "supermarket", "market"],
    "Dining Out": ["restaurant", "cafe", "coffee", "latte", "cappuccino", "burger", "pizza",
                   "lunch", "dinner", "meal", "sandwich", "wrap", "kebab", "tea"],
    "Utilities": ["electric", "gas", "bill", "power", "internet", "broadband", "energy"],
    "Services": ["client work", "service", "hour", "consulting", "repair", "labour", "delivery"],
    "Shopping": ["product", "macbook", "laptop", "shirt", "shoes", "mall", "clothes",
                 "amazon", "cable", "charger", "book", "pen", "notebook"],
}


def categorise(description):
    text = description.lower()
    best_cat, best_score = "Miscellaneous", 0
    for cat, keywords in CATEGORIES.items():
        score = sum(1 for kw in keywords if re.search(r"\b" + re.escape(kw), text))
        if score > best_score:
            best_cat, best_score = cat, score
    return best_cat


def _to_float(price_str):
    return float(price_str.replace(",", "."))


def _clean_description(line):
    desc = END_PRICE_RE.sub("", line)
    desc = re.sub(r"^\s*\d+\s*[xX]\s+", "", desc)
    desc = re.sub(r"^\s*\d{5,}\s+", "", desc)
    desc = re.sub(r"[£$€]", "", desc)
    desc = re.sub(r"^[^A-Za-z0-9]+|[^A-Za-z0-9)]+$", "", desc)
    return re.sub(r"\s{2,}", " ", desc).strip()


def find_receipt_total(text):
    totals = []
    for line in text.splitlines():
        if TOTAL_RE.search(line) and not SUBTOTAL_RE.search(line):
            prices = ANY_PRICE_RE.findall(line)
            if prices:
                totals.append(_to_float(prices[-1]))
    return max(totals) if totals else None


def parse_receipt_data(text):
    rows = []
    for raw in text.splitlines():
        line = raw.strip(" |_~[]{}")
        if len(line) < 4:
            continue
        if SKIP_RE.search(line) or DATE_RE.search(line) or QTY_DETAIL_RE.search(line):
            continue

        match = END_PRICE_RE.search(line)
        if not match:
            continue

        description = _clean_description(line)
        if len(description) < 2 or not re.search(r"[A-Za-z]{2,}", description):
            continue

        rows.append({
            "Item Description": description,
            "Category": categorise(description),
            "Cost (£)": _to_float(match.group(1)),
        })

    df = pd.DataFrame(rows, columns=["Item Description", "Category", "Cost (£)"])
    return df, find_receipt_total(text)


def extract_text_from_receipt(image):
    variants = {
        "cleaned": preprocess_image(image),
        "grayscale": _to_gray(image),
    }
    best_text, best_score = "", -1
    for img in variants.values():
        for psm in (6, 4):
            text = pytesseract.image_to_string(img, config=f"--oem 3 --psm {psm}")
            score = sum(1 for ln in text.splitlines() if END_PRICE_RE.search(ln.strip(" |_~[]{}")))
            if score > best_score:
                best_text, best_score = text, score
    return best_text