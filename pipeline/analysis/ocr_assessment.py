"""이미지 metadata와 부모 텍스트를 대조해 비차단 OCR 표본 후보를 만든다."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import mimetypes
import re
import sys
from collections import Counter
from pathlib import Path
from typing import Any
from urllib.request import Request, urlopen

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from pipeline.common import ROOT, iter_jsonl, relative_to_root, utc_now_iso, write_json_atomic

IMAGE_JSONL = ROOT / "data/legal_api_v2/05_processed_document_json/full_20260719_163719/image/images.jsonl"
FINAL_DIR = ROOT / "data/legal_api_v2/06_final"
SOURCE_TEXT_DIR = ROOT / "data/legal_api_v2/05_processed_document_json/full_20260719_163719/text"
OUTPUT_DIR = ROOT / "reports/legal_api_v2/ocr_assessment"
DOWNLOAD_DIR = ROOT / "data/legal_api_v2/05_ocr_source"


def download_samples(candidates: list[dict[str, Any]], download_dir: Path) -> list[dict[str, Any]]:
    download_dir.mkdir(parents=True, exist_ok=True)
    results = []
    for item in candidates:
        raw_url = str(item["image_url"])
        normalized_path = raw_url.replace("/LSA/", "/LSW/")
        url = normalized_path if normalized_path.startswith("http") else "https://www.law.go.kr" + normalized_path
        result = {"record_id": item["record_id"], "url": url, "status": "failed"}
        try:
            request = Request(url, headers={"User-Agent": "legal-rag-v2-ocr-assessment/1.0"})
            with urlopen(request, timeout=60) as response:
                content = response.read()
                mime_type = response.headers.get_content_type()
            if not mime_type.startswith("image/"):
                raise ValueError(f"이미지가 아닌 응답: mime={mime_type}, bytes={len(content)}")
            suffix = mimetypes.guess_extension(mime_type) or ".bin"
            safe_name = re.sub(r"[^A-Za-z0-9._-]", "_", str(item["record_id"]))
            target = download_dir / f"{safe_name}{suffix}"
            temporary = target.with_name(target.name + ".tmp")
            temporary.write_bytes(content)
            temporary.replace(target)
            width = height = None
            try:
                from PIL import Image

                with Image.open(target) as image:
                    width, height = image.size
            except (ImportError, OSError):
                pass
            result.update(
                {
                    "status": "downloaded",
                    "path": relative_to_root(target),
                    "mime_type": mime_type,
                    "extension": suffix,
                    "bytes": len(content),
                    "width": width,
                    "height": height,
                    "sha256": hashlib.sha256(content).hexdigest(),
                }
            )
        except Exception as exc:  # noqa: BLE001 - 표본별 실패를 manifest에 보존
            result["error"] = str(exc)
        results.append(result)
    return results


def assess(
    image_jsonl: Path = IMAGE_JSONL,
    final_dir: Path = FINAL_DIR,
    output_dir: Path = OUTPUT_DIR,
    source_text_dir: Path = SOURCE_TEXT_DIR,
    download: bool = False,
    download_dir: Path = DOWNLOAD_DIR,
) -> dict[str, Any]:
    output_dir.mkdir(parents=True, exist_ok=True)
    baseline_parent_lengths = {}
    for path in sorted(final_dir.glob("*.jsonl")):
        for row in iter_jsonl(path):
            metadata = row.get("metadata") or {}
            baseline_parent_lengths[str(metadata.get("record_id") or "")] = len(str(row.get("page_content") or ""))
    source_parent_lengths = {}
    for path in sorted(source_text_dir.glob("*.jsonl")):
        for row in iter_jsonl(path):
            metadata = row.get("metadata") or {}
            source_parent_lengths[str(metadata.get("record_id") or "")] = len(str(row.get("page_content") or ""))
    items = []
    for row in iter_jsonl(image_jsonl):
        metadata = row.get("metadata") or {}
        parent_id = str(metadata.get("parent_record_id") or "")
        items.append(
            {
                "record_id": metadata.get("record_id"),
                "parent_record_id": parent_id,
                "source_type": metadata.get("source_type"),
                "source_id": metadata.get("source_id"),
                "section": metadata.get("section"),
                "doc_title": metadata.get("doc_title"),
                "image_id": metadata.get("image_id"),
                "image_url": metadata.get("image_url"),
                "has_url": bool(metadata.get("image_url")),
                "parent_text_available": parent_id in source_parent_lengths,
                "parent_in_baseline": parent_id in baseline_parent_lengths,
                "parent_char_count": source_parent_lengths.get(parent_id),
            }
        )
    missing = [item for item in items if not item["parent_text_available"]]
    if missing:
        raise ValueError(f"부모 텍스트 누락 이미지: {len(missing)}")
    candidates = []
    for source_type, section, count in (("precedent", "body", 5), ("statute", "annex", 5)):
        pool = [
            item for item in items
            if item["source_type"] == source_type and item["section"] == section
            and item["has_url"] and item["parent_in_baseline"]
        ]
        candidates.extend(pool[:count])
    with (output_dir / "sample_candidates.csv").open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(candidates[0]))
        writer.writeheader()
        writer.writerows(candidates)
    distribution = Counter(f"{item['source_type']}:{item['section']}" for item in items)
    manifest = {
        "generated_at": utc_now_iso(),
        "input": relative_to_root(image_jsonl),
        "records": len(items),
        "urls_present": sum(item["has_url"] for item in items),
        "urls_missing": sum(not item["has_url"] for item in items),
        "parent_text_available": sum(item["parent_text_available"] for item in items),
        "parent_in_baseline": sum(item["parent_in_baseline"] for item in items),
        "parent_excluded_by_baseline_filters": sum(not item["parent_in_baseline"] for item in items),
        "distribution": dict(distribution),
        "sample_candidates": candidates,
        "baseline_decision": "exclude_images_parent_text_available",
        "baseline_blocked_by_ocr": False,
        "sample_ocr_status": "not_attempted_no_approved_local_ocr_engine",
        "inclusion_rule": "부모 텍스트에 없는 검색 가치 있는 법적 사실·금액·표·도식이 표본 OCR에서 확인될 때만 증분 추가",
    }
    manual_review_path = output_dir / "manual_review.json"
    if manual_review_path.exists():
        manifest["manual_review"] = json.loads(manual_review_path.read_text(encoding="utf-8"))
    if download:
        manifest["sample_downloads"] = download_samples(candidates, download_dir)
    else:
        previous_manifest_path = output_dir / "manifest.json"
        if previous_manifest_path.exists():
            previous = json.loads(previous_manifest_path.read_text(encoding="utf-8"))
            if "sample_downloads" in previous:
                manifest["sample_downloads"] = previous["sample_downloads"]
    write_json_atomic(output_dir / "manifest.json", manifest)
    return manifest


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--image-jsonl", type=Path, default=IMAGE_JSONL)
    parser.add_argument("--final-dir", type=Path, default=FINAL_DIR)
    parser.add_argument("--output-dir", type=Path, default=OUTPUT_DIR)
    parser.add_argument("--source-text-dir", type=Path, default=SOURCE_TEXT_DIR)
    parser.add_argument("--download-samples", action="store_true")
    parser.add_argument("--download-dir", type=Path, default=DOWNLOAD_DIR)
    args = parser.parse_args()
    print(
        json.dumps(
            assess(
                args.image_jsonl,
                args.final_dir,
                args.output_dir,
                args.source_text_dir,
                args.download_samples,
                args.download_dir,
            ),
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
