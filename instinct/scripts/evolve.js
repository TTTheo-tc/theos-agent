#!/usr/bin/env node
/**
 * 升格引擎 (Evolution Engine)
 *
 * Scans memory/instinct/events/, clusters transferable_rules,
 * and promotes rules meeting thresholds to rules/ACTIVE.md.
 *
 * Thresholds (from instinct_refactor_guide.md §5.3):
 *   - frequency >= 3 events
 *   - avg confidence >= 0.72
 *   - last seen within 14 days
 *
 * Usage: node evolve.js [--dry-run]
 *
 * Kill switch: INSTINCT_EVOLVE_ENABLED=false
 */
const fs   = require('fs');
const path = require('path');
const os   = require('os');

const WORKSPACE  = process.env.THEOS_WORKSPACE
                   || path.join(os.homedir(), '.theos', 'workspace');
const MEMORY_DIR = path.join(WORKSPACE, 'memory', 'instinct');
const EVENTS_DIR = path.join(MEMORY_DIR, 'events');
const RULES_DIR  = path.join(MEMORY_DIR, 'rules');

const FREQ_THRESHOLD  = Number(process.env.INSTINCT_EVOLVE_MIN_FREQ || 3);
const CONF_THRESHOLD  = Number(process.env.INSTINCT_EVOLVE_MIN_CONF || 0.72);
const RECENCY_DAYS    = Number(process.env.INSTINCT_EVOLVE_RECENCY_DAYS || 14);
const PROBATION_STALE_DAYS = Number(process.env.INSTINCT_PROBATION_STALE_DAYS || 7);
const MAX_BOOST       = Number(process.env.INSTINCT_MAX_DOMAIN_BOOST || 3.0);
const MIN_INTERVAL_SECONDS = Math.max(0, Number(process.env.INSTINCT_EVOLVE_MIN_INTERVAL_SECONDS || 0));

const DRY_RUN = process.argv.includes('--dry-run');

if (process.env.INSTINCT_EVOLVE_ENABLED === 'false') {
  console.log('⏭ [Evolver] disabled via INSTINCT_EVOLVE_ENABLED=false');
  process.exit(0);
}

// ── Helpers ───────────────────────────────────────────────────────────────

function ensureDir(dir) {
  if (!fs.existsSync(dir)) fs.mkdirSync(dir, { recursive: true });
}

function loadIndex() {
  const fpath = path.join(RULES_DIR, 'index.json');
  try {
    return JSON.parse(fs.readFileSync(fpath, 'utf-8'));
  } catch {
    return {};
  }
}

function loadEvents() {
  if (!fs.existsSync(EVENTS_DIR)) return [];
  return fs.readdirSync(EVENTS_DIR)
    .filter(f => f.endsWith('.json'))
    .map(f => {
      try { return JSON.parse(fs.readFileSync(path.join(EVENTS_DIR, f), 'utf-8')); }
      catch { return null; }
    })
    .filter(Boolean);
}

/** Normalize rule text for dedup: lowercase, trim, collapse whitespace. */
function normalizeRule(text) {
  return text.toLowerCase().replace(/\s+/g, ' ').trim();
}

function daysSince(isoStr) {
  return (Date.now() - new Date(isoStr).getTime()) / (1000 * 60 * 60 * 24);
}

function isoDate(d) {
  return d.toISOString().slice(0, 10);
}

function addDays(d, n) {
  const r = new Date(d);
  r.setDate(r.getDate() + n);
  return r;
}

// ── Temporal decay ───────────────────────────────────────────────────────

const HALF_LIFE = { stable: Infinity, adaptive: 60, volatile: 21 }; // days
const ARCHIVE_THRESHOLD = 0.3;

/**
 * Apply metadata-rich temporal decay to active rules.
 * Rules whose effective confidence drops below ARCHIVE_THRESHOLD are archived.
 * Stable rules never decay.
 */
function decayRules(activeRules) {
  const now = Date.now();
  const toArchive = [];
  const kept = [];

  for (const rule of activeRules) {
    if (rule.class === 'stable') { kept.push(rule); continue; }

    const lastSeen = rule.last_seen ? new Date(rule.last_seen).getTime() : now;
    const ageDays = (now - lastSeen) / (1000 * 60 * 60 * 24);
    const halfLife = HALF_LIFE[rule.class] || 60;
    const decayFactor = Math.exp(-Math.LN2 * ageDays / halfLife);
    const baseConf = rule.base_conf || rule.conf || 0.7;
    const effectiveConf = baseConf * decayFactor;

    if (effectiveConf < ARCHIVE_THRESHOLD) {
      rule.demoted_reason = 'decay';
      toArchive.push(rule);
    } else {
      rule.conf = Math.round(effectiveConf * 100) / 100;
      kept.push(rule);
    }
  }
  return { kept, toArchive };
}

// ── Cluster rules across events ──────────────────────────────────────────

function clusterRules(events) {
  // Map: normalized_rule -> { text, count, totalConf, taskIds[], sessionKeys[], lastSeen, firstSeen, domains Set, artifacts[], tests[], _seen Set }
  const clusters = new Map();

  for (const ev of events) {
    const rules = ev.generalization?.transferable_rules || [];
    const conf  = ev.generalization?.confidence || 0.5;
    const ts    = ev.timestamp;
    const taskId = ev.task_id;
    const sessionKey = ev.session_key || '';
    const routingDomains = ev.routing?.domains || [];
    const evArtifacts = ev.outcome?.artifacts || [];
    const evTests = ev.outcome?.tests || [];

    for (const rule of rules) {
      const key = normalizeRule(rule);
      if (!key || key.length < 10) continue;

      if (!clusters.has(key)) {
        clusters.set(key, {
          text: rule,             // keep original casing from first occurrence
          count: 0,
          totalConf: 0,
          taskIds: [],
          sessionKeys: [],
          lastSeen: ts,
          firstSeen: ts,
          domains: new Set(),
          artifacts: [],
          tests: [],
          _seen: new Set(),       // dedup: (rule_key, task_id) pairs
        });
      }

      const c = clusters.get(key);

      // Dedup: same rule + same task_id counts only once.
      // reflect.js writes the same rule to both events/ and live_rules.jsonl;
      // without this guard the count is inflated when evolve merges both sources.
      const dedupKey = `${key}|${taskId}`;
      if (c._seen.has(dedupKey)) {
        // Still update timestamps / metadata but don't bump count/conf.
        if (new Date(ts) > new Date(c.lastSeen)) c.lastSeen = ts;
        if (new Date(ts) < new Date(c.firstSeen)) c.firstSeen = ts;
        continue;
      }
      c._seen.add(dedupKey);

      c.count += 1;
      c.totalConf += conf;
      c.taskIds.push(taskId);
      if (sessionKey) c.sessionKeys.push(sessionKey);
      for (const d of routingDomains) c.domains.add(d);
      c.artifacts.push(...evArtifacts);
      c.tests.push(...evTests);
      if (new Date(ts) > new Date(c.lastSeen)) c.lastSeen = ts;
      if (new Date(ts) < new Date(c.firstSeen)) c.firstSeen = ts;
    }
  }

  return clusters;
}

// ── Load PROBATION rules ─────────────────────────────────────────────────

function loadProbationRules() {
  const fpath = path.join(RULES_DIR, 'PROBATION.md');
  if (!fs.existsSync(fpath)) return [];
  const text = fs.readFileSync(fpath, 'utf-8');
  const rules = [];
  for (const line of text.split('\n')) {
    const m = line.match(/^- \[(.+?)\] (.+?)(?:\s+<!--\s*(.+?)\s*-->)?$/);
    if (!m) continue;
    const meta = {};
    if (m[3]) {
      for (const pair of m[3].split(/\s+/)) {
        const [k, ...rest] = pair.split(':');
        if (k && rest.length) meta[k] = rest.join(':');
      }
    }
    rules.push({
      id: m[1],
      text: m[2].trim(),
      scope: meta.scope || '',
      domains: (meta.domains || '').split(',').filter(Boolean),
      boost: parseFloat(meta.boost) || 0,
      class: 'probation',
      conf: parseFloat(meta.conf) || 0.7,
      base_conf: parseFloat(meta.base_conf) || parseFloat(meta.conf) || 0.7,
      last_seen: meta.last_seen || '',
      first_seen: meta.first_seen || meta.last_seen || '',
      review_after: meta.review_after || '',
      distinct_sessions: parseInt(meta.distinct_sessions) || 1,
      distinct_tasks: parseInt(meta.distinct_tasks) || 1,
      promoted_reason: meta.promoted_reason || '',
    });
  }
  return rules;
}

function writeProbation(rules) {
  ensureDir(RULES_DIR);
  const lines = [
    '# Probation Rules',
    '',
    'Rules pending verification before promotion to ACTIVE.',
    '`- [ID] rule text  <!-- scope:X domains:Y boost:Z class:probation conf:N base_conf:N first_seen:DATE last_seen:DATE distinct_sessions:N distinct_tasks:N -->`',
    '',
  ];

  for (const r of rules) {
    lines.push(formatProbationLine(r));
  }

  lines.push('');
  const fpath = path.join(RULES_DIR, 'PROBATION.md');
  if (!DRY_RUN) fs.writeFileSync(fpath, lines.join('\n'));
  return fpath;
}

function formatProbationLine(r) {
  const scope = r.scope || 'domain_boost';
  const domains = Array.isArray(r.domains) ? r.domains.join(',') : (r.domains || '');
  const boost = r.boost || 0;
  const conf = r.conf || 0.7;
  const baseConf = r.base_conf || conf;
  const firstSeen = r.first_seen || isoDate(new Date());
  const lastSeen = r.last_seen || isoDate(new Date());
  const distinctSessions = r.distinct_sessions || 1;
  const distinctTasks = r.distinct_tasks || 1;
  const promotedReason = r.promoted_reason || '';
  return `- [${r.id}] ${r.text}  <!-- scope:${scope} domains:${domains} boost:${boost} class:probation conf:${conf} base_conf:${baseConf} first_seen:${firstSeen} last_seen:${lastSeen} distinct_sessions:${distinctSessions} distinct_tasks:${distinctTasks} promoted_reason:${promotedReason} -->`;
}

// ── Load live_rules.jsonl as additional input ────────────────────────────

function loadLiveRules() {
  const fpath = path.join(MEMORY_DIR, 'live_rules.jsonl');
  if (!fs.existsSync(fpath)) return [];
  const lines = fs.readFileSync(fpath, 'utf-8').split('\n').filter(Boolean);
  const rules = [];
  for (const line of lines) {
    try {
      rules.push(JSON.parse(line));
    } catch { /* skip malformed */ }
  }
  return rules;
}

/** Convert live_rules.jsonl entries into synthetic events for clusterRules(). */
function liveRulesToEvents(liveRules) {
  return liveRules.map(lr => ({
    task_id: lr.task_id || `live#${lr.timestamp || Date.now()}`,
    session_key: lr.session_key || 'unknown',
    timestamp: lr.timestamp || new Date().toISOString(),
    request: { demand_class: lr.demand_class || 'other' },
    generalization: {
      transferable_rules: [lr.text],
      confidence: lr.confidence || 0.5,
    },
    outcome: {
      artifacts: lr.artifacts || [],
      tests: lr.tests || [],
    },
    routing: { domains: lr.domains || [] },
  }));
}

// ── Detect conflicts with existing ACTIVE rules ──────────────────────────

function loadActiveRules() {
  const fpath = path.join(RULES_DIR, 'ACTIVE.md');
  if (!fs.existsSync(fpath)) return [];
  const text = fs.readFileSync(fpath, 'utf-8');
  const rules = [];
  for (const line of text.split('\n')) {
    const m = line.match(/^- \[(.+?)\] (.+?)(?:\s+<!--\s*(.+?)\s*-->)?$/);
    if (!m) continue;
    const meta = {};
    if (m[3]) {
      for (const pair of m[3].split(/\s+/)) {
        const [k, ...rest] = pair.split(':');
        if (k && rest.length) meta[k] = rest.join(':');
      }
    }
    rules.push({
      id: m[1],
      text: m[2].trim(),
      scope: meta.scope || '',
      domains: (meta.domains || '').split(',').filter(Boolean),
      boost: parseFloat(meta.boost) || 0,
      class: meta.class || 'adaptive',
      conf: parseFloat(meta.conf) || 0.7,
      base_conf: parseFloat(meta.base_conf) || parseFloat(meta.conf) || 0.7,
      first_seen: meta.first_seen || '',
      last_seen: meta.last_seen || '',
      review_after: meta.review_after || '',
      distinct_sessions: parseInt(meta.distinct_sessions) || 0,
      distinct_tasks: parseInt(meta.distinct_tasks) || 0,
      promoted_reason: meta.promoted_reason || '',
      demoted_reason: meta.demoted_reason || '',
      verification_support_count: parseInt(meta.verification_support_count) || 0,
      user_adopted_count: parseInt(meta.user_adopted_count) || 0,
    });
  }
  return rules;
}

/** Simple conflict check: if new rule contains opposite keywords to an active rule. */
const OPPOSE_PAIRS = [
  ['优先', '避免'], ['always', 'never'], ['prefer', 'avoid'],
  ['先', '后'], ['before', 'after'],
];

function hasConflict(newRule, activeRules) {
  const lower = newRule.toLowerCase();
  for (const active of activeRules) {
    const aLower = active.text.toLowerCase();
    for (const [a, b] of OPPOSE_PAIRS) {
      if ((lower.includes(a) && aLower.includes(b)) ||
          (lower.includes(b) && aLower.includes(a))) {
        // Check if they share domain context (rough: >30% word overlap)
        const nWords = new Set(lower.split(/\s+/));
        const aWords = new Set(aLower.split(/\s+/));
        let overlap = 0;
        for (const w of nWords) { if (aWords.has(w)) overlap++; }
        if (overlap / Math.max(nWords.size, 1) > 0.3) {
          return { conflictsWith: active.id, reason: `${a} vs ${b}` };
        }
      }
    }
  }
  return null;
}

// ── Simplicity preference (I3) ───────────────────────────────────────────

/**
 * Detect semantic overlap between a candidate rule and existing ACTIVE rules.
 * Uses simple word overlap: >50% shared words indicates overlap.
 * Returns the overlapping rule if found, null otherwise.
 */
function findOverlappingRule(candidateText, existingRules) {
  function tokenize(text) {
    const lower = text.toLowerCase();
    const asciiWords = lower
      .replace(/[^\w\s\u3400-\u9fff]/g, ' ')
      .split(/\s+/)
      .filter(w => w.length > 2);
    if (asciiWords.length > 0) return new Set(asciiWords);

    const cjk = (lower.match(/[\u3400-\u9fff]/g) || []).join('');
    const grams = [];
    for (let i = 0; i < cjk.length - 1; i++) grams.push(cjk.slice(i, i + 2));
    return new Set(grams.filter(g => g.length === 2));
  }

  const cWords = tokenize(candidateText);
  if (cWords.size === 0) return null;

  for (const rule of existingRules) {
    const rWords = tokenize(rule.text);
    if (rWords.size === 0) continue;

    let shared = 0;
    for (const w of cWords) { if (rWords.has(w)) shared++; }
    const overlapRatio = shared / Math.min(cWords.size, rWords.size);
    if (overlapRatio > 0.5) return rule;
  }
  return null;
}

// ── Promote rules ────────────────────────────────────────────────────────

/**
 * Three-stage promotion:
 *   1. Candidates -> PROBATION (passesV1Checks + no conflict + conf >= 0.72)
 *   2. PROBATION -> ACTIVE (cross-session recurrence, freq+tasks, or verification signal)
 *   3. PROBATION -> ARCHIVE (stale or conflicting)
 */
function promote(clusters, activeRules, probationRules) {
  const promoted      = [];  // probation -> active
  const newProbation  = [];  // candidates -> probation
  const conflicts     = [];
  const now = Date.now();

  const activeNorms = new Set(activeRules.map(r => normalizeRule(r.text)));
  const probationNorms = new Set(probationRules.map(r => normalizeRule(r.text)));

  for (const [key, c] of clusters) {
    const avgConf = c.totalConf / c.count;
    const recency = daysSince(c.lastSeen);
    const distinctSessions = new Set(c.sessionKeys.filter(Boolean)).size;
    const distinctTasks = new Set(c.taskIds.filter(Boolean)).size;

    // Skip if already active
    if (activeNorms.has(key)) continue;

    // Check if already in probation — evaluate for promotion to active
    if (probationNorms.has(key)) {
      const existingProb = probationRules.find(r => normalizeRule(r.text) === key);
      if (!existingProb) continue;

      // Update probation rule metadata from latest cluster data
      existingProb.conf = Math.round(avgConf * 100) / 100;
      existingProb.last_seen = c.lastSeen ? c.lastSeen.slice(0, 10) : isoDate(new Date());
      existingProb.distinct_sessions = distinctSessions;
      existingProb.distinct_tasks = distinctTasks;

      // Check promotion conditions: any of the three signals
      let promotedReason = '';

      // Signal 1: Cross-session recurrence (distinct session_key >= 2)
      if (distinctSessions >= 2) {
        promotedReason = 'cross_session';
      }
      // Signal 2: freq >= 3 AND >= 2 distinct task_ids
      else if (c.count >= FREQ_THRESHOLD && distinctTasks >= 2) {
        promotedReason = 'freq_and_tasks';
      }
      // Signal 3: Verification signal — artifacts include test files that exist on disk
      else {
        const testFiles = [...new Set(c.tests)].filter(Boolean);
        const hasVerifiedTest = testFiles.some(tf => {
          const fullPath = path.join(process.cwd(), tf);
          return fs.existsSync(fullPath);
        });
        if (hasVerifiedTest) {
          promotedReason = 'verified_tests';
        }
      }

      if (promotedReason) {
        const conflict = hasConflict(c.text, activeRules);
        if (conflict) {
          conflicts.push({ rule: c.text, ...conflict, count: c.count, avgConf });
          continue;
        }

        const overlap = findOverlappingRule(c.text, activeRules);
        if (overlap && c.text.length >= overlap.text.length * 0.8) {
          continue;
        }

        const scope = c.domains.size > 1 ? 'risk_warn' : 'domain_boost';
        const domains = [...c.domains];
        const boost = Math.min(avgConf * 1.5, MAX_BOOST);

        promoted.push({
          id: existingProb.id,
          text: c.text,
          scope,
          domains,
          boost: Math.round(boost * 100) / 100,
          count: c.count,
          avgConf: Math.round(avgConf * 100) / 100,
          taskIds: c.taskIds.slice(0, 5),
          sessionKeys: [...new Set(c.sessionKeys)].slice(0, 5),
          lastSeen: c.lastSeen,
          firstSeen: c.firstSeen || existingProb.first_seen,
          distinct_sessions: distinctSessions,
          distinct_tasks: distinctTasks,
          promoted_reason: promotedReason,
          verification_support_count: [...new Set(c.tests)].filter(Boolean).length,
          user_adopted_count: 0,
        });
      }
      continue;
    }

    // New candidate — evaluate for probation entry
    if (avgConf < CONF_THRESHOLD) continue;
    if (recency > RECENCY_DAYS) continue;

    const conflict = hasConflict(c.text, activeRules);
    if (conflict) {
      conflicts.push({ rule: c.text, ...conflict, count: c.count, avgConf });
      continue;
    }

    // Simplicity preference: skip if overlapping with active and not more concise
    const overlap = findOverlappingRule(c.text, activeRules);
    if (overlap && c.text.length >= overlap.text.length * 0.8) {
      continue;
    }

    const scope = c.domains.size > 1 ? 'risk_warn' : 'domain_boost';
    const domains = [...c.domains];
    const boost = Math.min(avgConf * 1.5, MAX_BOOST);

    newProbation.push({
      id: `P${now}-${Math.random().toString(36).slice(2, 8)}`,
      text: c.text,
      scope,
      domains,
      boost: Math.round(boost * 100) / 100,
      class: 'probation',
      conf: Math.round(avgConf * 100) / 100,
      base_conf: Math.round(avgConf * 100) / 100,
      first_seen: c.firstSeen ? c.firstSeen.slice(0, 10) : isoDate(new Date()),
      last_seen: c.lastSeen ? c.lastSeen.slice(0, 10) : isoDate(new Date()),
      distinct_sessions: distinctSessions,
      distinct_tasks: distinctTasks,
      promoted_reason: '',
      verification_support_count: [...new Set(c.tests)].filter(Boolean).length,
      user_adopted_count: 0,
    });
  }

  return { promoted, newProbation, conflicts };
}

/** Evaluate probation rules for archival (stale or conflicting). */
function evaluateProbationArchival(probationRules, activeRules) {
  const toArchive = [];
  const kept = [];

  for (const rule of probationRules) {
    const lastSeen = rule.last_seen || '';
    const stale = lastSeen && daysSince(lastSeen) > PROBATION_STALE_DAYS;

    const conflict = hasConflict(rule.text, activeRules);
    const overlap = findOverlappingRule(rule.text, activeRules);

    if (stale) {
      rule.demoted_reason = 'stale';
      toArchive.push(rule);
    } else if (conflict) {
      rule.demoted_reason = 'conflict';
      toArchive.push(rule);
    } else if (overlap) {
      rule.demoted_reason = 'overlap';
      toArchive.push(rule);
    } else {
      kept.push(rule);
    }
  }

  return { kept, toArchive };
}

// ── Write ACTIVE.md ──────────────────────────────────────────────────────

function formatRuleLine(r) {
  const scope = r.scope || 'domain_boost';
  const domains = Array.isArray(r.domains) ? r.domains.join(',') : (r.domains || '');
  const boost = r.boost || 0;
  const cls = r.class || 'adaptive';
  const conf = r.conf || 0.7;
  const baseConf = r.base_conf || conf;
  const firstSeen = r.first_seen || isoDate(new Date());
  const lastSeen = r.last_seen || isoDate(new Date());
  const reviewDays = cls === 'volatile' ? 21 : 60;
  const reviewAfter = r.review_after || isoDate(addDays(new Date(lastSeen), reviewDays));
  const distinctSessions = r.distinct_sessions || 0;
  const distinctTasks = r.distinct_tasks || 0;
  const promotedReason = r.promoted_reason || '';
  const demotedReason = r.demoted_reason || '';
  const verificationCount = r.verification_support_count || 0;
  const userAdoptedCount = r.user_adopted_count || 0;
  return `- [${r.id}] ${r.text}  <!-- scope:${scope} domains:${domains} boost:${boost} class:${cls} conf:${conf} base_conf:${baseConf} first_seen:${firstSeen} last_seen:${lastSeen} review_after:${reviewAfter} distinct_sessions:${distinctSessions} distinct_tasks:${distinctTasks} promoted_reason:${promotedReason} demoted_reason:${demotedReason} verification_support_count:${verificationCount} user_adopted_count:${userAdoptedCount} -->`;
}

function writeActive(existingRules, newRules) {
  ensureDir(RULES_DIR);
  const lines = [
    '# Active Rules',
    '',
    'Auto-promoted by evolve.js. Format:',
    '`- [ID] rule text  <!-- scope:X domains:Y boost:Z class:C conf:N base_conf:N last_seen:DATE review_after:DATE demoted_reason:R verification_support_count:N user_adopted_count:N -->`',
    '',
  ];

  // Keep existing rules that aren't being replaced
  const newNorms = new Set(newRules.map(r => normalizeRule(r.text)));
  for (const r of existingRules) {
    if (!newNorms.has(normalizeRule(r.text))) {
      lines.push(formatRuleLine(r));
    }
  }

  // Add new rules with full metadata
  for (const r of newRules) {
    lines.push(formatRuleLine({
      id: r.id,
      text: r.text,
      scope: r.scope,
      domains: r.domains,
      boost: r.boost,
      class: 'adaptive',
      conf: r.avgConf,
      base_conf: r.avgConf,
      first_seen: r.firstSeen ? r.firstSeen.slice(0, 10) : isoDate(new Date()),
      last_seen: r.lastSeen ? r.lastSeen.slice(0, 10) : isoDate(new Date()),
      review_after: isoDate(addDays(new Date(), 60)),
      distinct_sessions: r.distinct_sessions || 0,
      distinct_tasks: r.distinct_tasks || 0,
      promoted_reason: r.promoted_reason || '',
    }));
  }

  lines.push('');
  const fpath = path.join(RULES_DIR, 'ACTIVE.md');
  if (!DRY_RUN) fs.writeFileSync(fpath, lines.join('\n'));
  return fpath;
}

// ── Write MEMORY.md managed block ────────────────────────────────────────

function writeMemoryBlock(activeRules) {
  const memoryPath = path.join(WORKSPACE, 'MEMORY.md');
  const startTag = '<!-- theos:instinct:rules:start -->';
  const endTag = '<!-- theos:instinct:rules:end -->';

  const blockLines = [
    startTag,
    '## Active Rules (managed by instinct)',
    '',
  ];
  for (const r of activeRules) {
    blockLines.push(`- ${r.text}`);
  }
  blockLines.push(endTag);
  const block = blockLines.join('\n');

  let content = '';
  if (fs.existsSync(memoryPath)) {
    content = fs.readFileSync(memoryPath, 'utf-8');
  }

  if (content.includes(startTag) && content.includes(endTag)) {
    // Replace existing block
    const re = new RegExp(
      startTag.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')
      + '[\\s\\S]*?'
      + endTag.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')
    );
    content = content.replace(re, block);
  } else if (content) {
    // Append block
    content = content.trimEnd() + '\n\n' + block + '\n';
  } else {
    // Create new file
    content = '# Memory\n\n' + block + '\n';
  }

  if (!DRY_RUN) fs.writeFileSync(memoryPath, content);
}

// ── Archive replaced rules ───────────────────────────────────────────────

function archiveReplaced(existingRules, newRules) {
  const newNorms = new Set(newRules.map(r => normalizeRule(r.text)));
  const replaced = newRules.length === 0
    ? existingRules
    : existingRules.filter(r => newNorms.has(normalizeRule(r.text)));
  if (replaced.length === 0) return;

  ensureDir(RULES_DIR);
  const fpath = path.join(RULES_DIR, 'ARCHIVE.md');
  const header = '# Archived Rules\n\nRules replaced or expired.\n\n';
  let content = fs.existsSync(fpath) ? fs.readFileSync(fpath, 'utf-8') : header;
  if (!content.trim()) content = header;

  const ts = new Date().toISOString();
  for (const r of replaced) {
    content += `- [${r.id}] ${r.text}  <!-- archived:${ts} -->\n`;
  }
  if (!DRY_RUN) fs.writeFileSync(fpath, content);
}

// ── Update index.json ────────────────────────────────────────────────────

function updateIndex(promoted, conflicts) {
  ensureDir(RULES_DIR);
  const fpath = path.join(RULES_DIR, 'index.json');
  const index = loadIndex();

  index.last_evolved = new Date().toISOString();
  index.active_count = loadActiveRules().length;
  index.total_promotions = (index.total_promotions || 0) + promoted.length;
  index.pending_conflicts = conflicts.length;

  if (!DRY_RUN) fs.writeFileSync(fpath, JSON.stringify(index, null, 2) + '\n');
  return index;
}

// ── Decay: archive stale rules ───────────────────────────────────────────

function decayStaleRules(activeRules, events) {
  const DECAY_DAYS = RECENCY_DAYS * 3; // 42 days default
  const now = Date.now();
  const stale = [];

  for (const rule of activeRules) {
    // Check if any recent event references this rule
    const norm = normalizeRule(rule.text);
    const lastHit = events
      .filter(ev => (ev.generalization?.transferable_rules || [])
        .some(r => normalizeRule(r) === norm))
      .map(ev => new Date(ev.timestamp).getTime())
      .sort((a, b) => b - a)[0];

    if (!lastHit || (now - lastHit) / (1000 * 60 * 60 * 24) > DECAY_DAYS) {
      stale.push(rule);
    }
  }

  return stale;
}

// ── Main ─────────────────────────────────────────────────────────────────

console.log('🧬 [Evolver] Scanning events...');

if (!DRY_RUN && MIN_INTERVAL_SECONDS > 0) {
  const index = loadIndex();
  const lastEvolvedAt = index.last_evolved ? new Date(index.last_evolved).getTime() : 0;
  if (lastEvolvedAt && (Date.now() - lastEvolvedAt) / 1000 < MIN_INTERVAL_SECONDS) {
    console.log(`  Skipping evolve: last run at ${index.last_evolved}.`);
    process.exit(0);
  }
}

// Load events from events/ and live_rules.jsonl
const events = loadEvents();
const liveRules = loadLiveRules();
const liveEvents = liveRulesToEvents(liveRules);
const allEvents = [...events, ...liveEvents];

if (allEvents.length === 0) {
  console.log('  No events found. Nothing to evolve.');
  process.exit(0);
}
console.log(`  Found ${events.length} event(s), ${liveRules.length} live rule(s).`);

const clusters = clusterRules(allEvents);
console.log(`  Clustered into ${clusters.size} unique rule(s).`);

let activeRules = loadActiveRules();
let probationRules = loadProbationRules();

// Apply metadata-rich temporal decay before promotion checks
const { kept: survivedRules, toArchive: decayed } = decayRules(activeRules);
if (decayed.length > 0) {
  console.log(`  Decaying ${decayed.length} rule(s) via temporal decay (conf < ${ARCHIVE_THRESHOLD}).`);
  decayed.forEach(r => console.log(`    - [${r.id}] ${r.text} (class:${r.class} conf:${r.conf})`));
  if (!DRY_RUN) archiveReplaced(decayed, []);
}
activeRules = survivedRules;

const { promoted, newProbation, conflicts } = promote(clusters, activeRules, probationRules);

// Evaluate probation rules for archival (stale or conflicting with active)
const { kept: keptProbation, toArchive: archivedProbation } = evaluateProbationArchival(probationRules, activeRules);
if (archivedProbation.length > 0) {
  console.log(`  Archiving ${archivedProbation.length} probation rule(s) (stale/conflict).`);
  archivedProbation.forEach(r => console.log(`    - [${r.id}] ${r.text}`));
  if (!DRY_RUN) archiveReplaced(archivedProbation, []);
}

// Remove promoted rules from probation
const promotedNorms = new Set(promoted.map(r => normalizeRule(r.text)));
probationRules = keptProbation.filter(r => !promotedNorms.has(normalizeRule(r.text)));
// Add new probation entries
probationRules = [...probationRules, ...newProbation];

// Legacy stale decay: catch rules without metadata that haven't been seen in events
const stale = decayStaleRules(activeRules, allEvents);
if (stale.length > 0) {
  console.log(`  Decaying ${stale.length} stale rule(s) (legacy heuristic).`);
  const staleNorms = new Set(stale.map(r => normalizeRule(r.text)));
  activeRules = activeRules.filter(r => !staleNorms.has(normalizeRule(r.text)));
  if (!DRY_RUN) archiveReplaced(stale, []);
}

const hasChanges = promoted.length > 0 || newProbation.length > 0
  || conflicts.length > 0 || stale.length > 0 || decayed.length > 0
  || archivedProbation.length > 0;

if (!hasChanges) {
  console.log('  No rules meet promotion thresholds. Done.');
  process.exit(0);
}

if (conflicts.length > 0) {
  console.log(`  ⚠ ${conflicts.length} conflict(s) detected (skipped):`);
  conflicts.forEach(c => console.log(`    - "${c.rule}" conflicts with [${c.conflictsWith}]: ${c.reason}`));
}

if (newProbation.length > 0) {
  console.log(`  🔄 ${newProbation.length} rule(s) entering probation:`);
  newProbation.forEach(r => console.log(`    - [${r.id}] ${r.text} (conf:${r.conf})`));
}

if (promoted.length > 0) {
  console.log(`  ✅ Promoting ${promoted.length} rule(s) from probation to active:`);
  promoted.forEach(r => console.log(`    - [${r.id}] ${r.text} (reason:${r.promoted_reason} scope:${r.scope} boost:${r.boost})`));
}

if (!DRY_RUN) {
  if (promoted.length > 0) archiveReplaced(activeRules, promoted);
  writeActive(activeRules, promoted);
  writeProbation(probationRules);

  // Write MEMORY.md managed block with active rules (including newly promoted)
  const allActiveRules = loadActiveRules();
  writeMemoryBlock(allActiveRules);

  updateIndex(promoted, conflicts);
  console.log('  Rules written to ACTIVE.md, PROBATION.md, MEMORY.md, index.json updated.');

  // Bridge: queue high-confidence promoted rules for KG import as lesson nodes
  if (promoted.length > 0) {
    const kgPendingPath = path.join(MEMORY_DIR, 'kg_pending.jsonl');
    for (const r of promoted) {
      if ((r.avgConf || 0) >= 0.8) {
        const record = JSON.stringify({
          rule_text: r.text,
          domains: r.domains || [],
          confidence: r.avgConf,
          promoted_at: new Date().toISOString(),
          source_rule_id: r.id,
        });
        fs.appendFileSync(kgPendingPath, record + '\n', 'utf8');
      }
    }
    console.log(`  📦 Queued ${promoted.filter(r => (r.avgConf || 0) >= 0.8).length} rule(s) for KG lesson import.`);
  }
} else {
  console.log('  (dry run — no files written)');
}

console.log('🧬 [Evolver] Done.');
