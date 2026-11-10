/**
 * Fault-injection worker for dsh-image-isolation tests.
 *
 * Deterministically violates the wire protocol in small payloads so the
 * parent-side validation can be exercised without allocating large buffers:
 *  - op 'garbage': sends a non-object, a reply with an unknown id, then a
 *    reply with a non-boolean `ok` for the real id (malformed reply).
 *  - op 'oversized': replies ok:true with a result whose declared byte mass
 *    exceeds the transfer bound (plain object, no actual allocation).
 *  - op 'schema': replies ok:true with an empty {} result for an op whose
 *    schema requires typed fields (op-specific shape violation).
 *  - op 'baderror': replies ok:false with an oversized, wrongly-shaped error
 *    envelope (lowercase code, over-long message).
 *  - anything else: hangs (equivalent to the silent worker).
 */
if (process.send === undefined || process.channel === undefined) process.exit(74)

process.on('message', (message) => {
  if (message === null || typeof message !== 'object') return
  const { id, op } = message
  if (op === 'garbage') {
    process.send('not an object')
    process.send({ id: 424242, ok: true, result: {} })
    process.send({ id, ok: 'true', result: {} })
    return
  }
  if (op === 'oversized') {
    process.send({ id, ok: true, result: { data: { byteLength: 300 * 1024 * 1024 } } })
    return
  }
  if (op === 'schema') {
    // 'prepare' results must be {data: typed buffer, ref: {...}} — {} is a violation.
    process.send({ id, ok: true, result: {} })
    return
  }
  if (op === 'baderror') {
    process.send({
      id,
      ok: false,
      error: {
        code: 'not-a-real-code-échec',
        message: 'x'.repeat(9000),
      },
    })
    return
  }
  // hang: never reply
})
