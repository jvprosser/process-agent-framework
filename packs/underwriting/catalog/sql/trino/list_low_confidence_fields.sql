-- result: fields
SELECT
  w.document_id,
  w.page_index,
  w.word_text,
  w.confidence,
  f.field_path,
  f.field_value
FROM ${var:database}.ocr_word w
JOIN ${var:database}.ocr_field f
  ON f.submission_id = w.submission_id
 AND f.document_id = w.document_id
 AND instr(f.field_value, w.word_text) > 0
WHERE w.submission_id = ${var:claim_id}
  AND w.word_text IS NOT NULL
  AND TRIM(w.word_text) != ''
  AND w.confidence < 0.8
ORDER BY w.confidence, f.field_path
