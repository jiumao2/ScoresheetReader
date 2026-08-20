import { spawn, spawnSync } from 'node:child_process';
import { existsSync, mkdirSync, mkdtempSync, readFileSync, rmSync } from 'node:fs';
import { homedir, tmpdir } from 'node:os';
import { fileURLToPath } from 'node:url';
import path from 'node:path';

const repositoryRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const frontendRoot = path.join(repositoryRoot, 'frontend');
const backendRoot = path.join(repositoryRoot, 'backend');
const e2eDataDir = mkdtempSync(path.join(tmpdir(), 'scoresheet-reader-e2e-'));
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

const python = resolvePython();
const backendPort = Number(process.env.SCORESHEET_E2E_BACKEND_PORT ?? 18000);
const frontendPort = Number(process.env.SCORESHEET_E2E_FRONTEND_PORT ?? 15173);
const viteCli = path.join(repositoryRoot, 'node_modules', 'vite', 'bin', 'vite.js');
const playwrightCli = path.join(repositoryRoot, 'node_modules', '@playwright', 'test', 'cli.js');
const resultPath = path.join(repositoryRoot, 'output', 'playwright', 'results.json');
const suppliedTemplate = path.join(repositoryRoot, 'scoresheet_template.pdf');
const fallbackTemplate = path.join(repositoryRoot, 'tmp', 'e2e-template.pdf');
const children = [];

function resolveTemplate() {
  if (existsSync(suppliedTemplate)) return suppliedTemplate;

  mkdirSync(path.dirname(fallbackTemplate), { recursive: true });
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

function stop(child) {
  if (!child.pid || child.exitCode !== null) return;
  if (process.platform === 'win32') {
    spawnSync('taskkill', ['/pid', String(child.pid), '/T', '/F'], { stdio: 'ignore' });
  } else {
    child.kill('SIGTERM');
  }
}

function cleanup() {
  [...children].reverse().forEach(stop);
}

function cleanupData() {
  const resolvedDataDir = path.resolve(e2eDataDir);
  const resolvedTempDir = path.resolve(tmpdir());
  if (
    resolvedDataDir.startsWith(`${resolvedTempDir}${path.sep}`)
    && path.basename(resolvedDataDir).startsWith('scoresheet-reader-e2e-')
  ) {
    try {
      rmSync(resolvedDataDir, { recursive: true, force: true, maxRetries: 3, retryDelay: 100 });
    } catch (error) {
      if (!['EPERM', 'EBUSY', 'ENOTEMPTY'].includes(error?.code)) throw error;
      console.warn(`[e2e-runner] Temporary data is still locked and was retained: ${resolvedDataDir}`);
    }
  }
}

function resultExitCode() {
  if (!existsSync(resultPath)) return null;
  try {
    const report = JSON.parse(readFileSync(resultPath, 'utf8'));
    return report.stats?.unexpected === 0 ? 0 : 1;
  } catch {
    return null;
  }
}

function waitForExit(child) {
  if (child.exitCode !== null) return Promise.resolve(child.exitCode);
  return new Promise((resolve, reject) => {
    let settled = false;
    const finish = (code) => {
      if (settled) return;
      settled = true;
      clearInterval(resultPoll);
      resolve(code ?? child.exitCode ?? 1);
    };
    const resultPoll = setInterval(() => {
      const code = resultExitCode();
      if (code !== null) {
        console.log(`[e2e-runner] Playwright report complete (exit ${code}).`);
        finish(code);
      }
    }, 200);
    child.once('error', reject);
    child.once('exit', finish);
    child.once('close', finish);
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

process.once('SIGINT', () => {
  cleanup();
  process.exit(130);
});
process.once('SIGTERM', () => {
  cleanup();
  process.exit(143);
});

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

  rmSync(resultPath, { force: true });
  const tests = launch(process.execPath, [playwrightCli, 'test', ...process.argv.slice(2)], {
    cwd: frontendRoot,
    env: {
      ...serviceEnvironment,
      SCORESHEET_E2E_BASE_URL: `http://127.0.0.1:${frontendPort}`,
    },
  });
  exitCode = await waitForExit(tests);
} catch (error) {
  console.error(error instanceof Error ? error.message : error);
} finally {
  console.log('[e2e-runner] Stopping local test services.');
  cleanup();
  await new Promise((resolve) => setTimeout(resolve, 250));
  cleanupData();
  console.log('[e2e-runner] Local test services stopped.');
}

process.exit(exitCode);
