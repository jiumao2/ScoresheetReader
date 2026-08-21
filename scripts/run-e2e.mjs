import { spawn, spawnSync } from 'node:child_process';
import { existsSync, mkdtempSync, readFileSync, rmSync } from 'node:fs';
import { createServer } from 'node:net';
import { homedir, tmpdir } from 'node:os';
import { fileURLToPath } from 'node:url';
import path from 'node:path';

const repositoryRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const frontendRoot = path.join(repositoryRoot, 'frontend');
const backendRoot = path.join(repositoryRoot, 'backend');
const e2eDataDir = mkdtempSync(path.join(tmpdir(), 'scoresheet-reader-e2e-'));
const resultPath = path.join(e2eDataDir, 'results.json');
const artifactPath = path.join(e2eDataDir, 'playwright-artifacts');
const children = [];

function resolvePython() {
  if (process.env.SCORESHEET_PYTHON) return process.env.SCORESHEET_PYTHON;
  const executable = process.platform === 'win32' ? 'python.exe' : 'python';
  const candidates = [
    process.env.CONDA_PREFIX ? path.join(process.env.CONDA_PREFIX, executable) : null,
    process.platform === 'win32'
      ? path.join(homedir(), 'anaconda3', 'envs', 'scoresheet-reader', executable)
      : null,
    process.platform === 'win32'
      ? path.join(homedir(), 'miniconda3', 'envs', 'scoresheet-reader', executable)
      : null,
  ].filter(Boolean);
  return candidates.find((candidate) => existsSync(candidate)) ?? executable;
}

function reserveFreePort() {
  return new Promise((resolve, reject) => {
    const server = createServer();
    server.unref();
    server.once('error', reject);
    server.listen(0, '127.0.0.1', () => {
      const address = server.address();
      if (!address || typeof address === 'string') {
        server.close();
        reject(new Error('无法分配本地测试端口。'));
        return;
      }
      server.close((error) => error ? reject(error) : resolve(address.port));
    });
  });
}

const python = resolvePython();
const backendPort = Number(
  process.env.SCORESHEET_E2E_BACKEND_PORT ?? await reserveFreePort(),
);
let frontendPort = Number(
  process.env.SCORESHEET_E2E_FRONTEND_PORT ?? await reserveFreePort(),
);
while (!process.env.SCORESHEET_E2E_FRONTEND_PORT && frontendPort === backendPort) {
  frontendPort = Number(await reserveFreePort());
}
if (backendPort === frontendPort) throw new Error('前后端测试端口不能相同。');

const viteCli = path.join(repositoryRoot, 'node_modules', 'vite', 'bin', 'vite.js');
const playwrightCli = path.join(repositoryRoot, 'node_modules', '@playwright', 'test', 'cli.js');
const suppliedTemplate = path.join(repositoryRoot, 'scoresheet_template.pdf');
const fallbackTemplate = path.join(e2eDataDir, 'e2e-template.pdf');

function resolveTemplate() {
  if (existsSync(suppliedTemplate)) return suppliedTemplate;
  const generated = spawnSync(
    python,
    [
      '-c',
      "import os; from pypdf import PdfWriter; writer = PdfWriter(); writer.add_blank_page(width=595.32, height=842.04); writer.write(os.environ['SCORESHEET_E2E_TEMPLATE'])",
    ],
    {
      cwd: backendRoot,
      env: { ...process.env, SCORESHEET_E2E_TEMPLATE: fallbackTemplate },
      stdio: 'inherit',
    },
  );
  if (generated.status !== 0) {
    throw new Error(`无法生成公开测试用空白模板，退出码 ${generated.status ?? 'unknown'}。`);
  }
  return fallbackTemplate;
}

function launch(command, args, options) {
  const child = spawn(command, args, {
    stdio: ['ignore', 'inherit', 'inherit'],
    ...options,
  });
  child.launchError = null;
  child.once('error', (error) => {
    child.launchError = error;
  });
  children.push(child);
  return child;
}

function waitForChildClose(child, timeoutMs = 5_000) {
  if (child.exitCode !== null) return Promise.resolve(child.exitCode);
  return new Promise((resolve) => {
    const timeout = setTimeout(() => resolve(child.exitCode), timeoutMs);
    child.once('close', (code) => {
      clearTimeout(timeout);
      resolve(code);
    });
  });
}

async function stop(child) {
  if (!child.pid || child.exitCode !== null) return;
  if (process.platform === 'win32') {
    spawnSync('taskkill', ['/pid', String(child.pid), '/T', '/F'], { stdio: 'ignore' });
  } else {
    child.kill('SIGTERM');
  }
  await waitForChildClose(child);
}

async function cleanup() {
  for (const child of [...children].reverse()) await stop(child);
}

async function cleanupData() {
  const resolvedDataDir = path.resolve(e2eDataDir);
  const resolvedTempDir = path.resolve(tmpdir());
  if (
    resolvedDataDir.startsWith(`${resolvedTempDir}${path.sep}`)
    && path.basename(resolvedDataDir).startsWith('scoresheet-reader-e2e-')
  ) {
    for (let attempt = 0; attempt < 40; attempt += 1) {
      try {
        rmSync(resolvedDataDir, { recursive: true, force: true, maxRetries: 2, retryDelay: 100 });
        return;
      } catch (error) {
        if (!['EPERM', 'EBUSY', 'ENOTEMPTY'].includes(error?.code)) throw error;
        if (attempt === 39) {
          console.warn(`[e2e-runner] Temporary data is still locked and was retained: ${resolvedDataDir}`);
          return;
        }
        await new Promise((resolve) => setTimeout(resolve, 500));
      }
    }
  }
}

function verifyPlaywrightReport() {
  if (!existsSync(resultPath)) throw new Error('Playwright 未生成 JSON 报告。');
  const report = JSON.parse(readFileSync(resultPath, 'utf8'));
  const stats = report.stats ?? {};
  const executed = ['expected', 'unexpected', 'flaky']
    .reduce((sum, key) => sum + Number(stats[key] ?? 0), 0);
  if (executed === 0) throw new Error('Playwright 未执行任何非跳过测试，拒绝将报告视为成功。');
  if ((report.errors?.length ?? 0) > 0) {
    throw new Error(`Playwright 报告包含 ${report.errors.length} 个顶层错误。`);
  }
  if (Number(stats.unexpected ?? 0) !== 0) {
    throw new Error(`Playwright 有 ${stats.unexpected} 个非预期失败。`);
  }
}

function waitForExit(child) {
  if (child.exitCode !== null) return Promise.resolve(child.exitCode);
  return new Promise((resolve, reject) => {
    child.once('error', reject);
    child.once('close', (code) => resolve(code ?? 1));
  });
}

async function waitFor(url, child, timeoutMs = 30_000) {
  const started = Date.now();
  while (Date.now() - started < timeoutMs) {
    if (child.exitCode !== null) {
      throw new Error(`${url} 的服务提前退出，退出码 ${child.exitCode}。`);
    }
    if (child.launchError) throw child.launchError;
    try {
      const response = await fetch(url);
      if (response.ok) return;
    } catch {
      // The service is still starting.
    }
    await new Promise((resolve) => setTimeout(resolve, 200));
  }
  throw new Error(`等待 ${url} 启动超时。`);
}

async function handleSignal(code) {
  await cleanup();
  await cleanupData();
  process.exit(code);
}

process.once('SIGINT', () => void handleSignal(130));
process.once('SIGTERM', () => void handleSignal(143));

let exitCode = 1;
try {
  const templatePath = resolveTemplate();
  const backend = launch(python, ['-m', 'scoresheet_reader.main'], {
    cwd: backendRoot,
    env: {
      ...process.env,
      SCORESHEET_DATA_DIR: e2eDataDir,
      SCORESHEET_TEMPLATE_PATH: templatePath,
      SCORESHEET_MASTER_FIXTURE_PATH: path.join(repositoryRoot, 'shared', 'demo_master_data.json'),
      SCORESHEET_RECOGNITION_MODE: 'mock',
      SCORESHEET_PORT: String(backendPort),
      RUN_QWEN_LIVE: '0',
    },
  });
  const serviceEnvironment = {
    ...process.env,
    SCORESHEET_FRONTEND_PORT: String(frontendPort),
    SCORESHEET_BACKEND_PORT: String(backendPort),
  };
  const frontend = launch(process.execPath, [viteCli, '--host', '127.0.0.1', '--strictPort'], {
    cwd: frontendRoot,
    env: serviceEnvironment,
  });
  await Promise.all([
    waitFor(`http://127.0.0.1:${backendPort}/api/v1/health`, backend),
    waitFor(`http://127.0.0.1:${frontendPort}`, frontend),
  ]);

  const tests = launch(process.execPath, [playwrightCli, 'test', ...process.argv.slice(2)], {
    cwd: frontendRoot,
    env: {
      ...serviceEnvironment,
      SCORESHEET_E2E_BASE_URL: `http://127.0.0.1:${frontendPort}`,
      SCORESHEET_E2E_RESULT_PATH: resultPath,
      SCORESHEET_E2E_OUTPUT_DIR: artifactPath,
      RUN_PRIVATE_LIVE_UI: '0',
    },
  });
  const testExitCode = await waitForExit(tests);
  if (testExitCode !== 0) throw new Error(`Playwright 退出码为 ${testExitCode}。`);
  verifyPlaywrightReport();
  exitCode = 0;
} catch (error) {
  console.error(error instanceof Error ? error.message : error);
} finally {
  console.log('[e2e-runner] Stopping local test services.');
  await cleanup();
  await cleanupData();
  console.log('[e2e-runner] Local test services stopped.');
}

process.exit(exitCode);
