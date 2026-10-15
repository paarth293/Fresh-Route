import { defineConfig } from '@playwright/test';
import path from 'node:path';
const python = path.resolve(process.platform === 'win32' ? '../.venv/Scripts/python.exe' : '../.venv/bin/python');
export default defineConfig({
  testDir: './tests/video', timeout: 300_000, workers: 1,
  outputDir: '../work/video/capture-results',
  use: { baseURL: 'http://127.0.0.1:8000', viewport: { width: 1440, height: 1000 }, headless: true,
    video: { mode: 'on', size: { width: 1440, height: 1000 } } },
  webServer: {
    command: `"${python}" -m uvicorn app.main:app --app-dir ../backend --host 127.0.0.1 --port 8000`,
    url: 'http://127.0.0.1:8000/health', timeout: 30_000, reuseExistingServer: false,
    env: { FRESHROUTE_DB_PATH: path.resolve('../work/video/video-demo.sqlite3'), FRESHROUTE_OPERATOR_PASSWORD: '' },
  },
});
