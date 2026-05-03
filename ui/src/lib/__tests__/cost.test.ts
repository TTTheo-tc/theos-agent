import { describe, expect, test } from 'vitest'
import { estimateCost } from '../cost'

describe('estimateCost', () => {
  test('calculates claude sonnet cost correctly', () => {
    const result = estimateCost('claude-sonnet-4-20250514', 1_000_000, 500_000, 200_000)
    expect(result.inputCost).toBeCloseTo(3.0, 4)
    expect(result.outputCost).toBeCloseTo(7.5, 4)
    expect(result.cacheCost).toBeCloseTo(0.06, 4)
    expect(result.totalCost).toBeCloseTo(10.56, 4)
  })

  test('calculates claude opus cost correctly', () => {
    const result = estimateCost('claude-3-opus', 100_000, 50_000, 0)
    expect(result.inputCost).toBeCloseTo(1.5, 4)
    expect(result.outputCost).toBeCloseTo(3.75, 4)
    expect(result.totalCost).toBeCloseTo(5.25, 4)
  })

  test('falls back to default pricing for unknown model', () => {
    const result = estimateCost('unknown-model', 1_000_000, 0, 0)
    expect(result.inputCost).toBeCloseTo(3.0, 4)
  })

  test('returns zero for zero tokens', () => {
    const result = estimateCost('claude-sonnet-4-20250514', 0, 0, 0)
    expect(result.totalCost).toBe(0)
  })
})
