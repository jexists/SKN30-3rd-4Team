import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { createWorker, OEM, PSM } from "tesseract.js";

const here = path.dirname(fileURLToPath(import.meta.url));
const root = path.resolve(here, "..", "..");
const assessmentPath = path.join(root, "reports", "legal_api_v2", "ocr_assessment", "manifest.json");
const outputDir = path.join(root, "data", "legal_api_v2", "05_ocr_json", "review");
const outputPath = path.join(outputDir, "ocr_results.jsonl");
const manifestPath = path.join(root, "data", "legal_api_v2", "05_ocr_json", "ocr_manifest.json");
const logDir = path.join(root, "pipeline", "logs", "ocr");
const runId = new Date().toISOString().replaceAll(":", "").replace(".000", "");
const logPath = path.join(logDir, `${runId}.jsonl`);

const assessment = JSON.parse(fs.readFileSync(assessmentPath, "utf8"));
const candidateById = new Map(assessment.sample_candidates.map((item) => [item.record_id, item]));
const downloads = (assessment.sample_downloads || []).filter((item) => item.status === "downloaded");
if (downloads.length === 0) {
  throw new Error("다운로드된 OCR 표본이 없습니다. ocr_assessment.py --download-samples를 먼저 실행하세요.");
}

fs.mkdirSync(outputDir, { recursive: true });
fs.mkdirSync(logDir, { recursive: true });
const appendLog = (event) => fs.appendFileSync(logPath, `${JSON.stringify({ at: new Date().toISOString(), ...event })}\n`, "utf8");

appendLog({ event: "run_started", samples: downloads.length, languages: ["kor", "eng"] });
const latestProgress = new Map();
const worker = await createWorker(["kor", "eng"], OEM.LSTM_ONLY, {
  cachePath: path.join(here, ".cache"),
  logger: (message) => {
    if (message.status && message.progress !== undefined) {
      const percent = Math.floor(message.progress * 100);
      if (latestProgress.get(message.status) !== percent && percent % 10 === 0) {
        latestProgress.set(message.status, percent);
        process.stdout.write(`[ocr] ${message.status}: ${percent}%\n`);
      }
    }
  },
});
await worker.setParameters({ tessedit_pageseg_mode: PSM.AUTO });

const records = [];
const provenance = [];
const totalStarted = performance.now();
try {
  for (let index = 0; index < downloads.length; index += 1) {
    const download = downloads[index];
    const candidate = candidateById.get(download.record_id);
    if (!candidate) throw new Error(`표본 metadata 없음: ${download.record_id}`);
    const imagePath = path.resolve(root, ...download.path.split("/"));
    process.stdout.write(`[${index + 1}/${downloads.length}] ${download.record_id}\n`);
    const started = performance.now();
    try {
      const result = await worker.recognize(imagePath);
      const elapsedSeconds = (performance.now() - started) / 1000;
      const text = (result.data.text || "").trim();
      records.push({
        page_content: text,
        metadata: {
          source_type: candidate.source_type,
          source_id: candidate.source_id,
          record_id: candidate.record_id,
          parent_record_id: candidate.parent_record_id,
          doc_title: candidate.doc_title,
          section: candidate.section,
        },
      });
      provenance.push({
        record_id: candidate.record_id,
        parent_record_id: candidate.parent_record_id,
        image_path: download.path,
        image_sha256: download.sha256,
        ocr_engine: "tesseract.js",
        ocr_version: "7.0.0",
        languages: ["kor", "eng"],
        ocr_status: text ? "success" : "empty",
        ocr_confidence: result.data.confidence,
        elapsed_seconds: elapsedSeconds,
        output_chars: text.length,
      });
      appendLog({ event: "sample_completed", record_id: candidate.record_id, elapsed_seconds: elapsedSeconds, confidence: result.data.confidence, output_chars: text.length });
    } catch (error) {
      provenance.push({
        record_id: candidate.record_id,
        parent_record_id: candidate.parent_record_id,
        image_path: download.path,
        image_sha256: download.sha256,
        ocr_engine: "tesseract.js",
        ocr_version: "7.0.0",
        languages: ["kor", "eng"],
        ocr_status: "failed",
        error: String(error),
      });
      appendLog({ event: "sample_failed", record_id: candidate.record_id, error: String(error) });
    }
  }
} finally {
  await worker.terminate();
}

const temporaryOutput = `${outputPath}.tmp`;
fs.writeFileSync(temporaryOutput, records.map((record) => JSON.stringify(record)).join("\n") + "\n", "utf8");
fs.renameSync(temporaryOutput, outputPath);
const succeeded = provenance.filter((item) => item.ocr_status === "success");
const totalSeconds = (performance.now() - totalStarted) / 1000;
const manifest = {
  pipeline: "legal_rag_v2/sample_ocr",
  run_id: runId,
  finished_at: new Date().toISOString(),
  engine: { name: "tesseract.js", version: "7.0.0", languages: ["kor", "eng"], oem: "LSTM_ONLY", psm: "AUTO" },
  input_assessment: path.relative(root, assessmentPath).replaceAll("\\", "/"),
  output: path.relative(root, outputPath).replaceAll("\\", "/"),
  totals: {
    samples: provenance.length,
    success: succeeded.length,
    failed: provenance.filter((item) => item.ocr_status === "failed").length,
    empty: provenance.filter((item) => item.ocr_status === "empty").length,
    elapsed_seconds: totalSeconds,
    average_seconds_per_success: succeeded.length ? succeeded.reduce((sum, item) => sum + item.elapsed_seconds, 0) / succeeded.length : null,
    projected_seconds_for_113: succeeded.length ? (succeeded.reduce((sum, item) => sum + item.elapsed_seconds, 0) / succeeded.length) * 113 : null,
  },
  items: provenance,
};
const temporaryManifest = `${manifestPath}.tmp`;
fs.mkdirSync(path.dirname(manifestPath), { recursive: true });
fs.writeFileSync(temporaryManifest, `${JSON.stringify(manifest, null, 2)}\n`, "utf8");
fs.renameSync(temporaryManifest, manifestPath);
appendLog({ event: "run_completed", totals: manifest.totals });
process.stdout.write(`${JSON.stringify(manifest.totals, null, 2)}\n`);
