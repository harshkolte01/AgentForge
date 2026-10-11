import assert from "node:assert/strict";
import { test } from "node:test";
import { artifactHref, deliverableViews, formatBytes } from "../features/sessions/answer-extras-model.ts";

// Deliverables --------------------------------------------------------------------------------------------------------

test("a deliverable links to the owner download route of its own task, with a readable size", () => {
  const [view] = deliverableViews("11111111-1111-4111-8111-111111111111", [{ id: "22222222-2222-4222-8222-222222222222", filename: "report.md", byte_size: 2048 }]);
  assert.deepEqual(view, { id: "22222222-2222-4222-8222-222222222222", filename: "report.md", size: "2 KB", href: "/api/v1/tasks/11111111-1111-4111-8111-111111111111/artifacts/22222222-2222-4222-8222-222222222222" });
});

test("ids that are not plain identifiers produce no link", () => {
  assert.equal(artifactHref("task", "../../etc"), null);
  assert.equal(artifactHref("task/../x", "a"), null);
  assert.equal(artifactHref("task", "a?b=1"), null);
  assert.equal(deliverableViews("t", [{ id: "a b", filename: "", byte_size: 1 }])[0].href, null);
  assert.equal(deliverableViews("t", [{ id: "a", filename: " ", byte_size: 1 }])[0].filename, "Download");
});

test("sizes read like a person would say them", () => {
  assert.equal(formatBytes(0), "0 B");
  assert.equal(formatBytes(812), "812 B");
  assert.equal(formatBytes(1536), "1.5 KB");
  assert.equal(formatBytes(1024), "1 KB");
  assert.equal(formatBytes(50 * 1024), "50 KB");
  assert.equal(formatBytes(1.3 * 1024 * 1024), "1.3 MB");
  assert.equal(formatBytes(-1), "");
  assert.equal(formatBytes(Number.NaN), "");
});
