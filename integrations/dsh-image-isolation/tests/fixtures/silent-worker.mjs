/**
 * Test-only worker that acknowledges jobs and never replies, holding its slot
 * open so pool timeout, cancellation, disposal, and queue-bound behavior can
 * be exercised deterministically. Never shipped.
 */
process.on('message', () => { /* deliberately silent */ })
