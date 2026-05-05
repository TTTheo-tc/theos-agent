import { useEffect } from 'react'

type SyncTask = () => void | Promise<unknown>

export function useDelayedSync(
  sync: SyncTask,
  delayMs: number,
  options: { focusDelayMs?: number } = {},
) {
  const focusDelayMs = options.focusDelayMs ?? 400

  useEffect(() => {
    let stopped = false
    let running = false
    let timer: number | undefined

    const clearTimer = () => {
      if (timer !== undefined) window.clearTimeout(timer)
      timer = undefined
    }

    const schedule = (ms: number) => {
      clearTimer()
      timer = window.setTimeout(run, ms)
    }

    const run = () => {
      if (stopped || running) return
      running = true

      Promise.resolve(sync())
        .catch(() => undefined)
        .finally(() => {
          running = false
          if (!stopped) schedule(delayMs)
        })
    }

    const onFocus = () => schedule(focusDelayMs)

    schedule(0)
    window.addEventListener('focus', onFocus)

    return () => {
      stopped = true
      clearTimer()
      window.removeEventListener('focus', onFocus)
    }
  }, [sync, delayMs, focusDelayMs])
}
