/** Shared client-side classification of TERMINAL preview-grant failures.
 *
 * The Host contract (src/host.mjs grantFor/validateTimeline) fails these exact
 * codes when a grant can no longer serve ANY op except explicit cleanup:
 *   - BAD_GRANT      grant expired (fixed Host TTL), closed, another Session, or workspace changed
 *   - CONFIG_CHANGED Host plugin configuration changed; grant AND write permit were revoked
 *   - PATH_CHANGED   workspace / timeline identity changed
 * Every later poll or action against the same grant id can only repeat the
 * same failure, so the client must STOP retrying and require an explicit
 * reopen. Unknown error codes are deliberately NOT classified as terminal.
 * Older hosts that use BAD_GRANT for unknown operations are rejected by the
 * build handshake before these grant-bound tools mount; Host security is unchanged. */
const TERMINAL_GRANT_PATTERN = /^(BAD_GRANT|CONFIG_CHANGED|PATH_CHANGED):/
/** Returns the terminal grant code carried by a client-side Error.message, or undefined. */
export function terminalGrantFailure(message) {
  return typeof message === 'string' ? TERMINAL_GRANT_PATTERN.exec(message)?.[1] : undefined
}
/** Single actionable banner for a dead grant; the code is appended by callers. */
export const GRANT_DEAD_MESSAGE = '预览授权已失效（过期 / 已关闭 / 宿主配置或工作区变化）。当前显示的是过期只读快照；生成帧、引用、草稿、手动写入与提案确认已全部停用。请点击「刷新保存版本」或重新打开此时间线后继续；不会自动续期、重新渲染、提交或发送。'
