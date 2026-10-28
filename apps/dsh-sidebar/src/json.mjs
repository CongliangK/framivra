/** One native JSON parser on both sides of the wire; never parse timeline semantics here. */
export class LosslessJSONError extends Error {
  constructor() {
    super('LOSSLESS_JSON_UNSUPPORTED: This runtime/browser lacks native JSON source/rawJSON support. Upgrade it to display complete timeline JSON without changing numbers.')
    this.name = 'LosslessJSONError'
  }
}

export function requireLosslessJSON() {
  let supported = false
  try {
    JSON.parse('9007199254740993', (_key, _value, context) => {
      supported = context?.source === '9007199254740993' &&
        JSON.isRawJSON(JSON.rawJSON(context.source)) &&
        JSON.stringify(JSON.rawJSON(context.source)) === context.source
    })
  } catch { /* Refuse explicitly, never fall back to rounded data. */ }
  if (!supported) throw new LosslessJSONError()
}

export function parseJSON(text) {
  requireLosslessJSON()
  return JSON.parse(text, (_key, value, context) => {
    if (typeof value !== 'number') return value
    // Canonical safe integers remain usable for protocol frame/index validation.
    // Everything else keeps the original token, including decimals/exponents/-0.
    return Number.isSafeInteger(value) && String(value) === context.source
      ? value : JSON.rawJSON(context.source)
  })
}

export const stringifyJSON = (value, space) => JSON.stringify(value, null, space)
