/** Owner-only installation/configuration service. All host I/O uses public capabilities. */
import { createHash, randomUUID } from 'node:crypto'
import { homedir } from 'node:os'
import { basename, dirname, isAbsolute, join, normalize, parse } from 'node:path'
import { boundedText } from './bounded.mjs'
import { readConfiguration, invalidateConfiguration } from './configuration.mjs'
import { checkKernelConfig } from './kernel-config.mjs'

export const KERNEL_CONFIG_LIMIT = 1024 * 1024
export const KERNEL_WHEEL_LIMIT = 64 * 1024 * 1024
const LOG_LIMIT = 16 * 1024
const FIXED_PYTHONS = ['/usr/bin/python3', '/usr/local/bin/python3']
const hash = bytes => `sha256:${createHash('sha256').update(bytes).digest('hex')}`
const response = (data, status = 200) => new Response(JSON.stringify(data), { status, headers: { 'Content-Type': 'application/json', 'Cache-Control': 'no-store', 'X-Content-Type-Options': 'nosniff' } })
class ManagementError extends Error {
  constructor(code, message, status = 400) { super(message); this.code = code; this.status = status }
}
const reject = (code, message, status) => { throw new ManagementError(code, message, status) }
const object = value => value && typeof value === 'object' && !Array.isArray(value)
const cleanLog = text => String(text).replace(/\x1b\[[0-?]*[ -/]*[@-~]/g, '').replace(/[\x00-\x08\x0b-\x1f\x7f]/g, '').slice(-LOG_LIMIT)
const publicValidation = result => {
  const issues = list => (Array.isArray(list) ? list : []).slice(0, 200).map(item => ({ path: String(item.path ?? '').slice(0, 512), message: String(item.message ?? '').slice(0, 1024) }))
  return { ok: result?.ok === true, errors: issues(result?.errors), warnings: issues(result?.warnings), truncated: (result?.errors?.length ?? 0) > 200 || (result?.warnings?.length ?? 0) > 200 }
}

// Fixed script, never a shell command or wheel-supplied entrypoint. An immutable
// bounded owner-confirmed byte snapshot arrives via stdin. Exclusive mkdir and
// no-follow checks prevent replacing any existing installation. pip is offline.
export const INSTALL_SCRIPT = String.raw`
import base64, hashlib, io, json, os, pathlib, stat, subprocess, sys, venv, zipfile
root, target, expected, filename = sys.argv[1:]
def safe_dir(path, create=False):
    current = pathlib.Path(path.anchor)
    for part in path.parts[1:]:
        current = current / part
        try: info = os.lstat(current)
        except FileNotFoundError:
            if not create: raise
            os.mkdir(current, 0o700)
            info = os.lstat(current)
        if not stat.S_ISDIR(info.st_mode) or stat.S_ISLNK(info.st_mode):
            raise RuntimeError('Managed path must contain only real directories')
root = pathlib.Path(root)
target = pathlib.Path(target)
if target.parent != root or not target.name.startswith('kernel-'):
    raise RuntimeError('Invalid managed target')
safe_dir(root, True)
os.mkdir(target, 0o700)
safe_dir(target)
data = base64.b64decode(sys.stdin.buffer.read(89478490), validate=True)
if len(data) > 67108864 or 'sha256:' + hashlib.sha256(data).hexdigest() != expected:
    raise RuntimeError('Wheel snapshot hash mismatch')
with zipfile.ZipFile(io.BytesIO(data)) as wheel:
    names = wheel.namelist()
    metadata = [name for name in names if name.endswith('.dist-info/METADATA')]
    if len(names) > 20000 or len(metadata) != 1 or not metadata[0].lower().startswith('framivra-'):
        raise RuntimeError('Not a Framivra wheel')
    info = wheel.getinfo(metadata[0])
    if info.file_size > 1048576:
        raise RuntimeError('Oversized wheel metadata')
    import email.parser
    meta = email.parser.BytesParser().parsebytes(wheel.read(metadata[0]))
    if meta.get('Name', '').lower().replace('_', '-') != 'framivra':
        raise RuntimeError('Wheel distribution must be framivra')
venv.EnvBuilder(with_pip=True, symlinks=False).create(target)
wheelpath = target / filename
with open(wheelpath, 'xb') as output:
    output.write(data)
os.chmod(wheelpath, 0o400)
python = target / 'bin' / 'python'
subprocess.run([str(python), '-I', '-m', 'pip', '--isolated', 'install', '--no-index', '--no-deps', '--disable-pip-version-check', str(wheelpath)], check=True, cwd=str(target))
vtx = target / 'bin' / 'vtx'
if not vtx.is_file() or vtx.is_symlink():
    raise RuntimeError('Installed wheel does not provide regular bin/vtx')
print('Framivra installed in independent venv; kernel selection was not changed.', flush=True)
`

async function localTarget(ctx, path, signal) {
  const target = await ctx.fs.resolve(path, { signal })
  const canonical = ctx.fs.processPath(target)
  if (!isAbsolute(canonical) || ctx.fs.processPathFromHostPath(canonical) !== canonical) reject('LOCAL_FS_REQUIRED', '仅支持本机共享路径文件系统', 422)
  return target
}
function absolutePath(path) {
  if (typeof path !== 'string' || path.length > 4096 || !path.startsWith('/') || normalize(path) !== path || /[\x00-\x1f\x7f]/.test(path)) reject('BAD_PATH', '请选择无遍历段的本机绝对路径')
  return path
}
async function rejectLinks(ctx, path, signal, allowMissing = false) {
  if (typeof ctx.fs.lstat !== 'function') reject('FS_UNSUPPORTED', '文件系统必须支持 lstat 才能安全管理配置/安装', 502)
  let current = parse(path).root
  const parts = path.slice(current.length).split('/').filter(Boolean)
  for (let i = 0; i < parts.length; i++) {
    current = join(current, parts[i])
    const info = await ctx.fs.lstat(current, {}, signal)
    if (!info) { if (allowMissing) return; reject('NOT_FOUND', '路径不存在', 404) }
    if (info.type === 'symlink' || (i < parts.length - 1 && info.type !== 'directory')) reject('UNSAFE_PATH', '管理路径不能包含符号链接或非目录父路径', 403)
  }
}
async function rejectWorkspaces(ctx, target, signal) {
  if (typeof ctx.sessions?.list !== 'function' || typeof ctx.workingDirectory?.get !== 'function') reject('SESSION_STORE_UNAVAILABLE', '无法权威枚举会话工作区，已拒绝信任配置或安装路径', 503)
  const sessions = ctx.sessions.list()
  if (!Array.isArray(sessions)) reject('SESSION_STORE_UNAVAILABLE', '会话列表格式不可用，已拒绝信任路径', 503)
  for (const session of sessions) {
    const cwd = ctx.workingDirectory?.get?.(session)
    if (cwd && ctx.fs.contains(await localTarget(ctx, cwd, signal), target)) reject('WORKSPACE_PATH', '配置文件和受管安装不能位于会话工作区内', 403)
  }
}
/** Shared by the management editor AND every kernel spawn. Never accepts a request path. */
export async function validateKernelConfigSelection(ctx, snapshot, signal, { allowMissing = false } = {}) {
  const path = snapshot.vtxConfigFile
  if (!path) return undefined // Explicitly clear ambient VTX_CONFIG at spawn.
  absolutePath(path)
  await rejectLinks(ctx, path, signal, allowMissing)
  const target = await localTarget(ctx, path, signal), parent = await localTarget(ctx, dirname(path), signal)
  if (!ctx.fs.contains(parent, target) || ctx.fs.processPath(target) !== path) reject('UNSAFE_PATH', '配置路径不在独立规范父目录内', 403)
  await rejectWorkspaces(ctx, target, signal)
  if (snapshot.vtxExecutable !== 'vtx') {
    const bin = dirname(snapshot.vtxExecutable)
    const install = basename(bin) === 'bin' ? dirname(bin) : bin
    if (install !== '/' && ctx.fs.contains(await localTarget(ctx, install, signal), target)) reject('INSTALL_CONFIG_PATH', '请使用安装目录外的独立 VTX_CONFIG 文件', 403)
  }
  const info = await ctx.fs.stat(target, signal)
  if (info && info.type !== 'file') reject('NOT_REGULAR', '配置路径必须是普通 JSON 文件')
  if (!info && !allowMissing) reject('NOT_FOUND', '已选 VTX_CONFIG 文件不存在；请先在配置编辑器中明确创建', 404)
  if (info?.size > KERNEL_CONFIG_LIMIT) reject('FILE_TOO_LARGE', '配置文件超过管理上限', 413)
  return { path, target, snapshot, info }
}

/** No tools, sessions or browser-supplied roots are needed to use this service. */
export function createKernelManagement(ctx, config = {}) {
  const home = homedir()
  const managedRoot = join(home, '.local', 'share', 'framivra-dsh', 'kernels')
  const suggestedConfigFile = join(home, '.config', 'framivra', 'config.json')
  const lifecycle = new AbortController()
  const previews = new Map(), jobs = new Map(), active = new Map(), tasks = new Set(), operations = new Set(), selections = new Map()
  let saving = false, disposed = false, resolvePolicy
  const local = (path, signal) => localTarget(ctx, path, signal)
  const absolute = absolutePath
  const noLinks = (path, signal, allowMissing) => rejectLinks(ctx, path, signal, allowMissing)
  const outsideWorkspaces = (target, signal) => rejectWorkspaces(ctx, target, signal)
  function installPolicy() {
    const policy = resolvePolicy?.()
    if (policy?.mode !== 'danger-full-access') reject('INSTALL_POLICY_DENIED', '本地安装仅在 Host 权威默认文件策略明确为 danger-full-access 时开放；当前策略受限或未知。不会申请提权或绕过沙箱。', 403)
    return policy
  }
  async function regular(path, signal, maxBytes) {
    const target = await local(path, signal)
    const info = await ctx.fs.stat(target, signal)
    if (info?.type !== 'file') reject('NOT_REGULAR', '请选择已有普通文件')
    if (info.size !== undefined && info.size > maxBytes) reject('FILE_TOO_LARGE', '文件超过管理界面大小上限', 413)
    return { target, info }
  }
  async function bytes(target, signal, cap) {
    if (typeof ctx.fs.readBytes !== 'function') reject('FS_UNSUPPORTED', '文件系统不支持有界读取', 502)
    const data = await ctx.fs.readBytes(target, signal, cap)
    if (data.byteLength > cap) reject('FILE_TOO_LARGE', '文件超过大小上限', 413)
    return data
  }
  async function configTarget(signal) {
    const snapshot = readConfiguration(config)
    const path = snapshot.vtxConfigFile
    if (!path) reject('CONFIG_FILE_UNSET', '无法权威发现旧内核默认配置。请先在插件设置中指定独立的绝对 .json VTX_CONFIG 文件。', 409)
    return validateKernelConfigSelection(ctx, snapshot, signal, { allowMissing: true })
  }
  async function readConfig(signal) {
    const value = await configTarget(signal)
    const info = await ctx.fs.stat(value.target, signal)
    if (!info) return { ...value, text: '{}\n', hash: 'absent', exists: false, info: undefined }
    if (info.type !== 'file') reject('NOT_REGULAR', '配置路径必须是普通 JSON 文件')
    const data = await bytes(value.target, signal, KERNEL_CONFIG_LIMIT)
    const after = await ctx.fs.stat(value.target, signal)
    if (after?.version !== info.version) reject('CONFIG_CONFLICT', '读取期间配置变化，请重读；草稿不会被覆盖', 409)
    let text
    try { text = new TextDecoder('utf-8', { fatal: true, ignoreBOM: true }).decode(data) } catch { reject('NOT_UTF8', '配置文件必须为 UTF-8 JSON') }
    return { ...value, text, hash: hash(data), exists: true, info }
  }
  function draft(text) {
    if (typeof text !== 'string' || Buffer.byteLength(text) > KERNEL_CONFIG_LIMIT) reject('BAD_CONFIG_TEXT', '配置原文必须为不超过 1 MiB 的 UTF-8 文本', 413)
    return text
  }
  function selectionTokenFor(value) {
    for (const [token, selected] of selections) if (selected.expiresAt <= Date.now()) selections.delete(token)
    if (selections.size >= 64) selections.delete(selections.keys().next().value)
    const token = randomUUID()
    selections.set(token, { path: value.path, fingerprint: value.snapshot.fingerprint, targetKey: value.target.targetKey, hash: value.hash, expiresAt: Date.now() + 30 * 60_000 })
    return token
  }
  function rememberSelection(value) {
    const token = randomUUID()
    for (const [key, selected] of selections) if (selected.expiresAt <= Date.now()) selections.delete(key)
    if (selections.size >= 64) selections.delete(selections.keys().next().value)
    selections.set(token, { path: value.path, fingerprint: value.snapshot.fingerprint, targetKey: value.target.targetKey, hash: value.hash, expiresAt: Date.now() + 30 * 60_000 })
    return token
  }
  async function saveConfig(data, signal) {
    if (saving) reject('CONFIG_BUSY', '另一个配置保存正在进行', 409)
    if (data.confirmed !== true) reject('CONFIRM_REQUIRED', '保存配置需要显式确认')
    draft(data.text)
    saving = true
    try {
      const validation = publicValidation(await checkKernelConfig(data.text))
      if (validation?.ok !== true) return { ok: false, error: 'CONFIG_INVALID', code: 'CONFIG_INVALID', message: '请先修复配置检查错误', validation }
      const current = await readConfig(signal)
      if (data.expectedHash !== current.hash) reject('CONFIG_CONFLICT', '文件与读取时版本不同，请保留草稿并重新读取比较', 409)
      const selection = typeof data.selectionToken === 'string' ? selections.get(data.selectionToken) : undefined
      if (!selection || selection.expiresAt <= Date.now() || selection.path !== current.path || selection.fingerprint !== current.snapshot.fingerprint || selection.targetKey !== current.target.targetKey || selection.hash !== data.expectedHash) reject('CONFIG_SELECTION_CHANGED', '配置目标或读取基线已改变；请保留草稿并重新读取正确目标', 409)
      let backupPath
      if (current.exists) {
        backupPath = `${current.path}.backup-${randomUUID()}.json`
        await noLinks(backupPath, signal, true)
        const backup = await local(backupPath, signal)
        const parent = await local(dirname(current.path), signal)
        if (!ctx.fs.contains(parent, backup)) reject('UNSAFE_PATH', '备份路径越界', 403)
        await ctx.fs.writeText(backup, current.text, { kind: 'createIfAbsent' }, signal)
      }
      // Recheck trusted selection and canonical path after all asynchronous work.
      const latest = await configTarget(signal)
      if (latest.snapshot.fingerprint !== current.snapshot.fingerprint || latest.target.targetKey !== current.target.targetKey) reject('CONFIG_CHANGED', '插件配置选择已改变；保留草稿并重新读取', 409)
      const intendedHash = hash(Buffer.from(data.text))
      try {
        await ctx.fs.writeText(current.target, data.text, current.exists ? { kind: 'replaceIfVersion', version: current.info.version } : { kind: 'createIfAbsent' }, signal)
      } catch (error) {
        // Only the provider's definitive pre-publication failures are safe to
        // call not-applied. Cancellation/I/O errors can race atomic publication.
        if (['FS_STALE_VERSION', 'FS_NOT_OBSERVED', 'FS_SANDBOX_DENIED', 'FS_PERMISSION_DENIED', 'FS_NOT_REGULAR_FILE', 'FS_TOO_LARGE'].includes(error.code)) throw error
        invalidateConfiguration(config)
        return { ok: false, error: 'outcome_unknown', code: 'outcome_unknown', message: '配置保存结果尚未确认；请保留草稿、重新读取文件并核对 intendedHash，不要直接重试保存。', path: current.path, expectedHash: current.hash, intendedHash, backupPath }
      }
      invalidateConfiguration(config)
      selections.delete(data.selectionToken)
      let selectionToken
      // The write is known applied. Renew the editing baseline for consecutive
      // saves; a changed/invalid Host selection must not relabel that write failed.
      try {
        const snapshot = readConfiguration(config)
        if (!disposed && snapshot.vtxConfigFile === current.path) selectionToken = rememberSelection({ ...current, snapshot, hash: intendedHash })
      } catch { /* Published successfully; require an explicit reread instead. */ }
      return { ok: true, path: current.path, hash: intendedHash, backupPath, validation, selectionToken, needsReload: !selectionToken }
    } finally { saving = false }
  }
  async function scan(signal) {
    const snapshot = readConfiguration(config)
    const candidates = [], warnings = [], seen = new Set()
    let probes = 0
    async function candidate(path, source) {
      if (++probes > 180 || seen.has(path)) return
      seen.add(path)
      try {
        const target = await local(path, signal)
        if ((await ctx.fs.stat(target, signal))?.type === 'file') candidates.push({ path, kind: 'executable', source })
      } catch (error) {
        signal.throwIfAborted()
        if (error?.code === 'FS_SANDBOX_DENIED' || error?.code === 'FS_PERMISSION_DENIED') warnings.push(`无法读取候选路径：${path}`)
        else if (error?.code !== 'FS_NOT_FOUND') throw error
      }
    }
    if (snapshot.vtxExecutable !== 'vtx') await candidate(snapshot.vtxExecutable, 'current-setting')
    for (const dir of [...snapshot.trustedPath.split(':').slice(0, 32), join(home, '.local/bin'), join(home, '.cargo/bin'), '/opt/homebrew/bin']) await candidate(join(dir, 'vtx'), 'trusted-or-common-bin')
    const roots = [join(home, '.local/share/pipx/venvs'), join(home, '.local/pipx/venvs'), join(home, '.local/share/uv/tools'), join(home, '.virtualenvs'), managedRoot]
    for (const rootPath of roots) {
      const root = await local(rootPath, signal)
      if ((await ctx.fs.stat(root, signal))?.type !== 'directory') continue
      const listing = await ctx.fs.listDir(root, signal)
      if (listing.length > 24) warnings.push(`目录候选超出上限，仅查看前 24 项：${rootPath}`)
      for (const entry of listing.slice(0, 24)) {
        if (entry.type !== 'directory' || !/^[^/\\\x00-\x1f]+$/.test(entry.name) || entry.name === '.' || entry.name === '..' || !ctx.fs.contains(root, entry.target)) continue
        await candidate(join(rootPath, entry.name, 'bin/vtx'), 'common-venv')
      }
    }
    return { ok: true, candidates, warnings, suggestedConfigFile, managedRoot, truncated: probes > 180 }
  }
  async function fixedPython(signal) {
    for (const path of FIXED_PYTHONS) {
      const target = await local(path, signal)
      if ((await ctx.fs.stat(target, signal))?.type !== 'file') continue
      const resolved = await ctx.subprocess.resolveExecutable(path, { PATH: '/usr/bin:/bin:/usr/local/bin' }, signal)
      if (resolved === path || resolved === ctx.fs.processPath(target)) return resolved
    }
    reject('PYTHON_NOT_FOUND', '未找到固定系统 Python3；请先安装支持 venv 的系统 Python', 424)
  }
  async function preview(data, signal) {
    installPolicy()
    for (const [nonce, item] of previews) if (Date.now() >= item.expiresAt) previews.delete(nonce)
    if (previews.size >= 4) reject('PREVIEW_LIMIT', '安装预览数量已达上限，请等待旧预览过期', 429)
    const wheelPath = absolute(data.wheelPath)
    if (!/^framivra-[A-Za-z0-9_.+!-]+\.whl$/.test(basename(wheelPath))) reject('BAD_WHEEL', '仅支持用户信任的本地 framivra wheel (.whl)')
    await noLinks(wheelPath, signal)
    const { target } = await regular(wheelPath, signal, KERNEL_WHEEL_LIMIT)
    const content = await bytes(target, signal, KERNEL_WHEEL_LIMIT)
    if (content[0] !== 0x50 || content[1] !== 0x4b) reject('BAD_WHEEL', '文件不是 ZIP wheel')
    await noLinks(managedRoot, signal, true)
    const root = await local(managedRoot, signal)
    await outsideWorkspaces(root, signal)
    const nonce = randomUUID(), targetDir = join(managedRoot, `kernel-${randomUUID()}`)
    if (await ctx.fs.stat(await local(targetDir, signal), signal)) reject('TARGET_EXISTS', '独立安装目标已存在', 409)
    const result = { nonce, hash: hash(content), wheelPath, targetDir, pythonPath: await fixedPython(signal), expiresAt: Date.now() + 5 * 60_000 }
    previews.set(nonce, result)
    return { ok: true, ...result, wheelBytes: content.byteLength }
  }
  async function cleanup(handle) {
    if (!active.has(handle)) return
    const running = active.get(handle)
    if (running) return running
    const task = (async () => {
      const controller = new AbortController(), timer = setTimeout(() => controller.abort(), 5000)
      try {
        handle.terminate()
        const empty = await abortable(handle.waitForExit(controller.signal), controller.signal)
        if (empty !== true) reject('PROCESS_CLEANUP_FAILED', '无法确认受管进程组已退出', 502)
        active.delete(handle)
      } finally { clearTimeout(timer) }
    })()
    active.set(handle, task)
    try { await task } catch (error) { active.set(handle, undefined); throw error }
  }
  function jobView(job) {
    const { jobId, state, targetDir, vtxPath, error, startedAt, finishedAt } = job
    const output = job.handle?.collected
    const logs = cleanLog([output?.stdout?.readFrom(0)?.text ?? '', output?.stderr?.readFrom(0)?.text ?? ''].join('\n'))
    return { jobId, state, targetDir, vtxPath, error, logs, startedAt, finishedAt }
  }
  async function install(job, plan) {
    const timeout = setTimeout(() => job.controller.abort(new DOMException('安装超过 5 分钟', 'TimeoutError')), 300_000)
    timeout.unref?.()
    const signal = AbortSignal.any([job.controller.signal, lifecycle.signal])
    let failed = false
    try {
      job.state = 'running'
      await noLinks(plan.wheelPath, signal)
      const { target } = await regular(plan.wheelPath, signal, KERNEL_WHEEL_LIMIT)
      const content = await bytes(target, signal, KERNEL_WHEEL_LIMIT)
      if (hash(content) !== plan.hash) reject('WHEEL_CHANGED', 'wheel 与已确认预览的 hash 不同；未执行安装', 409)
      await noLinks(managedRoot, signal, true)
      const root = await local(managedRoot, signal), destination = await local(plan.targetDir, signal)
      if (!ctx.fs.contains(root, destination) || ctx.fs.processPath(root) !== managedRoot || ctx.fs.processPath(destination) !== plan.targetDir) reject('UNSAFE_PATH', '安装管理目录发生路径变化', 403)
      await outsideWorkspaces(root, signal)
      if (await ctx.fs.stat(destination, signal)) reject('TARGET_EXISTS', '不覆盖已有安装', 409)
      if (await fixedPython(signal) !== plan.pythonPath) reject('PYTHON_CHANGED', '固定 Python 路径已改变，请重新预览', 409)
      signal.throwIfAborted()
      installPolicy() // Re-resolve Host policy at the actual write/spawn boundary.
      const handle = ctx.subprocess.spawn({
        argv: [plan.pythonPath, '-I', '-c', INSTALL_SCRIPT, managedRoot, plan.targetDir, plan.hash, basename(plan.wheelPath)],
        cwd: dirname(plan.pythonPath),
        env: { PATH: '/usr/bin:/bin:/usr/local/bin', VTX_CONFIG: undefined, PYTHONPATH: undefined, PYTHONHOME: undefined, PYTHONDONTWRITEBYTECODE: '1', PYTHONSAFEPATH: '1', PIP_CONFIG_FILE: '/dev/null', PIP_NO_INDEX: '1' },
        stdio: { stdin: { data: Buffer.from(content).toString('base64') }, stdout: { maxBytes: LOG_LIMIT / 2 }, stderr: { maxBytes: LOG_LIMIT / 2 } },
        signal, graceMs: 1000,
      })
      job.handle = handle
      active.set(handle, undefined)
      const outcome = await abortable(handle.done, signal)
      if (outcome.exitCode !== 0) reject('INSTALL_FAILED', `安装进程退出码 ${outcome.exitCode}；独立失败目录保留供检查`, 502)
      await cleanup(handle)
      job.state = 'succeeded'
      job.vtxPath = join(plan.targetDir, 'bin/vtx')
    } catch (error) {
      failed = true
      job.state = signal.aborted ? 'cancelled' : 'failed'
      job.error = { code: error.code ?? (signal.aborted ? 'INSTALL_CANCELLED' : 'INSTALL_FAILED'), message: error.message ?? String(error) }
    } finally {
      clearTimeout(timeout)
      if (job.handle && active.has(job.handle)) {
        try { await cleanup(job.handle) } catch (error) {
          job.state = 'failed'; job.vtxPath = undefined
          job.error = { code: 'PROCESS_CLEANUP_FAILED', message: error.message }
        }
      }
      if (failed) job.vtxPath = undefined
      job.finishedAt = Date.now()
    }
  }
  async function dispatch(data, signal) {
    if (!object(data) || typeof data.op !== 'string') reject('BAD_REQUEST', '请求必须指定 op')
    const fields = {
      scan: [], 'install-preview': ['wheelPath'], 'install-confirm': ['nonce', 'hash'], 'install-status': ['jobId', 'cancel'],
      'config-read': [], 'config-check': ['text'], 'config-save': ['text', 'expectedHash', 'selectionToken', 'confirmed'],
    }
    if (!fields[data.op] || Object.keys(data).some(key => key !== 'op' && !fields[data.op].includes(key))) reject('BAD_REQUEST', '未知操作或请求字段（不接受路径覆盖）')
    if (process.platform === 'win32') reject('PLATFORM_UNSUPPORTED', '内核管理目前仅支持 POSIX；Windows 请手动安装/配置', 422)
    switch (data.op) {
      case 'scan': return scan(signal)
      case 'config-read': {
        const value = await readConfig(signal), selectionToken = rememberSelection(value)
        return { ok: true, path: value.path, text: value.text, hash: value.hash, exists: value.exists, selectionToken }
      }
      case 'config-check': return { ok: true, validation: publicValidation(await checkKernelConfig(draft(data.text))) }
      case 'config-save': return saveConfig(data, signal)
      case 'install-preview': return preview(data, signal)
      case 'install-confirm': {
        installPolicy()
        const plan = previews.get(data.nonce)
        previews.delete(data.nonce) // consume before ANY await, including invalid confirmation
        if (!plan || Date.now() >= plan.expiresAt || data.hash !== plan.hash) reject('BAD_NONCE', '安装确认过期、hash 不符或已被使用', 409)
        if ([...jobs.values()].some(job => ['queued', 'running'].includes(job.state)) || active.size) reject('INSTALL_BUSY', '已有安装或未清理进程，请等待其完成', 409)
        if (jobs.size >= 16) jobs.delete(jobs.keys().next().value)
        const job = { jobId: randomUUID(), state: 'queued', targetDir: plan.targetDir, startedAt: Date.now(), controller: new AbortController() }
        jobs.set(job.jobId, job)
        const task = install(job, plan)
        tasks.add(task); void task.finally(() => tasks.delete(task))
        return { ok: true, jobId: job.jobId }
      }
      case 'install-status': {
        if (data.jobId !== undefined && (typeof data.jobId !== 'string' || !jobs.has(data.jobId))) reject('JOB_NOT_FOUND', '安装任务不存在（主机重载会清空内存状态）', 404)
        if (data.cancel !== undefined && typeof data.cancel !== 'boolean') reject('BAD_REQUEST', 'cancel 必须为布尔值')
        if (data.cancel) {
          if (!data.jobId) reject('BAD_REQUEST', '取消必须指定 jobId')
          const job = jobs.get(data.jobId)
          job.controller.abort()
          if (job.handle && active.has(job.handle)) await cleanup(job.handle)
        }
        return { ok: true, jobs: (data.jobId ? [jobs.get(data.jobId)] : [...jobs.values()]).map(jobView) }
      }
    }
  }
  return {
    setPolicyResolver(resolver) { resolvePolicy = typeof resolver === 'function' ? resolver : undefined },
    fetch(request) {
      const task = (async () => {
      const deadline = new AbortController(), timer = setTimeout(() => deadline.abort(), 30_000)
      const signal = AbortSignal.any([request.signal, lifecycle.signal, deadline.signal])
      try {
        if (disposed) reject('DISPOSED', '插件已卸载', 503)
        if (request.method !== 'POST') reject('METHOD_NOT_ALLOWED', '仅支持 POST', 405)
        let data
        try { data = JSON.parse(await boundedText(request, KERNEL_CONFIG_LIMIT + 64 * 1024, signal)) }
        catch (error) { signal.throwIfAborted(); reject('BAD_REQUEST', error.message?.startsWith('BODY_LIMIT') ? '请求超过上限' : '请求必须为完整 JSON', 400) }
        return response(await dispatch(data, signal))
      } catch (error) {
        const stale = ['FS_STALE_VERSION', 'FS_NOT_OBSERVED'].includes(error.code)
        const code = stale ? 'CONFIG_CONFLICT' : error.code ?? (signal.aborted ? 'ABORTED' : 'MANAGEMENT_FAILED')
        return response({ ok: false, error: code, code, message: stale ? '文件已改变；保存被拒绝，请保留草稿后重新读取' : error.message ?? String(error), suggestedConfigFile }, stale ? 409 : error.status ?? 400)
      } finally { clearTimeout(timer) }
      })()
      operations.add(task)
      task.then(() => operations.delete(task), () => operations.delete(task))
      return task
    },
    async dispose() {
      disposed = true; lifecycle.abort(); previews.clear(); selections.clear()
      for (const job of jobs.values()) job.controller.abort()
      // A provider may finish an atomic publication despite request abort. Never
      // let the old plugin unmount while any old write can still publish.
      await Promise.allSettled([...operations])
      await Promise.allSettled([...tasks])
      const results = await Promise.allSettled([...active.keys()].map(cleanup))
      const failures = results.filter(result => result.status === 'rejected').map(result => result.reason)
      if (failures.length) throw new AggregateError(failures, 'Installation process ranges remain owned')
    },
  }
}

function abortable(promise, signal) {
  return new Promise((resolve, rejectPromise) => {
    const abort = () => rejectPromise(signal.reason ?? new Error('Aborted'))
    signal.addEventListener('abort', abort, { once: true })
    Promise.resolve(promise).then(resolve, rejectPromise).finally(() => signal.removeEventListener('abort', abort))
    if (signal.aborted) { signal.removeEventListener('abort', abort); abort() }
  })
}
