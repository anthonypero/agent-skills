'use strict';

// Accept/reject harness for schemas/round.schema.json and schemas/feedback.schema.json
// (tech-requirements §5.1, §5.2, §5.5). Run with: npm test

const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const Ajv = require('ajv');

const ROOT = path.resolve(__dirname, '..');
const SCHEMAS = path.join(ROOT, 'schemas');
const FIX = path.join(__dirname, 'fixtures');

function readJSON(p) {
  return JSON.parse(fs.readFileSync(p, 'utf8'));
}

const roundSchema = readJSON(path.join(SCHEMAS, 'round.schema.json'));
const feedbackSchema = readJSON(path.join(SCHEMAS, 'feedback.schema.json'));

// strict:false keeps Ajv from rejecting the schemas themselves on pedantic
// meta-checks; data validation (oneOf / if-then / const) is unaffected.
const ajv = new Ajv({ allErrors: true, strict: false });
ajv.addSchema(feedbackSchema); // $id: feedback.schema.json
ajv.addSchema(roundSchema); // $id: round.schema.json; $ref resolves to the above
const validateRound = ajv.getSchema('round.schema.json');
const validateFeedback = ajv.getSchema('feedback.schema.json');

// [label, validator, fixture-file]
const ACCEPT = [
  ['round: submitted, non-null snapshot, one feedback anchor', validateRound, 'round.valid.json'],
  ['feedback: comment on source/line', validateFeedback, 'feedback.valid-comment.json'],
  ['feedback: edit on source/line (original + replacement)', validateFeedback, 'feedback.valid-edit.json'],
  ['feedback: comment on spatial/box', validateFeedback, 'feedback.valid-spatial.json'],
  ['feedback: comment on text/quote', validateFeedback, 'feedback.valid-text.json'],
  // v2.4 — an inline EDIT now anchors to a text/quote (quote === original); confirm the wire shape.
  ['feedback: edit on text/quote (quote === original)', validateFeedback, 'feedback.valid-edit-text.json'],
];

const REJECT = [
  ['round: unknown status value', validateRound, 'round.invalid-status.json'],
  ['feedback: anchor carries two kinds (source + spatial)', validateFeedback, 'feedback.invalid-two-kinds.json'],
  ['feedback: source anchor with two sub-fields (line + keyPath)', validateFeedback, 'feedback.invalid-source-two-subfields.json'],
  ['feedback: edit missing conditionally-required replacement', validateFeedback, 'feedback.invalid-edit-missing-replacement.json'],
];

for (const [label, validate, file] of ACCEPT) {
  test(`ACCEPT ${label}`, () => {
    const data = readJSON(path.join(FIX, file));
    const ok = validate(data);
    assert.equal(ok, true, `expected ACCEPT but got: ${JSON.stringify(validate.errors)}`);
  });
}

for (const [label, validate, file] of REJECT) {
  test(`REJECT ${label}`, () => {
    const data = readJSON(path.join(FIX, file));
    const ok = validate(data);
    assert.equal(ok, false, 'expected REJECT but schema accepted the fixture');
  });
}
