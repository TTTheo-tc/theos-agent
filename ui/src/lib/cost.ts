import type { CostBreakdown } from './types'

export const PRICING: Record<string, { input: number; output: number; cacheRead: number }> = {
  'claude-sonnet-4-20250514': { input: 3.0, output: 15.0, cacheRead: 0.30 },
  'claude-4-5-sonnet': { input: 3.0, output: 15.0, cacheRead: 0.30 },
  'claude-3-5-sonnet': { input: 3.0, output: 15.0, cacheRead: 0.30 },
  'claude-3-5-haiku': { input: 0.80, output: 4.0, cacheRead: 0.08 },
  'claude-3-haiku': { input: 0.25, output: 1.25, cacheRead: 0.03 },
  'claude-3-opus': { input: 15.0, output: 75.0, cacheRead: 1.50 },
  'claude-opus-4-6': { input: 15.0, output: 75.0, cacheRead: 1.50 },
  'gpt-4o': { input: 2.5, output: 10.0, cacheRead: 1.25 },
  'gpt-4o-mini': { input: 0.15, output: 0.60, cacheRead: 0.075 },
  'deepseek-chat': { input: 0.27, output: 1.10, cacheRead: 0.07 },
  'deepseek-reasoner': { input: 0.55, output: 2.19, cacheRead: 0.14 },
  'gemini-2.5-pro': { input: 1.25, output: 10.0, cacheRead: 0.31 },
  'gemini-2.5-flash': { input: 0.15, output: 0.60, cacheRead: 0.04 },
  'moonshot-v1': { input: 0.55, output: 0.55, cacheRead: 0.14 },
  'qwen-max': { input: 1.60, output: 6.40, cacheRead: 0.40 },
}

const DEFAULT_PRICING = { input: 3.0, output: 15.0, cacheRead: 0.30 }

function matchPricing(model: string) {
  if (!model) return DEFAULT_PRICING
  const ml = model.toLowerCase()
  for (const [key, pricing] of Object.entries(PRICING)) {
    if (ml.includes(key) || key.includes(ml)) return pricing
  }
  if (ml.includes('haiku')) return PRICING['claude-3-5-haiku'] ?? DEFAULT_PRICING
  if (ml.includes('opus')) return PRICING['claude-3-opus'] ?? DEFAULT_PRICING
  return DEFAULT_PRICING
}

export function estimateCost(
  model: string,
  inputTokens: number,
  outputTokens: number,
  cacheReadTokens = 0,
): CostBreakdown {
  const pricing = matchPricing(model)
  const inputCost = (inputTokens / 1_000_000) * pricing.input
  const outputCost = (outputTokens / 1_000_000) * pricing.output
  const cacheCost = (cacheReadTokens / 1_000_000) * pricing.cacheRead
  return {
    inputCost: Math.round(inputCost * 1e6) / 1e6,
    outputCost: Math.round(outputCost * 1e6) / 1e6,
    cacheCost: Math.round(cacheCost * 1e6) / 1e6,
    totalCost: Math.round((inputCost + outputCost + cacheCost) * 1e6) / 1e6,
  }
}
