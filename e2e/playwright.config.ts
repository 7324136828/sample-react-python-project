import { defineConfig, devices } from '@playwright/test';
import { existsSync } from 'node:fs';
import { dirname, join, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

const isMacOS = process.platform === 'darwin';
const isWindows = process.platform === 'win32';
const e2eRoot = dirname(fileURLToPath(import.meta.url));
const projectRoot = resolve(e2eRoot, '..');
const frontendRoot = join(projectRoot, 'frontend');
const environmentRoot = process.env.VIRTUAL_ENV || process.env.CONDA_PREFIX;
const environmentPython = environmentRoot
  ? join(environmentRoot, isWindows ? 'Scripts/python.exe' : 'bin/python')
  : undefined;
const localPython = join(
  projectRoot,
  '.venv',
  isWindows ? 'Scripts/python.exe' : 'bin/python',
);
const python = environmentPython && existsSync(environmentPython)
  ? environmentPython
  : existsSync(localPython)
    ? localPython
    : isWindows
      ? 'python'
      : 'python3';
const backendUrl = 'http://127.0.0.1:8001';
const frontendUrl = 'http://127.0.0.1:5174';
const testDatabase = join(e2eRoot, 'test-results', 'counter.db');
const environment = Object.fromEntries(
  Object.entries(process.env).filter(
    (entry): entry is [string, string] => entry[1] !== undefined,
  ),
);

export default defineConfig({
  testDir: './tests',
  fullyParallel: false,
  forbidOnly: Boolean(process.env.CI),
  retries: process.env.CI ? 2 : 0,
  reporter: 'html',
  use: {
    baseURL: frontendUrl,
    trace: 'on-first-retry',
  },
  projects: isMacOS
    ? [
        {
          name: 'safari-webkit',
          use: { ...devices['Desktop Safari'] },
        },
      ]
    : [
        {
          name: 'edge',
          use: {
            ...devices['Desktop Edge'],
            channel: 'msedge',
          },
        },
      ],
  webServer: [
    {
      command: `${JSON.stringify(python)} -m uvicorn backend.app.main:app --host 127.0.0.1 --port 8001`,
      cwd: projectRoot,
      env: {
        ...environment,
        COUNTER_DB_PATH: testDatabase,
        CORS_ORIGINS: frontendUrl,
      },
      url: `${backendUrl}/api/health`,
      reuseExistingServer: false,
    },
    {
      command: `${isWindows ? 'npm.cmd' : 'npm'} run dev -- --host 127.0.0.1 --port 5174 --strictPort`,
      cwd: frontendRoot,
      env: {
        ...environment,
        VITE_BACKEND_URL: backendUrl,
      },
      url: frontendUrl,
      reuseExistingServer: false,
    },
  ],
});
