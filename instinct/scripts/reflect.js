#!/usr/bin/env node
/**
 * 反思引擎 v2 (Reflection Engine)
 *
 * Two modes:
 *   Legacy:  node reflect.js --domain X --gotcha "text"
 *   New:     node reflect.js --mode post-task   (reads JSON from stdin)
 *
 * Post-task mode reads task context JSON from stdin,
 * then writes:
 *   events/<ts>-<session>.json   — structured reflection event
 *   lessons/<ts>-<session>.md    — human-readable lesson
 *   rules/CANDIDATES.md          — appends transferable rule candidates
 *
 * Kill switch: INSTINCT_REFLECT_ENABLED=false
 */
const fs   = require('fs');
const path = require('path');
const os   = require('os');

const WORKSPACE  = process.env.THEOS_WORKSPACE
                   || path.join(os.homedir(), '.theos', 'workspace');
const MEMORY_DIR = path.join(WORKSPACE, 'memory', 'instinct');

// ── Helpers ───────────────────────────────────────────────────────────────

function ensureDir(dir) {
  if (!fs.existsSync(dir)) fs.mkdirSync(dir, { recursive: true });
}

function safeFilename(s) {
  return s.replace(/[^a-zA-Z0-9_-]/g, '_').substring(0, 60);
}

function nowISO() { return new Date().toISOString(); }

function nowFileStamp() {
  return new Date().toISOString().replace(/[:.]/g, '-');
}

/** Strip markdown code blocks and excessive whitespace. */
function stripCode(text) {
  return text.replace(/```[\s\S]*?```/g, '').replace(/\s+/g, ' ').trim();
}

/** First sentence or first N chars, whichever is shorter. */
function firstSentence(text, max = 200) {
  const clean = stripCode(text);
  const m = clean.match(/^(.+?[。.!！?？\n])/);
  const sent = m ? m[1].trim() : clean.substring(0, max);
  return sent.substring(0, max);
}

function normalizeText(value) {
  return typeof value === 'string' ? value.trim() : '';
}

function toStringArray(value) {
  if (!Array.isArray(value)) return [];
  return value.map(v => typeof v === 'string' ? v.trim() : '').filter(Boolean);
}

function uniqueStrings(values) {
  return [...new Set((values || []).filter(Boolean))];
}

// ── Demand classification (keyword heuristic) ────────────────────────────

const DEMAND_RULES = [
  { cls: 'code_fix',  kw: ['fix', 'bug', 'error', 'issue', '修复', '问题', '错误', 'debug', 'broken'] },
  { cls: 'feature',   kw: ['feature', 'implement', 'add', 'create', 'build', '实现', '添加', '功能', '新增'] },
  { cls: 'analysis',  kw: ['analyze', 'understand', 'explain', 'review', '分析', '理解', '解读', '解释', '审查'] },
  { cls: 'search',    kw: ['search', 'find', 'look', 'where', '搜索', '查找', '在哪'] },
  { cls: 'ops',       kw: ['deploy', 'config', 'setup', 'install', 'run', '部署', '配置', '安装', '运行'] },
  { cls: 'refactor',  kw: ['refactor', 'clean', 'reorganize', '重构', '优化', '整理'] },
];

function classifyDemand(text) {
  const lower = text.toLowerCase();
  let best = { cls: 'other', score: 0 };
  for (const { cls, kw } of DEMAND_RULES) {
    const score = kw.filter(k => lower.includes(k)).length;
    if (score > best.score) best = { cls, score };
  }
  return best.cls;
}

// ── Extract transferable rules from response ─────────────────────────────

const RULE_PATTERNS = [
  /(?:注意|Note|Always|Never|建议|推荐|记住|Remember)[：:\s](.{15,120}?[.。！!？?\n])/gi,
  /当\s*(.{5,40})\s*时[，,]\s*(?:优先|应该|需要|建议)(.{10,80}?[.。！!？?\n])/g,
  /(?:When|If)\s+(.{10,60}),\s*(?:always|should|prefer|make sure)(.{10,80}?[.。！!？?\n])/gi,
];

function extractRules(text) {
  const clean = stripCode(text);
  const rules = [];
  const seen = new Set();
  for (const pat of RULE_PATTERNS) {
    pat.lastIndex = 0;
    let m;
    while ((m = pat.exec(clean)) !== null) {
      const rule = m[0].trim().substring(0, 150);
      const key = rule.toLowerCase();
      if (!seen.has(key) && rule.length >= 15) {
        seen.add(key);
        rules.push(rule);
      }
      if (rules.length >= 3) return rules;
    }
  }
  return rules;
}

// ── Quality filter for transferable rules (I1) ───────────────────────────

function isHardExclusion(rule) {
  // Task status ("completed X", "finished X", "完成了")
  if (/(?:completed?|finished|done|完成了|已完成)\s/i.test(rule)) return true;
  // Single bug ("fixed a null", "修了一个")
  if (/(?:fix(?:ed)?\s+(?:a|the|一个)|修了)\s/i.test(rule)) return true;
  // TODO items
  if (/^(?:todo|fixme|hack|note)[\s:]/i.test(rule)) return true;
  // Pure tool usage ("used grep", "ran pytest")
  if (/(?:used|ran|executed|调用了)\s+\w+/i.test(rule)) return true;
  return false;
}

function passesV1Checks(rule) {
  // 1. isOneTimeEvent: contains date/version/commit hash → one-time
  if (/\b\d{4}-\d{2}-\d{2}\b/.test(rule)) return false;  // date
  if (/\b[0-9a-f]{7,12}\b/.test(rule)) return false;       // commit hash
  if (/v\d+\.\d+/i.test(rule)) return false;               // version

  // 2. hasActionableStatement: contains conditional/action pattern
  const hasAction = /(?:when|if|always|never|should|must|当|如果|必须|不要|避免)/i.test(rule);
  if (!hasAction) return false;

  return true;
}

function filterTransferableRules(rawRules) {
  return rawRules.filter(rule => !isHardExclusion(rule) && passesV1Checks(rule));
}

// ── Extract file paths as artifacts ──────────────────────────────────────

function extractArtifacts(text) {
  const matches = text.match(/(?:src|tests?|lib|hooks|instinct|skills)\/[\w./-]+/g) || [];
  return [...new Set(matches)].slice(0, 10);
}

// ── Build structured event ───────────────────────────────────────────────

function buildEvent(input) {
  const {
    session_key,
    response,
    error,
    status,
    user_message,
    tools_used,
    usage,
    duration_ms,
    routing_domains,
    selected_primary,
    artifacts,
    tests,
  } = input;
  const ts = nowISO();
  const normalizedStatus = normalizeText(status) || (error ? 'failed' : 'success');
  const responseText = normalizeText(response);
  const userMessage = normalizeText(user_message);
  const taskText = userMessage || responseText || normalizeText(error);
  const toolNames = toStringArray(tools_used);
  const routingDomains = toStringArray(routing_domains);
  const explicitArtifacts = toStringArray(artifacts);
  const extractedArtifacts = extractArtifacts(responseText);
  const artifactList = uniqueStrings([...explicitArtifacts, ...extractedArtifacts]).slice(0, 20);
  const explicitTests = toStringArray(tests);
  const inferredTests = artifactList.filter(item => /^tests?\//.test(item));
  const testList = uniqueStrings([...explicitTests, ...inferredTests]).slice(0, 10);
  const usageData = usage && typeof usage === 'object' && !Array.isArray(usage) ? usage : {};
  const hasIssues = !!error || normalizedStatus !== 'success';
  const confidence = hasIssues ? 0.45 : 0.75;
  const rawRules = extractRules(responseText);
  const rules = filterTransferableRules(rawRules);

  return {
    version: 'stable',
    task_id: `${session_key}#${ts.replace(/[:.]/g, '-')}`,
    session_key,
    timestamp: ts,
    request: {
      raw: userMessage || '(not available in post-chat)',
      demand_class: classifyDemand(taskText),
      intent_summary: firstSentence(taskText),
    },
    routing: {
      domains: routingDomains,
      selected_primary: typeof selected_primary === 'string' && selected_primary
        ? selected_primary
        : (routingDomains[0] || null),
    },
    generation: {
      plan_pattern: '',
      effective_tricks: [],
      skills_used: [],
      tools_used: toolNames,
    },
    verification: {
      has_issues: hasIssues,
      issue_types: error ? ['runtime_error'] : (hasIssues ? [normalizedStatus] : []),
      fix_rounds: 0,
    },
    generalization: {
      transferable_rules: rules,
      cross_task_risks: [],
      confidence,
    },
    outcome: {
      status: normalizedStatus,
      artifacts: artifactList,
      tests: testList,
      cost_hint: { tool_calls: toolNames.length, llm_rounds: 0 },
      usage: usageData,
      duration_ms: typeof duration_ms === 'number' ? duration_ms : null,
    },
  };
}

// ── Write event + lesson + candidates ────────────────────────────────────

function writeEvent(event) {
  const dir = path.join(MEMORY_DIR, 'events');
  ensureDir(dir);
  const fname = `${nowFileStamp()}-${safeFilename(event.session_key)}.json`;
  fs.writeFileSync(path.join(dir, fname), JSON.stringify(event, null, 2) + '\n');
  return fname;
}

function writeLesson(event) {
  const dir = path.join(MEMORY_DIR, 'lessons');
  ensureDir(dir);
  const fname = `${nowFileStamp()}-${safeFilename(event.session_key)}.md`;
  const lines = [
    `# Lesson — ${event.timestamp}`,
    '',
    `**Session:** ${event.session_key}`,
    `**Status:** ${event.outcome.status}`,
    `**Demand:** ${event.request.demand_class}`,
    '',
  ];
  if (event.request.raw && event.request.raw !== '(not available in post-chat)') {
    lines.push('## User Request');
    lines.push(event.request.raw);
    lines.push('');
  }
  lines.push('## Summary');
  lines.push(event.request.intent_summary);
  lines.push('');
  if ((event.generation.tools_used || []).length > 0) {
    lines.push('## Tools');
    event.generation.tools_used.forEach(t => lines.push(`- ${t}`));
    lines.push('');
  }
  if (event.generalization.transferable_rules.length > 0) {
    lines.push('## Transferable Rules');
    event.generalization.transferable_rules.forEach(r => lines.push(`- ${r}`));
    lines.push('');
  }
  if (event.outcome.artifacts.length > 0) {
    lines.push('## Artifacts');
    event.outcome.artifacts.forEach(a => lines.push(`- ${a}`));
    lines.push('');
  }
  if (event.verification.has_issues) {
    lines.push('## Issues');
    lines.push(`Types: ${event.verification.issue_types.join(', ')}`);
    lines.push('');
  }
  fs.writeFileSync(path.join(dir, fname), lines.join('\n'));
  return fname;
}

function appendCandidates(event) {
  const rules = event.generalization.transferable_rules;
  if (rules.length === 0) return;

  const dir = path.join(MEMORY_DIR, 'rules');
  ensureDir(dir);
  const fpath = path.join(dir, 'CANDIDATES.md');

  const header = '# Candidate Rules\n\nRules pending promotion to ACTIVE.\n\n';
  let content = fs.existsSync(fpath) ? fs.readFileSync(fpath, 'utf-8') : header;
  if (!content.trim()) content = header;

  const entry = [
    `### ${event.timestamp} — ${event.session_key}`,
    `confidence: ${event.generalization.confidence} | demand: ${event.request.demand_class}`,
    '',
  ];
  rules.forEach(r => entry.push(`- ${r}`));
  entry.push('');

  fs.writeFileSync(fpath, content + entry.join('\n') + '\n');
}

function appendLiveRules(event) {
  const rules = event.generalization.transferable_rules;
  if (rules.length === 0) return;

  const fpath = path.join(MEMORY_DIR, 'live_rules.jsonl');
  const origin = event.outcome.status === 'success' ? 'success' : 'failed';

  for (const rule of rules) {
    const record = {
      text: rule,
      origin,
      confidence: event.generalization.confidence,
      demand_class: event.request.demand_class,
      session_key: event.session_key,
      task_id: event.task_id,
      timestamp: event.timestamp,
      domains: event.routing.domains || [],
      artifacts: event.outcome.artifacts || [],
      tests: event.outcome.tests || [],
    };
    fs.appendFileSync(fpath, JSON.stringify(record) + '\n', 'utf8');
  }
}

// ── Post-task mode (new) ─────────────────────────────────────────────────

function runPostTask() {
  let raw = '';
  try { raw = fs.readFileSync('/dev/stdin', 'utf-8'); } catch { return; }
  if (!raw.trim()) return;

  let input;
  try { input = JSON.parse(raw); } catch { return; }

  const event = buildEvent(input);
  const eventFile = writeEvent(event);
  // I6: reflect.js is now the single owner for lessons and candidate rules.
  // Keep reflector_active in the payload for backward compatibility only.
  const lessonFile = writeLesson(event);
  appendCandidates(event);
  appendLiveRules(event);

  console.log(`✅ [Reflector] event=${eventFile} lesson=${lessonFile} rules=${event.generalization.transferable_rules.length}`);
}

// ── Legacy mode (--domain X --gotcha Y) ──────────────────────────────────

function runLegacy(args) {
  const domainIndex = args.indexOf('--domain');
  const gotchaIndex = args.indexOf('--gotcha');
  if (domainIndex === -1 || gotchaIndex === -1) {
    console.log('Usage: node reflect.js --domain <domain> --gotcha "<lesson>"');
    console.log('       node reflect.js --mode post-task < stdin_json');
    return;
  }

  const domain = args[domainIndex + 1];
  const gotcha = args[gotchaIndex + 1];
  const memoryDir = path.join(MEMORY_DIR, domain);
  ensureDir(memoryDir);

  const filepath = path.join(memoryDir, `gotcha-${nowFileStamp()}.md`);
  fs.writeFileSync(filepath, `- ${gotcha}\n`);
  console.log(`✅ [Reflector] gotcha saved: ${filepath}`);
}

// ── Main ─────────────────────────────────────────────────────────────────

if (process.env.INSTINCT_REFLECT_ENABLED === 'false') {
  process.exit(0);
}

const args = process.argv.slice(2);
const modeIndex = args.indexOf('--mode');

if (modeIndex > -1 && args[modeIndex + 1] === 'post-task') {
  runPostTask();
} else {
  runLegacy(args);
}
