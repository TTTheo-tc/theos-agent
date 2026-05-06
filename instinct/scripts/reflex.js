#!/usr/bin/env node
/**
 * 反射引擎 v3 (Reflex Engine)
 * 任务开始时由 pre-chat hook 调用。
 *
 * 层级结构: instinct/domains/<category>/<domain>.md
 *   - _meta.md    类别说明文件（无 keywords，提供通用知识上下文）
 *   - *.md        具体 domain（有 keywords / skills / context）
 *
 * 匹配逻辑: 按 domain 关键词打分并排序 → 聚合所属 category →
 *           输出 category 上下文 + 各 domain 的 skills + context
 *
 * 用法: node reflex.js "<用户消息>"
 * 输出: 注入 [🧠 Instinct] 的结构化上下文文本（stdout）
 */

const fs   = require('fs');
const path = require('path');
const os   = require('os');

// ── Dream injection guard ─────────────────────────────────────────────────
const DREAM_INJECT_ENABLED = process.env.INSTINCT_DREAM_INJECT !== 'false'
  && process.env.INSTINCT_DREAM_INJECT === 'true';  // default OFF
const DREAM_REFLUX_LEVEL = process.env.INSTINCT_DREAM_REFLUX_LEVEL || 'L0';
const DREAM_L1_ENABLED = DREAM_REFLUX_LEVEL === 'L1' || DREAM_REFLUX_LEVEL === 'L2';

// ── Input ──────────────────────────────────────────────────────────────────
const userInput = process.argv.slice(2).join(' ').toLowerCase().trim();
if (!userInput) process.exit(0);

// ── Paths ──────────────────────────────────────────────────────────────────
const REPO_ROOT   = path.resolve(__dirname, '..', '..');
const DOMAINS_DIR = path.join(REPO_ROOT, 'instinct', 'domains');
const SKILLS_DIR  = path.join(REPO_ROOT, 'skills');
const WORKSPACE   = process.env.THEOS_WORKSPACE
                    || path.join(os.homedir(), '.theos', 'workspace');
const MEMORY_DIR  = path.join(WORKSPACE, 'memory', 'instinct');
const MAX_HITS    = Math.max(1, Number(process.env.INSTINCT_TOP_K || 4));
const MIN_SCORE   = Math.max(0, Number(process.env.INSTINCT_MIN_SCORE || 1));
const HISTORY_SCAN_LIMIT = Math.max(1, Number(process.env.INSTINCT_HISTORY_SCAN_LIMIT || 20));

// ── Parse a markdown file into sections ────────────────────────────────────
// NOTE: `heading` may contain regex metacharacters on purpose.
//   e.g. 'Keywords?' matches both "Keyword" and "Keywords".
function parseSection(text, heading) {
  const m = text.match(new RegExp(`##\\s*${heading}\\s*\\n([\\s\\S]*?)(?=\\n##|$)`, 'i'));
  return m ? m[1].trim() : '';
}

// ── Load all category directories and their domains ───────────────────────
function loadAll() {
  // categories: { name, metaContext, domains: [...] }
  const categories = [];

  if (!fs.existsSync(DOMAINS_DIR)) return categories;

  for (const entry of fs.readdirSync(DOMAINS_DIR, { withFileTypes: true })) {
    if (!entry.isDirectory()) continue;                // skip flat .md files
    const catName = entry.name;
    const catDir  = path.join(DOMAINS_DIR, catName);

    // Read _meta.md for category-level context (optional)
    const metaFile = path.join(catDir, '_meta.md');
    const metaContext = fs.existsSync(metaFile)
      ? parseSection(fs.readFileSync(metaFile, 'utf-8'), 'Context')
      : '';

    const domains = [];
    for (const f of fs.readdirSync(catDir)) {
      if (!f.endsWith('.md') || f === '_meta.md') continue;
      const text   = fs.readFileSync(path.join(catDir, f), 'utf-8');
      const dName  = f.replace('.md', '');
      domains.push({
        name:        dName,
        keywords:    parseSection(text, 'Keywords?')
                       .split(',').map(k => k.trim().toLowerCase()).filter(Boolean),
        skillsText:  parseSection(text, 'Skills?'),
        toolsText:   parseSection(text, 'Tools?'),
        contextText: parseSection(text, 'Context'),
      });
    }

    categories.push({ name: catName, metaContext, domains });
  }
  return categories;
}

// ── Keyword matching ──────────────────────────────────────────────────────
// Single-char keywords require word-boundary (space/punctuation) to avoid
// false positives like "查" matching inside "检查".
// Multi-char keywords (≥2) use plain substring matching — safe for Chinese.
function kwMatches(text, kw) {
  if (kw.length === 1) {
    return new RegExp(`(^|[\\s,，。！？、])(${kw})([\\s,，。！？、]|$)`).test(text)
        || text.startsWith(kw) || text.endsWith(kw);
  }
  return text.includes(kw);
}

function escapeRegExp(s) {
  return s.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
}

function countOccurrences(text, kw, wholeWord = false) {
  if (!kw) return 0;
  if (wholeWord) {
    const re = new RegExp(`\\b${escapeRegExp(kw)}\\b`, 'gi');
    return (text.match(re) || []).length;
  }
  let from = 0;
  let count = 0;
  while (from < text.length) {
    const idx = text.indexOf(kw, from);
    if (idx === -1) break;
    count += 1;
    from = idx + kw.length;
  }
  return count;
}

function isAsciiWordish(kw) {
  return /^[a-z0-9][a-z0-9 _-]*$/i.test(kw);
}

function scoreKeyword(text, kw) {
  if (!kwMatches(text, kw)) return { score: 0, hits: 0 };

  const asciiWordish = isAsciiWordish(kw);
  const wholeHits = asciiWordish ? countOccurrences(text, kw, true) : 0;
  const partialHits = countOccurrences(text, kw, false);
  const hits = Math.max(wholeHits, partialHits);
  if (hits <= 0) return { score: 0, hits: 0 };

  let weight = 1.0;
  if (kw.length === 1) weight = 0.3;
  else if (kw.length === 2) weight = 1.2;
  else if (kw.length <= 3) weight = 1.6;
  else if (kw.length <= 6) weight = 2.2;
  else weight = 2.8;

  if (kw.includes(' ')) weight += 0.6;     // multi-word phrase is more specific
  if (wholeHits > 0) weight += 0.8;        // strict word boundary match is higher quality

  return { score: weight * Math.min(hits, 3), hits };
}

// ── Match + score domains across all categories ──────────────────────────
function matchDomains(categories) {
  const hits = []; // { category, domain, score, matchedKeywords[] }
  for (const cat of categories) {
    for (const dom of cat.domains) {
      const matchedKeywords = [];
      let score = 0;
      for (const kw of [...new Set(dom.keywords)]) {
        const r = scoreKeyword(userInput, kw);
        if (r.score > 0) {
          score += r.score;
          matchedKeywords.push({ kw, score: r.score, hits: r.hits });
        }
      }
      if (score >= MIN_SCORE) {
        matchedKeywords.sort((a, b) => b.score - a.score || b.hits - a.hits || a.kw.localeCompare(b.kw));
        hits.push({ category: cat, domain: dom, score, matchedKeywords });
      }
    }
  }
  hits.sort((a, b) => b.score - a.score || b.matchedKeywords.length - a.matchedKeywords.length || a.domain.name.localeCompare(b.domain.name));
  return hits.slice(0, MAX_HITS);
}

// ── Load gotchas from memory ──────────────────────────────────────────────
function summarizeText(text, max = 220) {
  return text
    .replace(/```[\s\S]*?```/g, ' ')
    .replace(/^#+\s+/gm, '')
    .replace(/\*\*/g, '')
    .replace(/\s+/g, ' ')
    .trim()
    .substring(0, max);
}

function matchesDomainText(text, catName, domName, keywords = []) {
  const lower = (text || '').toLowerCase();
  if (!lower) return false;
  if (lower.includes(catName.toLowerCase()) || lower.includes(domName.toLowerCase())) return true;
  return [...new Set((keywords || []).map(k => k.toLowerCase()).filter(Boolean))]
    .some(kw => kwMatches(lower, kw));
}

function loadGenericLessons(catName, domName, keywords = []) {
  const dir = path.join(MEMORY_DIR, 'lessons');
  if (!fs.existsSync(dir)) return [];

  return fs.readdirSync(dir)
    .filter(f => f.endsWith('.md'))
    .sort()
    .reverse()
    .slice(0, HISTORY_SCAN_LIMIT)
    .map(f => fs.readFileSync(path.join(dir, f), 'utf-8').trim())
    .filter(txt => matchesDomainText(txt, catName, domName, keywords))
    .map(txt => summarizeText(txt));
}

function loadGenericEvents(catName, domName, keywords = []) {
  const dir = path.join(MEMORY_DIR, 'events');
  if (!fs.existsSync(dir)) return [];

  const gotchas = [];
  const domainKey = `${catName}/${domName}`.toLowerCase();
  for (const file of fs.readdirSync(dir).filter(f => f.endsWith('.json')).sort().reverse().slice(0, HISTORY_SCAN_LIMIT)) {
    try {
      const event = JSON.parse(fs.readFileSync(path.join(dir, file), 'utf-8'));
      const routedDomains = (event.routing?.domains || []).map(d => String(d).toLowerCase());
      const hasRoutingMatch = routedDomains.includes(domainKey)
        || routedDomains.some(d => d.endsWith(`/${domName.toLowerCase()}`) || d.startsWith(`${catName.toLowerCase()}/`));
      const haystack = [
        event.request?.raw || '',
        event.request?.intent_summary || '',
        ...(event.generalization?.transferable_rules || []),
      ].join(' ');
      if (!hasRoutingMatch && !matchesDomainText(haystack, catName, domName, keywords)) continue;

      for (const rule of (event.generalization?.transferable_rules || [])) {
        gotchas.push(summarizeText(rule, 160));
      }
      gotchas.push(
        `最近相关任务：${summarizeText(event.request?.intent_summary || event.request?.raw || 'unknown task', 120)}`
      );
      if (event.verification?.has_issues) {
        gotchas.push(
          `最近一次同类任务失败：${summarizeText(event.request?.intent_summary || event.request?.raw || 'unknown task', 120)}`
        );
      }
    } catch (err) {
      console.warn(`[Reflex] Failed to parse event ${file}: ${err.message}`);
    }
  }
  return gotchas;
}

function getGotchas(catName, domName, keywords = []) {
  const keys = [`${catName}/${domName}`, `${catName}`, domName];
  const gotchas = [];
  const seen = new Set();

  function pushGotcha(text) {
    const clean = summarizeText(text);
    if (!clean) return;
    const key = clean.toLowerCase();
    if (seen.has(key)) return;
    seen.add(key);
    gotchas.push(clean);
  }

  for (const key of keys) {
    const dir = path.join(MEMORY_DIR, key);
    if (!fs.existsSync(dir)) continue;
    fs.readdirSync(dir)
      .filter(f => f.endsWith('.md'))
      .forEach(f => {
        const txt = fs.readFileSync(path.join(dir, f), 'utf-8').trim();
        pushGotcha(txt);
      });
  }
  loadGenericLessons(catName, domName, keywords).forEach(pushGotcha);
  loadGenericEvents(catName, domName, keywords).forEach(pushGotcha);
  return gotchas.slice(0, 3);
}

// ── Resolve skill file path ───────────────────────────────────────────────
function skillPath(name) {
  const wsp = path.join(WORKSPACE, 'skills', name, 'SKILL.md');
  if (fs.existsSync(wsp)) return wsp;
  const blt = path.join(SKILLS_DIR, name, 'SKILL.md');
  if (fs.existsSync(blt)) return blt;
  return null;
}

function buildSkillLines(skillsText) {
  return skillsText.split('\n')
    .filter(l => l.trim().startsWith('-'))
    .map(l => {
      const m = l.match(/^-\s+([\w-]+)\s*:(.*)/);
      if (!m) return l;
      const [, name, desc] = m;
      const p = skillPath(name);
      const loc = p ? `  → read: ${p}` : '  → (not installed)';
      return `  - ${name}:${desc}\n${loc}`;
    })
    .join('\n');
}

// ── Load ACTIVE rules for domain boost & risk warnings ───────────────────
const RULES_DIR  = path.join(MEMORY_DIR, 'rules');
const MAX_DOMAIN_BOOST = Number(process.env.INSTINCT_MAX_DOMAIN_BOOST || 3.0);

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
      // I2: new metadata fields (backward-compatible defaults)
      class: meta.class || 'adaptive',
      conf: parseFloat(meta.conf) || 0.7,
      last_seen: meta.last_seen || '',
      review_after: meta.review_after || '',
    });
  }
  return rules;
}

// ── Load active rules from MEMORY.md managed block ──────────────────────
function loadMemoryBlockRules() {
  const memoryPath = path.join(WORKSPACE, 'MEMORY.md');
  if (!fs.existsSync(memoryPath)) return [];
  const content = fs.readFileSync(memoryPath, 'utf-8');
  const startTag = '<!-- theos:instinct:rules:start -->';
  const endTag = '<!-- theos:instinct:rules:end -->';
  const startIdx = content.indexOf(startTag);
  const endIdx = content.indexOf(endTag);
  if (startIdx === -1 || endIdx === -1) return [];
  const block = content.slice(startIdx + startTag.length, endIdx);
  const rules = [];
  for (const line of block.split('\n')) {
    const m = line.match(/^- (.+)$/);
    if (m) rules.push(m[1].trim());
  }
  return rules;
}

// ── Load probation rules for tentative injection ────────────────────────
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
      conf: parseFloat(meta.conf) || 0.7,
    });
  }
  return rules;
}

// ── L1 Dream lookup ──────────────────────────────────────────────────────
function dreamLookup(intentText, maxResults = 3) {
  try {
    if (!DREAM_L1_ENABLED) return [];
    const indexPath = path.join(MEMORY_DIR, 'DREAM_INDEX.jsonl');
    if (!fs.existsSync(indexPath)) return [];
    const lines = fs.readFileSync(indexPath, 'utf-8').split('\n').filter(Boolean);
    const intentLower = (intentText || '').toLowerCase();
    const intentWords = intentLower.split(/\s+/).filter(w => w.length > 2);

    const scored = [];
    for (const line of lines) {
      let entry;
      try { entry = JSON.parse(line); } catch { continue; }
      if (entry.status === 'failed') continue;

      let score = 0;
      // topic substring match
      if (entry.topic && intentLower.includes(entry.topic.toLowerCase())) score += 10;
      // tag matches
      for (const tag of (entry.tags || [])) {
        if (intentLower.includes(tag.toLowerCase())) score += 3;
      }
      // summary word overlap
      const summaryWords = (entry.summary || '').toLowerCase().split(/\s+/).filter(w => w.length > 2);
      for (const sw of summaryWords) {
        if (intentWords.includes(sw)) score += 1;
      }
      // reviewed_by_user bonus
      if (entry.reviewed_by_user) score += 2;

      if (score >= 5) scored.push({ ...entry, _score: score });
    }
    scored.sort((a, b) => b._score - a._score);
    return scored.slice(0, maxResults);
  } catch {
    return [];
  }
}

// ── I4: Tiered output assembly with soft budget ─────────────────────────
function assembleOutput(hits, activeRules) {
  const maxChars = parseInt(process.env.INSTINCT_MAX_CHARS || '8000', 10);
  const lines = [];
  let charCount = 0;

  /** Append a line if budget allows. Returns true if added. */
  function addLine(line) {
    if (charCount + line.length > maxChars) return false;
    lines.push(line);
    charCount += line.length;
    return true;
  }

  /** Append multiple lines, stopping at first budget overflow. */
  function addLines(arr) {
    for (const l of arr) {
      if (!addLine(l)) return false;
    }
    return true;
  }

  /** Emit a single domain hit (header, context, skills). */
  function emitDomain(hit, seenCats) {
    const { category: cat, domain: dom, score, matchedKeywords } = hit;
    if (!addLine(`\n【${cat.name}/${dom.name}】`)) return false;
    addLine(`🔎 score=${score.toFixed(1)}  matched=${matchedKeywords.slice(0, 5).map(m => m.kw).join(', ')}`);
    if (!seenCats.has(cat.name) && cat.metaContext) {
      addLine(`📚 [${cat.name}] ${cat.metaContext}`);
      seenCats.add(cat.name);
    }
    if (dom.contextText) addLine(`💡 ${dom.contextText}`);
    if (dom.skillsText) {
      addLine('推荐 Skills（按需 read_file 加载 SKILL.md）:');
      addLine(buildSkillLines(dom.skillsText));
    }
    return true;
  }

  // ── Header (always) ──────────────────────────────────────────────────
  addLine('🧠 [Instinct] Domain routing activated.\n');
  addLine('【脑干】core.md 已注入系统 prompt（常驻规则）。');

  if (hits.length === 0) {
    addLine('【判断】未匹配已知 Domain → 通用任务，直接执行。');
    addLine('  Skills 目录仍可用，在 system prompt 中查看 <skills> 列表。');
  }

  // ── Tier A: stable rules + top domain (~500 tokens) ──────────────────
  const stableRules = activeRules.filter(r => r.class === 'stable' && r.scope !== 'risk_warn');
  if (stableRules.length > 0) {
    addLine('\n🛡️ Stable Rules:');
    stableRules.forEach(r => addLine(`  • [${r.id}] ${r.text}`));
  }

  const seenCats = new Set();
  if (hits.length > 0) {
    emitDomain(hits[0], seenCats);
    // Top domain gotchas are part of Tier A (always loaded)
    const topDom = hits[0];
    const topGotchas = getGotchas(topDom.category.name, topDom.domain.name, topDom.domain.keywords);
    if (topGotchas.length > 0) {
      addLine(`\n⚠️  历史易错点 (${topGotchas.length} 条):`);
      topGotchas.forEach(g => addLine(`  • ${g}`));
    }
  }

  // ── Tier B: remaining domains + adaptive rules (~500-1000 tokens) ────
  if (hits.length > 1) {
    for (const hit of hits.slice(1)) {
      if (charCount > maxChars * 0.7) break;  // soft cap at 70%
      emitDomain(hit, seenCats);
    }
  }

  // Adaptive rules related to matched domains
  if (hits.length > 0 && charCount < maxChars * 0.7) {
    const matchedDomKeys = hits.map(h => `${h.category.name}/${h.domain.name}`.toLowerCase());
    const adaptiveRules = activeRules.filter(r =>
      r.class === 'adaptive' && r.scope !== 'risk_warn' && r.scope !== 'domain_boost'
      && r.domains.some(d => matchedDomKeys.some(mk => mk.includes(d) || d.includes(mk.split('/').pop())))
    );
    if (adaptiveRules.length > 0) {
      addLine('\n📋 Adaptive Rules (matched domains):');
      for (const r of adaptiveRules) {
        if (charCount > maxChars * 0.8) break;
        addLine(`  • [${r.id}] ${r.text}`);
      }
    }
  }

  // ── Tier C: gotchas for remaining domains + lessons (fill budget) ────
  if (hits.length > 1 && charCount < maxChars * 0.9) {
    for (const hit of hits.slice(1)) {
      if (charCount > maxChars * 0.9) break;
      const gotchas = getGotchas(hit.category.name, hit.domain.name, hit.domain.keywords);
      if (gotchas.length > 0) {
        addLine(`\n⚠️  历史易错点 [${hit.category.name}/${hit.domain.name}] (${gotchas.length} 条):`);
        gotchas.forEach(g => addLine(`  • ${g}`));
      }
    }
  }

  // ── Always: risk warnings (outside budget) ───────────────────────────
  const riskWarns = activeRules.filter(r => r.scope === 'risk_warn');
  if (riskWarns.length > 0) {
    lines.push('\n🛡️ Active Risk Warnings:');
    riskWarns.forEach(r => lines.push(`  ⚠ [${r.id}] ${r.text}`));
  }

  // NOTE: Active rules are already injected via the tiered output above
  // (stable rules, adaptive rules, risk warnings from ACTIVE.md).
  // MEMORY.md managed block is for external tools/users, not reflex injection.

  // ── Tentative probation rules (top 2 by confidence) ──────────────────
  const probRules = loadProbationRules();
  if (probRules.length > 0) {
    const sorted = probRules.sort((a, b) => b.conf - a.conf).slice(0, 2);
    lines.push('\n[🧠 Instinct] Tentative Rules');
    sorted.forEach(r => lines.push(`- [tentative] ${r.text} (probation, conf: ${r.conf})`));
  }

  // ── L1 Dream hints (low-weight, outside budget) ────────────────────────
  const dreamHints = dreamLookup(userInput);
  if (dreamHints.length > 0) {
    lines.push('\n🌙 Dream Hints (low-weight, unverified):');
    for (const hint of dreamHints) {
      const insightsToShow = (hint.insights || []).slice(0, 3);
      if (insightsToShow.length > 0) {
        for (const insight of insightsToShow) {
          lines.push(`  • ${insight} (from: ${hint.session_id})`);
        }
      } else {
        lines.push(`  • ${hint.summary} (from: ${hint.session_id})`);
      }
      lines.push(`  (Review: ${hint.review_path})`);
    }
  }

  // ── Always: I7 structured sidecar (outside budget) ───────────────────
  const extractedSkillNames = [];
  const seenSkills = new Set();
  for (const hit of hits) {
    if (hit.domain.skillsText) {
      for (const line of hit.domain.skillsText.split('\n')) {
        const m = line.match(/^-\s+([\w-]+)\s*:/);
        if (m && !seenSkills.has(m[1])) {
          seenSkills.add(m[1]);
          extractedSkillNames.push(m[1]);
        }
      }
    }
  }
  // Extract tool names from ## Tools section of PRIMARY domain only.
  // Secondary domains' tools stay in the deferred pool — the model can
  // reach them via tool_search if needed.  This keeps the auto-activated
  // tool surface small and focused.
  const extractedToolNames = [];
  if (hits.length > 0 && hits[0].domain.toolsText) {
    for (const name of hits[0].domain.toolsText.split(',')) {
      const trimmed = name.trim().toLowerCase();
      if (trimmed) extractedToolNames.push(trimmed);
    }
  }
  const sidecar = {
    domains: hits.map(h => `${h.category.name}/${h.domain.name}`),
    skills: extractedSkillNames,
    tools: extractedToolNames,
    selected_primary: hits.length > 0 ? `${hits[0].category.name}/${hits[0].domain.name}` : null,
  };
  lines.push('');
  lines.push(`<!-- instinct-routing:${JSON.stringify(sidecar)} -->`);

  return lines.join('\n');
}

// ── Main ──────────────────────────────────────────────────────────────────
const all     = loadAll();
const hits    = matchDomains(all);
const activeRules = loadActiveRules();

// Apply domain_boost from ACTIVE rules to matched domain scores
for (const hit of hits) {
  const domKey = `${hit.category.name}/${hit.domain.name}`;
  for (const rule of activeRules) {
    if (rule.scope === 'domain_boost' && rule.domains.some(d => domKey.includes(d) || d.includes(hit.domain.name))) {
      const boost = Math.min(rule.boost, MAX_DOMAIN_BOOST);
      hit.score += boost;
    }
  }
}
// Re-sort after boost
hits.sort((a, b) => b.score - a.score);

console.log(assembleOutput(hits, activeRules));
