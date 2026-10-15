import { defineConfig } from '@playwright/test';
import path from 'node:path';
const python = path.resolve(process.platform === 'win32' ? '../.venv/Scripts/python.exe' : '../.venv/bin/python');
export default defineConfig({
  testDir: './tests/e2e', testMatch: 'workflows.spec.ts', timeout: 180_000, workers: 1,
  use: { baseURL: 'http://127.0.0.1:8000', headless: true, trace: 'retain-on-failure' },
  webServer: {
    command: `"${python}" -m uvicorn app.main:app --app-dir ../backend --host 127.0.0.1 --port 8000`,
    url: 'http://127.0.0.1:8000/health', timeout: 30_000, reuseExistingServer: false,
    env: { FRESHROUTE_DB_PATH: path.resolve('../work/browser-audit.sqlite3'), FRESHROUTE_OPERATOR_PASSWORD: '' },
  },
});
